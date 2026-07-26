Execute one scheduled trading run now. Follow the mandate in CLAUDE.md exactly.

1. If `state/HALT` exists, stop immediately.
2. Read the last few entries of `state/journal.md` (create it if missing) and
   `state/ledger.json` to re-establish context: open positions, today's spend,
   pending orders.
3. Verify reality against Robinhood: `get_equity_positions` and
   `get_equity_orders` for account 797887684. Reconcile with the journal —
   trust Robinhood over the journal if they disagree.
4. Manage existing positions first: apply the stop-loss / take-profit /
   momentum-exit rules from CLAUDE.md. Place any required sell orders.
5. Scan the watchlist (`config/watchlist.txt`) for new entries: quotes first
   to shortlist movers, then historicals/technicals on the shortlist to
   confirm momentum per the mandate. Respect the position count, per-trade,
   and daily caps.
6. For each order: `review_equity_order` first, inspect the estimate and any
   alerts, then `place_equity_order` with a fresh UUID ref_id. Afterwards
   confirm via `get_equity_orders` that it was accepted.
7. Append a complete journal entry to `state/journal.md` (date/time ET,
   positions, orders placed with ref_ids and fill status, observations,
   watch items for next run). Do this even if you placed no orders.

Be decisive but conservative: when a setup is marginal, skip it.
