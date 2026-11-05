"""MCP server: the assistant's tools for Claude Desktop, Claude Code or any MCP client.

Run:
    python -m card_analytics.assistant.mcp_server          # stdio transport

Claude Desktop config (claude_desktop_config.json), with your own paths:
    {"mcpServers": {"card-risk-analytics": {
        "command": "C:/Users/<you>/Documents/barclays-projects/card-risk-analytics/.venv/Scripts/python.exe",
        "args": ["-m", "card_analytics.assistant.mcp_server"],
        "env": {"PYTHONPATH": "src", "CARDRISK_DB": "sqlite:///cardrisk.db"},
        "cwd": "C:/Users/<you>/Documents/barclays-projects/card-risk-analytics"}}}

All three tools are read-only, so no confirmation step is needed. Tools that change
anything (e.g. card-banking-platform transfers) must ask the user to confirm first.
"""
from __future__ import annotations

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from card_analytics.assistant import tools

server = _Server(
    "card-risk-analytics",
    instructions="Read-only tools over the Card Risk Analytics docs and PII-masked reporting layer. "
    "Cite doc results by their `source`. Use list_tables before run_sql.",
)


@server.tool()
def search_docs(query: str, k: int = 4) -> list[dict]:
    """Search the platform docs (data dictionary, lineage, reconciliation, segmentation, model card, findings memo, README).
    Returns sections with a `source` to cite."""
    return tools.search_docs(query, min(k, 8))


@server.tool()
def list_tables() -> dict:
    """List the tables run_sql can read, with every column and its meaning."""
    return tools.list_tables()


@server.tool()
def run_sql(sql: str) -> dict:
    """Run one read-only SQLite SELECT against the PII-masked reporting layer (max 200 rows). Amounts are CZK."""
    return tools.run_sql(sql)


if __name__ == "__main__":
    server.run()
