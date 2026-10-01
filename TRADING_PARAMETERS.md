# Agentic Swing-Trading Parameters

Authorized by account owner on 2026-08-23 for autonomous trading on the
"Agentic" account (••••7684). Strategy: capture intraday/multi-day price
swings — accumulate during trending lows, sell into trending highs — trading
Robinhood's regular and extended sessions only. The 24 Hour Market is not
used: as of 2026-08-27 the owner directed that the agent trade only regular
and extended hours, so `all_day_hours` is disallowed at the guardrail level.

## Universe
- ALL US-listed individual common stocks, any industry or market cap —
  no fixed watchlist. Candidates are selected in real time each run from
  scanners and technicals (volume/momentum/RSI/range position), buying
  what looks strongest at that moment.
- Liquidity floor: price ≥ $5 (no penny stocks) and average volume
  ≥ 500K shares/day — enough for a clean limit fill at this account's size.
  Market cap floor is $300M (not $2B+ large-cap-only) so genuine small/
  mid-caps down to the $5 price floor are eligible, not just mega-caps.
- Scanner coverage: each scan call returns at most 200 rows, so a single
  broad scan can silently omit part of the field once matches exceed 200.
  Run BOTH saved scans every pass:
  - `edb15197-727a-48e5-9119-2a77b280f915` — broad, no price ceiling,
    sorted `Last desc` (price high to low), so it is the high-priced names
    that fill the first page and the cheap tail that falls off.
  - `b440c52a-da3a-403d-9d9c-92bb53ac5322` — low-price band $5-$50,
    same technical filters, sorted `Market cap desc`, exists specifically
    to catch names that fall off the broad scan's first page.

### Candidate cap — top 50 by trending strength
Both scans hard-filter `RSI (14, 1H) <= 35`, so every row they return is
already a pullback candidate; what distinguishes them is whether the market
is actually participating in the dip. Rank and cut BEFORE any per-name
analysis:

1. Take the union of both scans and drop duplicate tickers.
2. Apply the cheap screens that need no extra calls: `Asset type` is STOCK,
   `Last` >= $5, `Average volume` >= 500K.
3. Score each remaining row from columns the scans already return — no extra
   API calls, since relative volume is `Volume / Average volume` and both
   are present on both scans:
   - **primary: relative volume, descending.** A dip on heavy participation
     is a real repricing; a dip on thin volume is drift.
   - **tiebreak: `% Change`, descending.** Between two equally busy dips,
     prefer the one already stabilising over the one still bleeding.
4. Keep the **top 50** and discard the rest for this run. Only those 50 are
   eligible for 5-minute historicals, technical indicators, and price-book
   depth checks.

The cap is a compute and cost bound, not a strategy change: per-name
analysis is the expensive part of a run, and 50 candidates is far more than
the 9-position ceiling can absorb. Names cut this run are not blacklisted —
the ranking is recomputed from scratch every pass.

- Exclusions: no ETFs or other funds (ETPs, leveraged/inverse products,
  closed-end funds), no options, no crypto, no margin.
- 24 Hour Market eligibility is not considered: since orders are never
  tagged `all_day_hours`, overnight-eligible names have no advantage. What
  matters is that a candidate is tradable in the session being used —
  `regular_hours`, or `extended_hours` when placing pre-/post-market.

## Position sizing & limits
- Account risk capital: full account (~$1,000 starting).
- Max **$150 in any single stock** (~15% of account value), counting the
  whole position, not just one order. Enforced in code: `config/limits.json`
  `max_position_usd` = 150, so the guardrail rejects any single buy over
  $150; the agent keeps the total position under it. Stocks priced above
  $150 a share are therefore out of reach. Sizing is a ceiling, not a target
  — prefer smaller when the signal is weak or the spread is wide.
  Lowered from 50% / ~$500 by owner instruction on 2026-10-01. Over the first
  30 closed trades the large positions were the big losers — RVMD (~$415,
  −$35), APLD (~$273, −$14), MGM (~$171, −$9), CCJ (~$300, −$7) — while most
  winners were $8–$30 positions. Until the strategy has a track record,
  no single name should be able to cost more than ~$8 at the −5% exit.
