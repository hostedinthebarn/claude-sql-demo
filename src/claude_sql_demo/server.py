"""An MCP server that lets Claude answer questions from a business's SQL database.

The demo runs against SQLite so it works with one command. The same shape
applies to SQL Server, Postgres or MySQL: swap the connection and keep the
guard rails.

Guard rails:
- Questions go through a read-only connection. The file is opened read-only,
  an authorizer refuses anything but reads, and every query has a row cap and
  a time limit.
- The one tool that changes data shows a preview first and changes nothing
  until it's called again with confirm=True. Every change is written to an
  audit log.
"""

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from claude_sql_demo.seed import build

DB_PATH = Path(os.environ.get("BLUEBONNET_DB", Path(__file__).resolve().parents[2] / "bluebonnet.db"))
MAX_ROWS = 200
QUERY_SECONDS = 5.0
STATUSES = ("pending", "shipped", "delivered", "cancelled")
FINAL = ("delivered", "cancelled")

mcp = MCPServer(
    name="bluebonnet-sales",
    instructions=(
        "Sales data for Bluebonnet Outfitters, an outdoor-gear shop. Money is stored in cents; "
        "the summary tools return dollars. Prefer the summary tools; use run_query for anything "
        "they don't cover, after checking the schema with list_tables and describe_table. "
        "update_order_status changes data: call it once without confirm to show the user the "
        "preview, and only call it with confirm=True after the user agrees."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

# Everything a plain SELECT needs, and nothing that writes, attaches or changes settings.
_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


def _ensure_db() -> None:
    if not DB_PATH.exists():
        build(DB_PATH)


@contextmanager
def _reader(guarded: bool = True) -> Iterator[sqlite3.Connection]:
    """A read-only connection. guarded=False skips the authorizer, for our own fixed schema queries."""
    _ensure_db()
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    if guarded:
        db.set_authorizer(_authorizer)
    deadline = time.monotonic() + QUERY_SECONDS
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        yield db
    finally:
        db.close()


def _writer() -> sqlite3.Connection:
    _ensure_db()
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _rows(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


@mcp.tool(annotations=READ_ONLY)
def list_tables() -> list[dict]:
    """List the tables in the database with their row counts."""
    with _reader(guarded=False) as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name")]
        return [{"table": n, "rows": db.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0]} for n in names]


@mcp.tool(annotations=READ_ONLY)
def describe_table(table: str) -> list[dict]:
    """Show a table's columns, types and keys."""
    with _reader(guarded=False) as db:
        known = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
        if table not in known:
            raise ToolError(f"No table called {table!r}. Tables: {', '.join(sorted(known))}")
        cols = db.execute("SELECT name, type, \"notnull\", pk FROM pragma_table_info(?)", (table,))
        return [{"column": c["name"], "type": c["type"], "required": bool(c["notnull"]), "key": bool(c["pk"])} for c in cols]


@mcp.tool(annotations=READ_ONLY)
def run_query(sql: str) -> dict:
    """Run one read-only SQL SELECT and return up to 200 rows.

    Anything that would change data, attach another database or change settings
    is refused. Queries are stopped after 5 seconds.
    """
    with _reader() as db:
        try:
            cursor = db.execute(sql)
        except sqlite3.DatabaseError as e:
            if "not authorized" in str(e) or "readonly" in str(e):
                raise ToolError("Refused: this connection is read-only, so only a single SELECT is allowed.") from None
            if "interrupted" in str(e):
                raise ToolError(f"Stopped: the query ran longer than {QUERY_SECONDS:g} seconds.") from None
            raise ToolError(f"Query failed: {e}") from None
        if cursor.description is None:
            raise ToolError("Only SELECT queries that return rows are allowed.")
        rows = cursor.fetchmany(MAX_ROWS + 1)
        return {
            "columns": [d[0] for d in cursor.description],
            "rows": [list(r) for r in rows[:MAX_ROWS]],
            "truncated": len(rows) > MAX_ROWS,
        }


@mcp.tool(annotations=READ_ONLY)
def sales_summary(start: str, end: str) -> dict:
    """Revenue, order count and average order value between two dates (YYYY-MM-DD, inclusive).

    Cancelled orders are left out.
    """
    with _reader() as db:
        row = db.execute(
            """
            SELECT count(DISTINCT o.id) AS orders,
                   coalesce(sum(i.quantity * i.unit_price_cents), 0) AS cents
            FROM orders o JOIN order_items i ON i.order_id = o.id
            WHERE o.ordered_on BETWEEN ? AND ? AND o.status != 'cancelled'
            """,
            (start, end),
        ).fetchone()
        orders, cents = row["orders"], row["cents"]
        return {
            "start": start,
            "end": end,
            "orders": orders,
            "revenue": round(cents / 100, 2),
            "average_order": round(cents / 100 / orders, 2) if orders else 0.0,
        }


@mcp.tool(annotations=READ_ONLY)
def top_customers(limit: int = 10, start: str = "0000-01-01", end: str = "9999-12-31") -> list[dict]:
    """The customers who spent the most, optionally between two dates (YYYY-MM-DD)."""
    with _reader() as db:
        return _rows(db.execute(
            """
            SELECT c.id, c.name, c.city, c.state,
                   count(DISTINCT o.id) AS orders,
                   round(sum(i.quantity * i.unit_price_cents) / 100.0, 2) AS spent
            FROM customers c
            JOIN orders o ON o.customer_id = c.id AND o.status != 'cancelled'
            JOIN order_items i ON i.order_id = o.id
            WHERE o.ordered_on BETWEEN ? AND ?
            GROUP BY c.id ORDER BY spent DESC, c.id LIMIT ?
            """,
            (start, end, max(1, min(limit, 50))),
        ))


@mcp.tool(annotations=READ_ONLY)
def low_stock(threshold: int = 10) -> list[dict]:
    """Products with stock at or below a threshold, lowest first."""
    with _reader() as db:
        return _rows(db.execute(
            "SELECT sku, name, category, stock FROM products WHERE stock <= ? ORDER BY stock, sku",
            (threshold,),
        ))


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False))
def update_order_status(order_id: int, status: str, confirm: bool = False) -> dict:
    """Change an order's status. Without confirm=True this only previews the change.

    Delivered and cancelled orders are final and can't be changed.
    """
    if status not in STATUSES:
        raise ToolError(f"Status must be one of: {', '.join(STATUSES)}")
    db = _writer()
    try:
        order = db.execute(
            "SELECT o.id, o.status, o.ordered_on, c.name AS customer FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.id = ?",
            (order_id,),
        ).fetchone()
        if order is None:
            raise ToolError(f"No order {order_id}")
        change = {"order_id": order_id, "customer": order["customer"], "ordered_on": order["ordered_on"],
                  "from": order["status"], "to": status}
        if order["status"] == status:
            return {**change, "result": "unchanged: the order already has that status"}
        if order["status"] in FINAL:
            raise ToolError(f"Order {order_id} is {order['status']}, which is final")
        if not confirm:
            return {**change, "result": "preview only: nothing changed. Call again with confirm=True to apply."}
        with db:
            db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
            db.execute(
                "INSERT INTO audit_log (action, detail) VALUES ('update_order_status', ?)",
                (f"order {order_id}: {order['status']} -> {status}",),
            )
        return {**change, "result": "applied"}
    finally:
        db.close()


def main() -> None:
    mcp.run()
