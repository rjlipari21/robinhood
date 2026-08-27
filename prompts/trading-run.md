Execute one scheduled trading run now. Follow the mandate in CLAUDE.md exactly.

1. If `state/HALT` exists, stop immediately.
2. Read the last few entries of `state/journal.md` (create it if missing) and
   `state/ledger.json` to re-establish context: open positions, today's
   orders, pending orders.
3. Verify reality against Robinhood: `get_equity_positions`, `get_equity_orders`
   and `get_portfolio` for account 797887684, plus `get_accounts` for
   `unsettled_funds`. Reconcile with the journal — trust Robinhood over the
   journal if they disagree.
4. Check the circuit breaker: if total account value is below $850, open no
   new positions this run. Manage exits only and say so in the journal.
5. Manage existing positions FIRST. For each holding, compare against entry:
   - down ≥5% → protective exit, limit sell now
   - up +3–5%, or hourly RSI ≥ 65, or at the upper end of its recent range
     → sell into strength with a limit sell
   There are no stop orders, so an unchecked losing position is unprotected.
6. Cancel any stale working orders that no longer reflect current conditions
   (`get_equity_orders` for open ones, then `cancel_equity_order`).
7. Only then look for entries. There is no watchlist — build a candidate list
   in real time:
   a. `run_scan` on BOTH saved scans
      (`edb15197-727a-48e5-9119-2a77b280f915` and
      `b440c52a-da3a-403d-9d9c-92bb53ac5322`), union the rows, drop
      duplicate tickers.
   b. Rank that union by relative volume (`Volume / Average volume`, both
      columns are in the scan rows) descending, tie-broken by `% Change`
      descending, and **keep only the top 50**. Do this from the scan
      output alone — no per-name calls yet.
   c. Only then spend calls: work down the 50 with historicals/technicals
      against the entry criteria in CLAUDE.md, and stop as soon as you have
      enough conviction to act or have run out of setups worth taking. You
      do not have to analyse all 50.
   Before each buy verify:
   - it is a common stock, not an ETF/ETP/closed-end fund — skip if unsure
   - price ≥ $5 and average volume supports a clean fill at your size
   - `get_equity_tradability` confirms the session you are tagging the order to
   - fewer than 9 open positions, and none already in this ticker
   - the buy keeps ≥10% of account value in cash
   - you are spending settled funds, not unsettled proceeds
   - size so that quantity × limit_price ≤ $500
8. For each order: `review_equity_order` first, inspect the estimate and any
   alerts, then `place_equity_order` with a fresh UUID ref_id. Tag
   `market_hours` to a session the name is actually eligible for:
   `regular_hours`, or `extended_hours` when running pre-/post-market.
   `all_day_hours` is rejected by the guardrail — the 24 Hour Market is not
   traded. Afterwards confirm via `get_equity_orders` that it was
   accepted. A guardrail rejection is final — do not retry or reshape the
   order.
9. Record any NEW fills for the owner's phone alerts. From the
   `get_equity_orders` data you already pulled in step 3, find orders now in
   state `filled` or `partially_filled` that are not already listed in
   `state/fills.jsonl`, and append one JSON object per line for each:
   `{"order_id","symbol","side","quantity","average_price","filled_at"}`
   — plus `"pnl_pct"` on a sell, computed against the entry price from the
   journal, and an optional short `"note"`. Append only; never rewrite or
   reorder existing lines, and never re-add an order_id already present.
   Dedupe and delivery are handled downstream, so a line here is enough.
10. Append a complete journal entry to `state/journal.md` (date/time ET,
   positions with entry prices, orders placed with ref_ids and fill status,
   observations, watch items for next run). Do this even if you placed no
   orders.

Be decisive but conservative: when a setup is marginal, skip it. Most runs
should place zero orders.
