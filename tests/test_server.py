import asyncio
import sqlite3

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from claude_sql_demo import server
from claude_sql_demo.seed import build


@pytest.fixture(autouse=True)
def demo_db(tmp_path, monkeypatch):
    path = build(tmp_path / "demo.db")
    monkeypatch.setattr(server, "DB_PATH", path)
    return path


def raw(path, sql, *args):
    with sqlite3.connect(path) as db:
        return db.execute(sql, args).fetchall()


def test_seed_is_repeatable(tmp_path):
    a, b = build(tmp_path / "a.db"), build(tmp_path / "b.db")
    query = "SELECT count(*), sum(customer_id), group_concat(status) FROM orders"
    assert raw(a, query) == raw(b, query)


def test_builds_the_database_on_first_use(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "fresh.db")
    assert {t["table"] for t in server.list_tables()} >= {"customers", "orders"}


def test_list_and_describe_tables():
    tables = {t["table"]: t["rows"] for t in server.list_tables()}
    assert set(tables) == {"audit_log", "customers", "order_items", "orders", "products"}
    assert tables["customers"] == 60 and tables["products"] == 16
    columns = server.describe_table("orders")
    assert [c["column"] for c in columns] == ["id", "customer_id", "ordered_on", "status"]
    assert columns[0]["key"] is True
    with pytest.raises(ToolError, match="No table called 'staff'"):
        server.describe_table("staff")


def test_run_query_reads():
    result = server.run_query("SELECT category, count(*) AS n FROM products GROUP BY category ORDER BY category")
    assert result["columns"] == ["category", "n"]
    assert result["rows"][0] == ["Accessories", 3]
    assert result["truncated"] is False


def test_run_query_caps_rows():
    result = server.run_query("SELECT * FROM order_items")
    assert len(result["rows"]) == server.MAX_ROWS
    assert result["truncated"] is True


@pytest.mark.parametrize("sql", [
    "INSERT INTO customers (name, email, city, state, joined_on) VALUES ('x', 'x@x', 'x', 'TX', '2026-01-01')",
    "UPDATE products SET stock = 0",
    "DELETE FROM orders",
    "DROP TABLE orders",
    "CREATE TABLE t (x)",
    "ATTACH DATABASE ':memory:' AS other",
    "PRAGMA user_version = 7",
    "SELECT 1; DELETE FROM orders",
    "WITH gone AS (DELETE FROM orders RETURNING id) SELECT * FROM gone",
])
def test_run_query_refuses_anything_but_reading(sql, demo_db):
    before = raw(demo_db, "SELECT count(*), sum(stock) FROM orders, products")
    with pytest.raises(ToolError):
        server.run_query(sql)
    assert raw(demo_db, "SELECT count(*), sum(stock) FROM orders, products") == before
    assert raw(demo_db, "PRAGMA user_version") == [(0,)]


def test_run_query_stops_runaway_queries(monkeypatch):
    monkeypatch.setattr(server, "QUERY_SECONDS", 0.2)
    with pytest.raises(ToolError, match="Stopped: the query ran longer"):
        server.run_query("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n")


def test_sales_summary_matches_the_raw_figures(demo_db):
    start, end = "2026-01-01", "2026-03-31"
    orders, cents = raw(demo_db, """
        SELECT count(DISTINCT o.id), sum(i.quantity * i.unit_price_cents) FROM orders o
        JOIN order_items i ON i.order_id = o.id
        WHERE o.ordered_on BETWEEN ? AND ? AND o.status != 'cancelled'""", start, end)[0]
    summary = server.sales_summary(start, end)
    assert summary["orders"] == orders > 0
    assert summary["revenue"] == round(cents / 100, 2)
    assert summary["average_order"] == round(cents / 100 / orders, 2)
    assert server.sales_summary("2020-01-01", "2020-12-31") == {
        "start": "2020-01-01", "end": "2020-12-31", "orders": 0, "revenue": 0.0, "average_order": 0.0}


def test_top_customers_are_ordered_and_limited():
    top = server.top_customers(limit=5)
    assert len(top) == 5
    assert [c["spent"] for c in top] == sorted((c["spent"] for c in top), reverse=True)
    assert len(server.top_customers(limit=500)) == 50
    assert len(server.top_customers(limit=0)) == 1


def test_low_stock():
    rows = server.low_stock(threshold=20)
    assert rows and all(r["stock"] <= 20 for r in rows)
    assert [r["stock"] for r in rows] == sorted(r["stock"] for r in rows)


def pending_order(path):
    return raw(path, "SELECT id FROM orders WHERE status = 'pending' ORDER BY id LIMIT 1")[0][0]


def test_update_previews_then_applies_and_audits(demo_db):
    order = pending_order(demo_db)
    preview = server.update_order_status(order, "shipped")
    assert preview["from"] == "pending" and preview["to"] == "shipped"
    assert preview["result"].startswith("preview only")
    assert raw(demo_db, "SELECT status FROM orders WHERE id = ?", order) == [("pending",)]
    assert raw(demo_db, "SELECT count(*) FROM audit_log") == [(0,)]

    assert server.update_order_status(order, "shipped", confirm=True)["result"] == "applied"
    assert raw(demo_db, "SELECT status FROM orders WHERE id = ?", order) == [("shipped",)]
    assert raw(demo_db, "SELECT action, detail FROM audit_log") == [
        ("update_order_status", f"order {order}: pending -> shipped")]

    assert server.update_order_status(order, "shipped", confirm=True)["result"].startswith("unchanged")


def test_update_refuses_bad_input(demo_db):
    delivered = raw(demo_db, "SELECT id FROM orders WHERE status = 'delivered' LIMIT 1")[0][0]
    with pytest.raises(ToolError, match="final"):
        server.update_order_status(delivered, "pending", confirm=True)
    with pytest.raises(ToolError, match="Status must be one of"):
        server.update_order_status(pending_order(demo_db), "lost", confirm=True)
    with pytest.raises(ToolError, match="No order 999999"):
        server.update_order_status(999999, "shipped", confirm=True)


def test_tools_are_published_with_the_right_hints():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert set(tools) == {"list_tables", "describe_table", "run_query", "sales_summary",
                          "top_customers", "low_stock", "update_order_status"}
    assert all(tools[n].annotations.read_only_hint for n in tools if n != "update_order_status")
    assert tools["update_order_status"].annotations.read_only_hint is False
