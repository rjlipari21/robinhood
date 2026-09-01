"""SQLite schema and connection helpers for the Robinhood AI dashboard."""

import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "dashboard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    account_number TEXT NOT NULL,
    account_type TEXT NOT NULL,
    buying_power REAL NOT NULL,
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    day_change REAL NOT NULL,
    day_change_pct REAL NOT NULL,
    total_return REAL NOT NULL,
    total_return_pct REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    current_price REAL NOT NULL,
    market_value REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_history (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    date TEXT NOT NULL,
    equity REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY,
    run_at TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    duration_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES agent_runs(id),
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity REAL,
    price REAL,
    confidence REAL NOT NULL,
    rationale TEXT NOT NULL,
    order_status TEXT NOT NULL,
    realized_pl REAL
);

CREATE INDEX IF NOT EXISTS idx_decisions_run ON agent_decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON agent_decisions(symbol);
CREATE INDEX IF NOT EXISTS idx_runs_at ON agent_runs(run_at);
"""


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> sqlite3.Connection:
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def is_empty(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()
    return row["n"] == 0
