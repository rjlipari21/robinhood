"""Sample data generator.

The `robinhood-trading` MCP connector isn't authorized in this environment
(see robinhood_source.py), so the dashboard is seeded with a deterministic,
realistic-looking sample account and AI agent decision history. This lets
the GUI be fully exercised today; swap in `robinhood_source.refresh_from_mcp`
once the connector is authorized.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone

RNG_SEED = 7
DAYS_OF_HISTORY = 120

WATCHLIST = [
    ("AAPL", "Apple Inc.", 190.0),
    ("MSFT", "Microsoft Corp.", 415.0),
    ("NVDA", "NVIDIA Corp.", 118.0),
    ("VOO", "Vanguard S&P 500 ETF", 520.0),
    ("AMZN", "Amazon.com Inc.", 185.0),
    ("GOOGL", "Alphabet Inc.", 165.0),
    ("TSLA", "Tesla Inc.", 240.0),
    ("SCHD", "Schwab US Dividend Equity ETF", 27.5),
]

ACTIONS = ["buy", "sell", "hold", "rebalance"]
ACTION_WEIGHTS = [0.28, 0.18, 0.44, 0.10]

RATIONALE_TEMPLATES = {
    "buy": [
        "{symbol} broke above its 20-day moving average on rising volume; "
        "momentum and relative strength both improved this week, so the agent "
        "added to the position within the account's target allocation band.",
        "Earnings for {symbol} beat consensus on revenue and margins; the agent "
        "raised its conviction score and increased the position size, keeping "
        "sector exposure under the 30% cap.",
        "{symbol} pulled back to a key support level identified in the agent's "
        "mean-reversion model with RSI oversold; a starter position was opened.",
    ],
    "sell": [
        "{symbol} exceeded its trailing stop threshold after a broad market "
        "drawdown; the agent trimmed the position to cap portfolio drawdown risk.",
        "Valuation on {symbol} moved two standard deviations above its 5-year "
        "average P/E with decelerating growth; the agent took partial profits.",
        "Sector concentration for {symbol}'s industry exceeded the risk policy "
        "limit after recent gains; the agent rebalanced by reducing the position.",
    ],
    "hold": [
        "No signal change for {symbol}; trend, momentum, and valuation scores "
        "remain within normal ranges, so the agent maintained the current position.",
        "{symbol} is inside its expected volatility band with no material news "
        "since the last run; the agent held the position unchanged.",
        "Model confidence for {symbol} stayed below the {threshold:.0%} action "
        "threshold this cycle, so the agent took no action.",
    ],
    "rebalance": [
        "Quarterly drift check: {symbol} allocation moved outside its target "
        "band relative to the model portfolio; the agent rebalanced back to target.",
        "Cash balance exceeded the account's idle-cash policy; the agent "
        "deployed excess cash into {symbol} per the target allocation.",
    ],
}

TRIGGERS = ["scheduled_daily_run", "market_open_signal", "volatility_alert", "manual_review"]

STATUSES_RUN = ["completed", "completed", "completed", "completed", "completed_with_warnings"]

ORDER_STATUSES = {
    "buy": ["filled", "filled", "filled", "partially_filled"],
    "sell": ["filled", "filled", "filled", "partially_filled"],
    "hold": ["no_order"],
    "rebalance": ["filled", "filled"],
}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


INITIAL_DEPOSIT = 22_000.00
CASH_RESERVE = 500.00  # the agent won't spend below this


def seed(conn: sqlite3.Connection) -> None:
    rng = random.Random(RNG_SEED)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    # --- account ---
    cash = 6_500.00
    holdings = {
        "AAPL": 22, "MSFT": 9, "NVDA": 14, "VOO": 11,
        "AMZN": 12, "GOOGL": 10, "TSLA": 6, "SCHD": 40,
    }
    avg_cost = {sym: base * rng.uniform(0.82, 0.98) for sym, _, base in WATCHLIST}

    equity_series: list[tuple[str, float]] = []
    start = now - timedelta(days=DAYS_OF_HISTORY)
    day_prices = {sym: base * rng.uniform(0.75, 0.95) for sym, _, base in WATCHLIST}

    run_rows: list[tuple] = []
    decision_rows: list[tuple] = []
    run_id = 1

    for day_offset in range(DAYS_OF_HISTORY + 1):
        day = start + timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue  # markets closed

        # random-walk each symbol's price toward its "current" base price
        for sym, _, base in WATCHLIST:
            drift = (base - day_prices[sym]) * 0.01
            noise = day_prices[sym] * rng.uniform(-0.018, 0.018)
            day_prices[sym] = max(1.0, day_prices[sym] + drift + noise)

        day_equity = cash + sum(holdings[sym] * day_prices[sym] for sym in holdings)
        equity_series.append((day.strftime("%Y-%m-%d"), round(day_equity, 2)))

        # one agent run per trading day, at 9:35 AM UTC-ish
        run_at = day.replace(hour=13, minute=35, second=0)
        trigger = rng.choice(TRIGGERS)
        n_decisions = rng.choice([1, 1, 2, 2, 3])
        symbols_today = rng.sample(list(holdings.keys()), k=min(n_decisions, len(holdings)))

        run_decisions_summary = []
        for sym in symbols_today:
            action = rng.choices(ACTIONS, weights=ACTION_WEIGHTS, k=1)[0]
            price = round(day_prices[sym], 2)
            confidence = round(rng.uniform(0.52, 0.97), 2)

            qty = None
            realized_pl = None
            if action == "buy":
                desired = rng.choice([1, 2, 3, 5])
                affordable = int(max(0, cash - CASH_RESERVE) // price)
                qty = min(desired, affordable)
                if qty <= 0:
                    action = "hold"
                    qty = None
                else:
                    holdings[sym] += qty
                    cash -= qty * price
            elif action == "sell":
                max_qty = max(1, int(holdings[sym] * 0.25))
                qty = rng.randint(1, max_qty) if holdings[sym] > 0 else 0
                if qty:
                    holdings[sym] -= qty
                    cash += qty * price
                    realized_pl = round(qty * (price - avg_cost[sym]), 2)
                else:
                    action = "hold"
                    qty = None
            elif action == "rebalance":
                desired = rng.choice([-3, -2, 2, 3])
                if desired > 0:
                    affordable = int(max(0, cash - CASH_RESERVE) // price)
                    qty = min(desired, affordable)
                    if qty <= 0:
                        action = "hold"
                        qty = None
                    else:
                        holdings[sym] += qty
                        cash -= qty * price
                else:
                    sell_qty = min(abs(desired), holdings[sym])
                    if sell_qty <= 0:
                        action = "hold"
                        qty = None
                    else:
                        holdings[sym] -= sell_qty
                        cash += sell_qty * price
                        qty = -sell_qty

            template = rng.choice(RATIONALE_TEMPLATES[action])
            rationale = template.format(symbol=sym, threshold=0.6)
            order_status = rng.choice(ORDER_STATUSES[action])

            decision_rows.append((
                run_id, sym, action, qty, price if action != "hold" else None,
                confidence, rationale, order_status, realized_pl,
            ))
            run_decisions_summary.append(f"{action.upper()} {sym}")

        status = rng.choice(STATUSES_RUN)
        duration_ms = rng.randint(1800, 9800)
        summary = (
            f"Evaluated {len(symbols_today)} symbol(s): "
            + ", ".join(run_decisions_summary) + "."
        )
        run_rows.append((
            run_id, _iso(run_at), "portfolio-agent-v2", trigger, status,
            summary, duration_ms,
        ))
        run_id += 1

    equity = round(cash + sum(holdings[sym] * day_prices[sym] for sym in holdings), 2)
    prev_close_equity = equity_series[-2][1] if len(equity_series) > 1 else equity
    day_change = round(equity - prev_close_equity, 2)
    day_change_pct = round((day_change / prev_close_equity) * 100, 2) if prev_close_equity else 0.0

    total_return = round(equity - INITIAL_DEPOSIT, 2)
    total_return_pct = round((total_return / INITIAL_DEPOSIT) * 100, 2)

    conn.execute(
        """INSERT INTO accounts
           (id, account_number, account_type, buying_power, cash, equity,
            day_change, day_change_pct, total_return, total_return_pct, updated_at)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "•••• 4471", "AI-Managed Individual Investing", round(cash, 2), round(cash, 2),
            equity, day_change, day_change_pct, total_return, total_return_pct, _iso(now),
        ),
    )

    for sym, name, _ in WATCHLIST:
        qty = holdings[sym]
        if qty <= 0:
            continue
        price = round(day_prices[sym], 2)
        conn.execute(
            """INSERT INTO positions
               (account_id, symbol, name, quantity, avg_cost, current_price, market_value, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?)""",
            (sym, name, qty, round(avg_cost[sym], 2), price, round(qty * price, 2), _iso(now)),
        )

    conn.executemany(
        "INSERT INTO equity_history (account_id, date, equity) VALUES (1, ?, ?)",
        equity_series,
    )

    conn.executemany(
        """INSERT INTO agent_runs
           (id, run_at, agent_name, trigger, status, summary, duration_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        run_rows,
    )

    conn.executemany(
        """INSERT INTO agent_decisions
           (run_id, symbol, action, quantity, price, confidence, rationale, order_status, realized_pl)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        decision_rows,
    )

    conn.commit()
