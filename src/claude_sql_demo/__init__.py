"""Claude SQL demo: an MCP server over a sample business database."""


def main() -> None:
    from claude_sql_demo.server import main as run

    run()
