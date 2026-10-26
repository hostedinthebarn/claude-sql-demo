# Claude SQL demo

An MCP server that lets Claude answer questions from a business's SQL database, safely.

It runs against **Bluebonnet Outfitters**, a made-up outdoor-gear shop: 60 customers, 16 products and a year of orders in a local SQLite file. Ask Claude things like:

- "How did Q1 compare with Q4 on revenue and average order value?"
- "Who are our top 5 customers this year, and where are they?"
- "What's about to run out of stock?"
- "Which products sell together most often?"
- "Mark order 510 as shipped."

The first four are read-only. The last one changes data, so Claude shows you a preview first and only applies it when you say yes.

## The tools

| Tool | What it does |
|---|---|
| `list_tables` | Tables and their row counts |
| `describe_table` | A table's columns, types and keys |
| `run_query` | One read-only `SELECT`, up to 200 rows |
| `sales_summary` | Revenue, orders and average order value between two dates |
| `top_customers` | The biggest spenders, optionally in a date range |
| `low_stock` | Products at or below a stock threshold |
| `update_order_status` | Changes an order's status: preview first, applied only with `confirm=True`, logged to `audit_log` |

The summary tools cover the common questions with tested SQL. `run_query` handles everything else.

## Guard rails

Giving an AI a database connection is only sensible with limits. This server enforces them in code, not in the prompt:

- **Read-only by construction.** Queries run on a connection that opens the file read-only, and an SQLite authorizer refuses anything that isn't a read: no `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ATTACH` or `PRAGMA` writes, and no data-changing statements hidden inside a `WITH`.
- **One statement at a time.** `SELECT 1; DELETE FROM orders` is refused.
- **Bounded.** At most 200 rows come back, and a query is stopped after 5 seconds.
- **Writes need a yes.** The only tool that changes data previews first, refuses changes to final orders, and writes an audit record.
- **Tool hints.** Every tool tells Claude whether it's read-only, so the app can ask before using the one that isn't.

The tests try each of these and check the database is unchanged afterwards.

## Run it with Claude Desktop

You need [uv](https://docs.astral.sh/uv/) installed.

```sh
git clone https://github.com/hostedinthebarn/claude-sql-demo.git
cd claude-sql-demo
uv run pytest            # optional: 21 tests
```

Then add the server to Claude Desktop's config (Settings → Developer → Edit Config), using the full path to the folder you cloned:

```json
{
  "mcpServers": {
    "bluebonnet-sales": {
      "command": "uv",
      "args": ["run", "--directory", "/full/path/to/claude-sql-demo", "claude-sql-demo"]
    }
  }
}
```

Restart Claude Desktop. The database is created on first use, at `bluebonnet.db` in the project folder. Set `BLUEBONNET_DB` to put it somewhere else. To start again from fresh data, delete the file.

## Using it with a real database

The demo uses SQLite so it runs with one command. For SQL Server, Postgres or MySQL, the shape stays the same:

- connect with a database user that only has `SELECT` rights, so the database enforces read-only as well as the server
- keep the row cap and the time limit (`statement_timeout` in Postgres, a query timeout in SQL Server)
- write tools that do one business action each, with a preview and an audit trail, instead of letting the model write SQL that changes data
- keep credentials in environment variables or the OS keychain, never in the config file you share

## Licence

MIT. See [LICENSE](LICENSE).
