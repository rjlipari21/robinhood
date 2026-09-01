Execute one scheduled trading run now. Follow the mandate in CLAUDE.md exactly.

**Turn budget.** You have a hard ceiling of 60 turns and the run is killed at
it — mid-thought, with no chance to clean up. The journal entry (step 10) is
written last and is the only durable record of what you did, so a run that
spends its turns exploring dies without one. Steps 0, 1 and 7a exist to make
the routine parts cost one call each; use them rather than re-deriving them
with ad-hoc shell. Never write a throwaway script to do something one of the
committed scripts already does.

0. **Load every tool you will need, in ONE `ToolSearch` call**, before you
   start. The Robinhood MCP tools are deferred, so each unplanned lookup costs
   a turn. A typical run needs:
   `select:mcp__robinhood-trading__get_equity_positions,mcp__robinhood-trading__get_equity_orders,mcp__robinhood-trading__get_portfolio,mcp__robinhood-trading__get_accounts,mcp__robinhood-trading__get_equity_quotes,mcp__robinhood-trading__get_equity_historicals,mcp__robinhood-trading__get_equity_technical_indicators,mcp__robinhood-trading__get_equity_tradability,mcp__robinhood-trading__get_equity_price_book,mcp__robinhood-trading__run_scan,mcp__robinhood-trading__get_equity_news,mcp__robinhood-trading__get_earnings_results,mcp__robinhood-trading__review_equity_order,mcp__robinhood-trading__place_equity_order,mcp__robinhood-trading__cancel_equity_order`

   `get_earnings_results` is in that list because loading a schema mid-run
   costs a turn, not because every run should call it — it is gated to
   candidates you intend to buy (step 7d). `get_equity_news` is there purely as
   the fallback for when `scripts/news-brief.py` fails; the script is the
   normal path and costs no schema load at all.

1. Run `./scripts/run-context.sh`. One call, and it gives you all of:
   - the `state/HALT` kill switch — **if it reports PRESENT, stop the run
     immediately and place no orders**
   - current ET date/time
   - the tail of `state/journal.md` (open positions, what last run was
     watching for)
   - the tail of `state/ledger.json` (recently executed orders)
   - the `order_id`s already in `state/fills.jsonl` — never re-add these
   - six fresh UUIDs. **Use these for `ref_id`; do not generate your own.**
     Each one at most once per run.

2. Verify reality against Robinhood: `get_equity_positions`, `get_equity_orders`
   and `get_portfolio` for account 797887684, plus `get_accounts` for
   `unsettled_funds`. Reconcile with the journal — trust Robinhood over the
   journal if they disagree.
3. Check the circuit breaker: if total account value is below $850, open no
   new positions this run. Manage exits only and say so in the journal.
4. Manage existing positions FIRST. For each holding, compare against entry:
   - down ≥5% → protective exit, limit sell now
   - up +3–5%, or hourly RSI ≥ 65, or at the upper end of its recent range
     → sell into strength with a limit sell
   There are no stop orders, so an unchecked losing position is unprotected.
   Batch what you can: `get_equity_quotes` and `get_equity_historicals` both
   take a list of symbols, so price all holdings in one call each rather than
   one call per name. Only `get_equity_technical_indicators` is per-symbol —
   spend it on positions that a quote shows are actually near a threshold, not
   on all of them reflexively.

   **News check on a triggered position only.** When a holding has actually
   crossed a threshold — down ≥5%, or into the profit band — run
   **`python3 scripts/news-brief.py TICKER`** (you may pass several tickers in
   one call) before you place the sell, and ask what kind of move this is:
   - Company-specific bad news (guidance cut, fraud or investigation, failed
     trial, dilutive offering, delisting or halt risk, auditor or CEO exit):
     the repricing is probably permanent. Sell, and do not average down.
   - Broad-market or sector selloff with nothing company-specific: the −5% is
     noise around a name whose thesis is intact. Still sell — the protective
     exit is not discretionary — but say so in the journal, because it informs
     whether the −5% band is too tight.
   - Takeover or merger bid on an up move: the price may be pinned near a deal
     price and further upside capped. Take the profit rather than holding for
     the range high.
   Do NOT news-check holdings that have not triggered. That is the whole
   point of putting this step behind a threshold — on a typical run it costs
   nothing because nothing triggered.
5. Cancel any stale working orders that no longer reflect current conditions
   (`get_equity_orders` for open ones, then `cancel_equity_order`).
6. Only then look for entries. There is no watchlist — build a candidate list
   in real time.