- Keep ≥10% of account value in cash at all times.
- Max 50 trades (placed orders) per day.
- Cash-account discipline: buy only with settled funds (avoid good-faith
  violations; T+1 settlement). NOTE: settlement is the practical cap on
  daily activity — once settled cash is deployed and sold, those proceeds
  are not re-spendable until the next day, so realized trade count will
  usually be well below the 50/day ceiling.

## Entries and exits

### Entries (rewritten 2026-10-01 to match the rules the agent runs)
These replace the "micro-led trend ladder" added 2026-08-24, which bought in
three rungs off 5-minute RSI and MICRO/MACRO trend states. As with the exits
below, `CLAUDE.md` and the run prompt never adopted it, and at hourly runs the
agent sees one 5-minute bar in twelve, so triggers built on single 5-minute
bars cannot fire as written. Every entry is now **one limit buy** for the
whole position.

Buy an uptrending or basing name that has pulled back to support. Any of:
- hourly RSI ≤ 35 (both saved scans already filter to this);
- price near the lower end of its 5–10 day range;
- a 2%+ dip in a name whose higher-timeframe trend is still up.

Before buying:
- **Confirm on bars, never a quote alone** — `get_equity_historicals` and
  `get_equity_technical_indicators`. A name making successive lower closes may
  still be falling rather than basing; skip it.
- **Pass the market filter** (see "Circuit breakers"): no new buys while SPY
  is below its 20-day average or down 1% or more on the day.
- **Pass the news and earnings screen** (see "News & catalyst screen"),
  including the veto on earnings inside the next 2 trading days.
- **Fit the limits** in "Position sizing & limits": $150 per name in total,
  ≤ 9 positions, ≥ 10% cash, settled funds only.
- Place a marketable limit at or near the bid–ask, tagged `regular_hours`.
- When a setup is marginal, skip it. Most runs should buy nothing.

Dropped with the ladder: buying in thirds (rungs 2 and 3 added on 5-minute
confirmation); the 5-minute RSI ≤ 42 and "RSI troughed and turned up" dip
triggers; **Path B momentum buys** (5-minute RSI 45–68 breakouts on rising
volume); and the MICRO/MACRO trend states, including the DOWN-ACCEL gate,
whose job is now done by the successive-lower-closes check.

### Exits (rewritten 2026-10-01 to match the rules the agent runs)
These replace the 3-rung "Laddering OUT" scheme added 2026-08-24 (sell a
third at +1.5%, +3% and +5%, sped up by 5-minute RSI and MICRO/MACRO trend
flips). `CLAUDE.md` and the run prompt never adopted that scheme, and at
hourly runs the agent sees only one 5-minute bar in twelve, so triggers built
on them cannot fire as intended. Every exit now sells the **whole position in
one limit order**.

- **Protective exit — checked first, every run.** Sell the whole position
  when it is down ≥5% from average cost. There are no stop orders, so this
  only happens when a run checks and places the limit sell. Because runs are
  an hour apart, fills land a little past the line: −5.0% to −5.5% so far.
  The 09:30 run matters most, since it is the first look after 17.5 hours.
- **Profit exit.** Sell into strength with a limit order when any of these
  holds:
  - up +3–5% from entry;
  - hourly RSI ≥ 65 **and** up at least +2% from entry;
  - price at the upper end of its recent range.
- **RSI exits need a +2% gain first** (owner instruction, 2026-10-01). Below
  +2%, an RSI ≥ 65 reading is not an exit: hold, and let the other exits
  govern it. Reason: over the first 30 closed trades (2026-08-25 → 09-29) the
  average win was +$2.00 against an average loss of −$5.95. Many wins were
  RSI exits taken at +0–1.5% (HLN +0.6%, NKE 0.0%, KO −0.03%, AFRM +0.9%,
  PFE +1.3%, FCX +1.5%), while losses ran the full −5%. RSI ≥ 65 on a stock
  that has only bounced back to its entry price is a recovery, not strength
  worth selling.
- **Earnings exit.** Close a holding before it reports — see "News &
  catalyst screen" below for the exact 14:30 / 15:30 trigger.
