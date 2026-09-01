"""Adapter boundary between this dashboard and the live `robinhood-trading` MCP server.

This repo registers the `robinhood-trading` MCP server in `.mcp.json`
(https://agent.robinhood.com/mcp/trading). Until that connector is
authorized (claude.ai connector settings, or `/mcp` in an interactive
Claude Code session), there is no way to pull real account or agent-run
data, so the dashboard runs on seeded sample data (see seed.py).

Once authorized, populate the database by calling this MCP server's tools
(names TBD by that server) inside a Claude Code session with access to it,
and writing normalized rows into the same tables `seed.py` populates:

    accounts        - one row, current snapshot (equity, buying power, ...)
    positions       - current holdings for that account
    equity_history  - daily equity marks, for the performance chart
    agent_runs      - one row per AI trading-agent invocation
    agent_decisions - one or more decisions (buy/sell/hold/rebalance) per run

`refresh_from_mcp()` below is the intended entry point for that ingestion
script; it is not implemented here because this process has no direct MCP
tool access. Wire it up once the connector is authorized.
"""

from __future__ import annotations

import sqlite3


class RobinhoodMCPUnavailable(RuntimeError):
    """Raised when live data was requested but the MCP connector isn't authorized yet."""


def refresh_from_mcp(conn: sqlite3.Connection) -> None:
    """Pull live account state + agent decision history via the robinhood-trading MCP
    server and upsert it into the local database.

    Not implemented: this process cannot call MCP tools directly. Run the
    ingestion from a Claude Code session that has the `robinhood-trading`
    connector authorized, using this function's docstring as the contract
    for what to write to each table.
    """
    raise RobinhoodMCPUnavailable(
        "The robinhood-trading MCP server is not authorized in this session. "
        "Authorize it via claude.ai connector settings (or `/mcp` in an "
        "interactive Claude Code session), then run the live sync from a "
        "session that has it enabled."
    )