7. Candidate list:
   a. `run_scan` on BOTH saved scans
      (`edb15197-727a-48e5-9119-2a77b280f915` and
      `b440c52a-da3a-403d-9d9c-92bb53ac5322`), then run
      **`python3 scripts/rank-candidates.py`**. It reads both scan payloads
      off disk, unions them, drops duplicate tickers and anything under $5,
      ranks by relative volume descending tie-broken by % Change descending,
      and prints the top 50. That is steps 7a and 7b of the old prompt in one
      call — do not rebuild this pipeline by hand.
      Read its header lines: they report how many rows the scanner truncated,
      and how many eligible names the top-50 cap cut.
   b. Rows marked `FUND?` have fund-like names. The scanner labels closed-end
      funds as `Asset type: STOCK`, so it cannot screen them out and neither
      can the script — that flag is a prompt to check, and CLAUDE.md says skip
      if unsure. REITs are common stocks and are fine. An unflagged row is not
      automatically a common stock either; the flag catches the obvious cases
      only.
   c. Only now spend per-name calls: work down the 50 with
      historicals/technicals against the entry criteria in CLAUDE.md, and stop
      as soon as you have enough conviction to act or have run out of setups
      worth taking. You do not have to analyse all 50 — in practice you should
      look at far fewer.
   d. **News check the finalists.** Once a name has passed technicals and you
      intend to buy it, run **`python3 scripts/news-brief.py TICKER ...`** and
      `get_earnings_results` for it, before `review_equity_order`.

      The script fetches the same Robinhood news feed VM-side and prints
      headlines only — ~165 tokens per ticker instead of the ~1,900 the
      `get_equity_news` tool costs, because it drops the full article bodies
      that carry no decision value. Pass every finalist in ONE call (it accepts
      up to 12) rather than one call per name. Read the output as:
      - `*` marks an article from the last 3 sessions — weight those.
      - `FLAGS:` lists veto keywords found in headlines. **These are hints to
        judge, not verdicts.** A `MERGER` flag may be a story about a
        competitor's deal; a `FRAUD` flag may be a class action already priced
        in a year ago. Read the headline before acting on a flag.
      - `FLAGS: none` means no keyword matched. It is not a clean bill of
        health — the flagger only reads headlines, and news the feed has not
        published yet does not exist to it.

      If the script exits non-zero it prints one line saying why (expired
      token, transport failure). Fall back to the `get_equity_news` MCP tool
      for the one or two names you actually care about — more expensive, but a
      news fetch failing must never block an exit or strand a decided trade.
      Note the fallback in the journal so a persistent breakage is visible.

      **Veto the buy** — do not place it — if you find any of:
      - a pending acquisition, merger, or going-private bid (the chart looks
        like a base because the price is pinned to a deal, not because buyers
        are accumulating)
      - an announced dilutive offering, ATM, or convertible raise
      - fraud allegations, an SEC or DOJ investigation, a restatement, or an
        auditor/CFO/CEO departure
      - delisting, bankruptcy, going-concern, or reverse-split risk
      - a failed clinical trial, lost FDA decision, or lost major contract
      - **earnings inside the next 2 trading days.** This is a swing strategy
        with a −5% protective exit and a 17.5-hour unmonitored overnight
        window; an earnings gap routinely exceeds 5% and will blow straight
        through the exit at the open, before any run is awake to act.

      Two judgement notes. First, the tool returns articles where the ticker
      is merely *mentioned* — a KO query returns stories about Nvidia's market
      cap. Read the headline and `published_at`, and disregard anything where
      your ticker is incidental. Second, articles run up to ~2 weeks old;
      weight the last 2–3 sessions and treat older items as background.

      A dip WITH bad company-specific news is not the setup this strategy
      wants. CLAUDE.md already warns that a name making lower closes may be
      falling rather than basing — this is how you tell the difference, and
      when in doubt, skip. Record the veto and its reason in the journal so a
      repeatedly-vetoed name is visible across runs.

   Before each buy verify:
   - it is a common stock, not an ETF/ETP/closed-end fund — skip if unsure
   - price ≥ $5 and average volume supports a clean fill at your size
   - `get_equity_tradability` confirms the session you are tagging the order to
   - fewer than 9 open positions, and none already in this ticker
   - the buy keeps ≥10% of account value in cash
   - you are spending settled funds, not unsettled proceeds
   - size so that quantity × limit_price ≤ $500
   - the news/earnings check in 7d found no veto condition
8. For each order: `review_equity_order` first, inspect the estimate and any
   alerts, then `place_equity_order` with a `ref_id` from step 1. Tag
   `market_hours` to a session the name is actually eligible for:
   `regular_hours`, or `extended_hours` when running pre-/post-market.
   `all_day_hours` is rejected by the guardrail — the 24 Hour Market is not
   traded. Afterwards confirm via `get_equity_orders` that it was
   accepted. A guardrail rejection is final — do not retry or reshape the
   order.
   **Never leave a reviewed order unplaced.** Once you call
   `review_equity_order` and the estimate is acceptable, place it on the next
   turn. If you are low on turns, placing the order and writing a short
   journal entry beats analysing another candidate.
9. Record any NEW fills for the owner's phone alerts. From the
   `get_equity_orders` data you already pulled in step 2, find orders now in
   state `filled` or `partially_filled` whose `order_id` was not in the
   already-alerted list from step 1, and append one JSON object per line to
   `state/fills.jsonl` for each:
   `{"order_id","symbol","side","quantity","average_price","filled_at"}`
   — plus `"pnl_pct"` on a sell, computed against the entry price from the
   journal, and an optional short `"note"`. Append only; never rewrite or
   reorder existing lines, and never re-add an order_id already present.
   Dedupe and delivery are handled downstream, so a line here is enough.
10. Append a complete journal entry to `state/journal.md` (date/time ET,
   positions with entry prices, orders placed with ref_ids and fill status,
   observations, watch items for next run). Do this even if you placed no
   orders. **Do not skip this to keep analysing** — an unrecorded run is worse
   than an incomplete one.

   Include any news findings that changed a decision: a candidate vetoed in
   step 7d and why, or the character of the news behind a triggered exit in
   step 4. A name vetoed for a pending merger or an upcoming earnings date is
   likely to keep resurfacing in the scans — recording it means the next run
   can skip it cheaply instead of re-fetching the same articles. Note when the
   veto expires where you can, e.g. "skip until after 09-04 earnings".

Write scratch files, if you need any at all, to `state/tmp/` — not to `state/`
or `/tmp`. You cannot delete files in this headless run, so anything you leave
elsewhere accumulates.

Be decisive but conservative: when a setup is marginal, skip it. Most runs
should place zero orders.