- **News on a triggered exit.** When a holding crosses a threshold, check its
  news before selling. That never makes the protective exit optional; it
  records whether the move was company-specific or market-wide (see "News &
  catalyst screen").
- **Averaging down: at most once per position**, never into company-specific
  bad news, and the whole position stays within the $150 per-name cap.

Dropped with the ladder: selling in thirds; 5-minute RSI ≥ 65 and MICRO
trend-flip exits; the MACRO DOWN-ACCEL exit; and the extra protective trigger
"trend state DOWN while the position is red". The old line "no averaging
down: a rung is never added below the previous rung's fill" is replaced by
the at-most-once rule above, which is what `CLAUDE.md` has always said.

## Circuit breakers
- If total account value falls below $850 (−15%), stop opening new
  positions, notify the owner, and wait for instructions.
- Any order rejection, unexpected balance, or tool failure: halt trading
  for that run and notify.
- **Market filter** (owner instruction, 2026-10-01): no new positions while
  SPY is below its 20-day average close, or is down 1% or more on the day.
  Gates entries only; exits are never blocked. Computed VM-side each run by
  `scripts/market-regime.py` (via `scripts/run-context.sh`). If that fails,
  the agent applies the same rule from SPY quotes and daily bars itself, and
  if it cannot, it opens nothing that run.
  Checked against SPY daily closes before adoption: it would have blocked the
  2026-08-31 to 09-02 entries, which netted about −$36 (mostly RVMD, whose
  loss the 09-09 outage deepened). It would not have blocked the MGM and
  MICC losses on 09-25, a selloff in smaller names while SPY was above its
  average. It blocked 14 of 43 sessions from 07-30 to 09-29 while SPY moved
  sideways. A small sample: re-judge it after a few more weeks of trades.

## News & catalyst screen
- Added 2026-09-01 on owner instruction. Before any buy, and before selling a
  position that has triggered an exit, the agent checks `get_equity_news` and
  `get_earnings_results` for that ticker.
- Purpose: technicals cannot distinguish a noise dip (the setup this strategy
  wants) from a permanent repricing (dilution, investigation, failed trial,
  pending buyout). The screen is what tells them apart.
- **Hard vetoes on a buy:** pending acquisition/merger/going-private bid;
  announced dilutive offering, ATM or convertible; fraud allegations, SEC/DOJ
  investigation, restatement, or auditor/CFO/CEO departure; delisting,
  bankruptcy, going-concern or reverse-split risk; failed trial, lost FDA
  decision, or lost major contract; **earnings inside the next 2 trading
  days** (an earnings gap routinely exceeds the −5% exit and clears it at the
  open, inside the unmonitored window).
- **Hard exit on a holding: never carry a position through its own print.**
  Added 2026-09-01, same day as the entry veto and for the same reason — the
  veto only ever gated *buying* into a print, which left the symmetric case
  open: a position bought while earnings were still distant simply rides
  through the report unmanaged. The rule: **on the 14:30 and 15:30 runs, close
  any holding reporting `pm` today or at any time on the next trading day**,
  and on any run close a holding reporting before that day's next run. 14:30 is
  preferred and 15:30 is the last chance, not the plan, since a limit entered
  near the close may not fill. Deliberately not phrased as "reports before the
  next scheduled run": at 14:30 the next run is 15:30 and a `pm` report today
  is not before it, so that test never fires at 14:30 and defers every earnings
  exit into the close.

  This deliberately sells healthy positions roughly once a quarter per name.
  That cost is accepted: a position held through a pre-open print has no −5%
  floor, only whatever the open decides, and the name can be bought back after
  it reports. Found by the first live smoke test of `news-brief.py`, which
  turned up CXM (3 @ $7.7887) reporting pre-open the next morning — bought
  12:04 ET, 3h46m before the entry veto was committed at 15:50 ET, so the gate
  had not failed, it did not yet exist.

  Costs no extra calls: the date is already fetched at entry, and is now
  recorded in the journal beside the entry price so the per-run check is
  arithmetic. Re-fetch a single holding only when its recorded date is within
  3 trading days and was `verified: false`, since tentative dates move.
- **Empty earnings results are "unknown", not "clear".** `get_earnings_results`
  returns an empty array for names with no earnings history in the feed —
  recent spinoffs and new listings. MICC (Magnum Ice Cream, separated from
  Unilever) is the known case in the current book. Left alone this passes the
  earnings gate silently, which is indistinguishable from a confirmed-clear
  name. On an empty array, fall back to `get_earnings_calendar` with `days: 3`
  and apply the 2-day veto if the ticker appears; if it does not, the buy may
  proceed but "no earnings data" is recorded in the journal so the blind spot
  is visible across runs. The extra call fires only in the rare empty case.
- **Fetched VM-side.** `scripts/news-brief.py` calls the Robinhood MCP endpoint
  over HTTP outside the agent's context and prints headlines, dates,
  publishers, and keyword flags — no article bodies. Measured 1,145 tokens for
  7 tickers (~165 each) against ~1,900 per ticker for the raw
  `get_equity_news` tool: a ~91% reduction, which is what makes screening
  several names per run affordable at all. Auth reuses the MCP OAuth token
  Claude Code already maintains; the token is never printed or logged, and the
  Read-tool deny on the credential file stays in force.
- **Fallback is the MCP tool.** Any auth or transport failure exits non-zero
  with a reason and no partial brief; the agent falls back to
  `get_equity_news` for the names it actually cares about. A news fetch
  failing must never block a protective exit — that is the failure mode that
  cost four runs on 2026-09-01.
- **Still a gate on finalists, not a list screen.** Even at ~165 tokens a
  ticker, screening all 50 candidates buys nothing the 9-position ceiling can
  use. One to three names per run is the intended volume; zero on a run with
  no entries and no triggered exits.
- **The keyword flagger is tested, because it fails silently.** A broken regex
  prints `FLAGS: none`, which reads identically to "this stock is clean". The
  first version anchored patterns at both ends, so "delist" could not match
  "delisting" and "resign" could not match "resigns" — real vetoes producing
  no flag. `scripts/test-news-flags.py` covers 26 positive and 12 negative
  cases; run it after touching the patterns.
- **Source is Robinhood's own news feed only.** `WebFetch` and `WebSearch`
  remain denied in `.claude/settings.json` and should stay denied: this agent
  places real orders unattended, and arbitrary web content is an injection
  surface where a crafted page or headline could try to instruct it. A
  first-party feed is not immune to bad information but it is not an
  instruction channel.
- Known limitation: the feed returns articles that merely *mention* the
  ticker, so relevance is a judgement call, and items run up to ~2 weeks old.

## Cadence & reporting
- Analysis/trade runs **hourly on the half hour** during Robinhood's **regular
  session only** — 09:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30 ET, Monday
  to Friday. Seven runs per trading day. Each run may analyze, place, or cancel
  orders within the limits above.
- Narrowed twice on 2026-09-01, both times on owner instruction, to cut Claude
  compute cost: from every-15-minutes / 07:00–20:00 (~52 runs/day) to
  every-30-minutes / 09:30–16:00 (13 runs/day), then from 30 minutes to hourly
  (7 runs/day). Measured spend at the original cadence was $2.57/run, ~$134/day
  against a ~$1,000 account. Measured cost of the three Haiku 4.5 runs that
  executed at the 30-minute cadence was ~$0.61/run, so 13 runs was ~$7.9/day
  and 7 runs is ~$4.3/day. This is an explicit owner decision to trade
  monitoring frequency for cost, not a drift.
- Pre-market and post-market extended sessions are no longer covered. The
  agent is never awake outside regular hours, so **every order should be
  tagged `regular_hours`**; `extended_hours` remains accepted by
  `config/limits.json` but is unreachable in practice.
- The overnight 24 Hour Market window is NOT covered, and as of 2026-08-27 is
  not traded at all. Three consequences to hold in mind:
  - The agent sees prices once an hour, not every 5-minute bar. A move that
    starts and reverses inside a one-hour gap is invisible, which is why entry
    and exit triggers are read on hourly RSI, multi-day ranges and percentage
    moves rather than single 5-minute bars.
  - Nothing manages positions between 16:00 and 09:30 ET — 17.5 unmonitored
    hours, up from 11. The 09:30 run is therefore the first sight of prices
    since the prior close and the one most likely to find a breached
    protective threshold from an overnight gap.
  - `all_day_hours` is rejected by `hooks/guardrails.py`, so no order can fill
    inside the unmonitored window. Overnight gap risk on positions already
    held remains and is accepted; the fill-while-unattended risk is removed.
    An order still working at 16:00 does not fill overnight — verify carryover
    with `get_equity_orders` at the start of the next run.
- Every placed/filled/cancelled order triggers a push notification to the
  owner's phone. Silent when no action is taken.

## Owner controls
- "Pause trading" disables the routine; "resume" re-enables.
- Parameter changes take effect by editing this file / telling the agent.
