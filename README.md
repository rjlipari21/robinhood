# Robinhood AI Account Dashboard

A local web GUI for a Robinhood AI-managed investing account: current
account/holdings overview plus a searchable, filterable history of every
decision the AI trading agent has made (buy / sell / hold / rebalance),
including the run it came from, its confidence score, and its rationale.

## Running it

```bash
pip install -r requirements.txt
python3 app.py
```

Then open http://localhost:5000.

## What you'll see

- **Overview** — account equity with a performance chart, buying power,
  total return, and current holdings with live gain/loss.
- **Agent Decision History** — every decision from every agent run, with
  filters by symbol, action, and date range, each row showing the model's
  confidence and the rationale it logged for that call.

## Data source

This repo has a `robinhood-trading` MCP server registered in `.mcp.json`
(`https://agent.robinhood.com/mcp/trading`), but it isn't authorized in
this environment yet, so there's no way to pull real account or agent-run
data here. The dashboard currently runs on **seeded sample data**
(`robinhood_dashboard/seed.py`) — a synthetic account, holdings, and ~120
days of agent-run history — so the GUI is fully usable today. A "Sample
data" badge in the top-right of the app marks this.

To wire up real data:

1. Authorize the `robinhood-trading` connector (claude.ai connector
   settings, or `/mcp` in an interactive Claude Code session).
2. From a session that has it enabled, pull account state and agent-run
   history via that MCP server's tools and write it into the same tables
   `seed.py` populates (`accounts`, `positions`, `equity_history`,
   `agent_runs`, `agent_decisions` — see `robinhood_dashboard/db.py` for
   the schema). `robinhood_dashboard/robinhood_source.py` documents the
   intended ingestion entry point (`refresh_from_mcp`).
3. Delete `data/dashboard.db` (or point `db.DB_PATH` elsewhere) so the app
   picks up the real data instead of reseeding sample data.

## Project layout

```
app.py                          Flask entrypoint (creates DB, seeds if empty, serves the app)
robinhood_dashboard/
  db.py                         SQLite schema + connection helpers
  seed.py                       Sample data generator
  robinhood_source.py           Adapter boundary for live MCP data (not yet wired up)
  api.py                        REST API: /api/account, /api/decisions, /api/runs/<id>, /api/meta
templates/index.html            App shell (Overview / Decision History tabs)
static/css/style.css            Styling (light/dark aware)
static/js/app.js                Frontend logic (fetches API, renders tables + chart)
```
