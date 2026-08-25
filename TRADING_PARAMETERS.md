# Agentic Swing-Trading Parameters

Authorized by account owner on 2026-08-23 for autonomous trading on the
"Agentic" account (••••7684). Strategy: capture intraday/multi-day price
swings — accumulate during trending lows, sell into trending highs — using
Robinhood's 24 Hour Market where available.

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

KNOWN GAP: because both scans filter to hourly RSI <= 35, Path B
(momentum-buy: MICRO trend UP, 5-min RSI 45-68, breakout on rising volume)
has no scanner feeding it — a breakout candidate is structurally excluded
from scan output. Path B currently only fires on names already surfaced for
another reason. Closing this needs a third saved scan on the Path B band.
- Exclusions: no ETFs or other funds (ETPs, leveraged/inverse products,
  closed-end funds), no options, no crypto, no margin.
- 24 Hour Market eligibility is a bonus, not a requirement: names not
  eligible for overnight trading are still fair game during regular and
  extended hours.

## Position sizing & limits
- Account risk capital: full account (~$1,000 starting).
- Max 50% of account value in any single stock (~$500 per name at current
  size). With the cash floor below, that means at most 2 max-size
  positions; smaller sizes may be used to hold more names concurrently
  (up to 9). Sizing is a ceiling, not a target — take a full 50% only on
  a high-conviction setup, and prefer smaller when the signal is weak or
  the spread is wide.
- Keep ≥10% of account value in cash at all times.
- Max 50 trades (placed orders) per day.
- Cash-account discipline: buy only with settled funds (avoid good-faith
  violations; T+1 settlement). NOTE: settlement is the practical cap on
  daily activity — once settled cash is deployed and sold, those proceeds
  are not re-spendable until the next day, so realized trade count will
  usually be well below the 50/day ceiling.

## Micro-led trend ladder, with macro as context only (real-time trend state)

Every run, recompute each candidate's and each holding's trend state on TWO
timeframes from LIVE data (get_equity_technical_indicators /
get_equity_historicals) — never rely on a stored state from a previous run.
MICRO leads: it is the primary driver for both entries and exits. MACRO is
context used only to veto a genuinely accelerating downtrend, not a
mandatory precondition for every entry.

- MICRO trend (primary, from the last 6-8 completed 5-MINUTE bars, extended
  hours, plus live 5-min RSI): sets both whether and exactly when to fire —
  don't wait for an hourly bar to close to confirm what 5-minute bars
  already show; use minute-level data to catch a turn as it happens, the
  way MET/TAL's RSI cooling from 100→72→63 was tracked intraday rather than
  waiting for the hour to close.
- MACRO trend (secondary, from the last 6 completed HOURLY bars): used only
  to screen out names in an ACCELERATING hourly downtrend (see DOWN-ACCEL
  below), not to veto every hourly dip. A mildly/slowly falling hourly EMA
  with price only marginally below it no longer blocks an otherwise-good
  micro setup — that was screening out too many real entries.

Trend state:
- UP        — higher highs and higher lows, or close above the 20-bar EMA
              with a rising slope.
- FLAT      — neither; chop or a basing range.
- DOWN      — lower highs and lower lows, or close below a falling 20-bar
              EMA.
- DOWN-ACCEL (macro only) — DOWN, and the gap between price and the falling
              EMA is widening bar-over-bar (not just narrowly below it) —
              this is the only macro state that blocks a new entry.

Rung size = 1/3 of the name's intended full position (so 3 rungs reach the
target; the 50%-of-account cap is the ceiling on the FULL position, not on
a rung). Never hold more than 3 rungs in one name.

### Laddering IN — two independent entry paths (either one opens rung 1)
Background (from the 1-minute-polling era, when this was tuned): polling was
missing most dips, because 5-min RSI mean-reverts fast enough that a strict
≤35 read is rarely caught mid-bar, so the account sat in cash through a
grinding-higher tape. Two changes fixed it: widen the dip trigger, and add a
second path that buys confirmed strength instead of only buying weakness.

Both widened thresholds still apply at the current 5-minute cadence. Polling
now lands on bar boundaries, so every completed 5-minute bar is seen exactly
once instead of being read mid-formation — but an intra-bar extreme that
reverses before the close is still invisible, which is what the wide bands
are for.

**Path A — dip-buy (unchanged in spirit, wider trigger):**
- Rung 1 opens on 5-min RSI ≤ 42 (was ≤35 — the tighter threshold was
  missing dips that bounced between polls), or price at the lower end of
  the 5-10 day range, or RSI having troughed and turned up within the last
  2 completed 5-min bars (catches a dip whose exact bottom fell between
  polls). Required gates: MICRO trend must not be actively DOWN at the
  moment of entry (a dip inside chop/basing is fine, a dip still falling on
  5-min bars is not — wait one more 5-min bar for the micro low to hold);
  MACRO trend must not be DOWN-ACCEL.

**Path B — momentum-buy (buy strength, not just weakness):**
- Rung 1 opens when MICRO trend is UP with 5-min RSI in the 45-68 band
  (confirmed uptrend, not yet overbought — widened from 65 after repeated
  misses where RSI crossed 65 between polls with no bar landing
  in-window) AND price has just closed above its prior 3-bar high on
  rising volume (a live breakout, not a stale high). This lets the
  strategy act on names already trending up instead of requiring them to
  dip first. MACRO trend must not be DOWN-ACCEL (loosened from a strict
  "not DOWN" — that gate was blocking most real breakouts, since an
  ordinary mildly-falling hourly EMA is common even in a healthy tape;
  DOWN-ACCEL is now the same bar Path A uses).
- Skip Path B if 5-min RSI ≥ 68 (too extended — wait for either a pullback
  into Path A range or a fresh breakout).

**Both paths, rungs 2 and 3:**
- Rung 2 adds as soon as MICRO trend confirms UP: one completed 5-minute
  bar closing above its prior 5-minute high, price at/above the rung-1
  fill. Do not wait for an hourly close.
- Rung 3 adds on a second consecutive UP 5-minute bar, price at/above
  rung 2.
- Stop laddering in if MACRO trend prints DOWN-ACCEL, or the name is
  already at its cap. Rungs are added on confirmation, never on a further
  drop.

### Laddering OUT (distribute into trending highs)
- Sell one rung at +1.5% above average cost, a second at +3%, the last at
  +5% — each as a limit order into strength (tightened from 2/4/6% so
  winners round-trip faster and free up settled cash for the next entry).
- Accelerate the ladder out (sell the next rung immediately, regardless of
  the price step) when MICRO (5-min) RSI ≥ 65, or MICRO trend flips DOWN
  with two consecutive lower 5-minute highs — these are the primary exit
  triggers and fire without waiting on macro. MACRO flipping to DOWN-ACCEL
  while the position is green is an additional, faster trigger to exit.
- Hold the remaining rungs while MICRO trend state stays UP — a runner is
  how the ladder pays for the small losses.

### Protective exit (unchanged, overrides the ladder)
- Close the ENTIRE position (all rungs at once, not laddered) when it is
  down ≥5% from average cost, or when trend state prints DOWN and the
  position is red. Stop orders are regular-hours-only, so this is enforced
  by each run placing a limit sell.
- No averaging down: a rung is never added below the previous rung's fill.

## Circuit breakers
- If total account value falls below $850 (−15%), stop opening new
  positions, notify the owner, and wait for instructions.
- Any order rejection, unexpected balance, or tool failure: halt trading
  for that run and notify.

## Cadence & reporting
- Analysis/trade runs every 5 minutes while the market is open (regular,
  extended, and overnight), matching the 5-minute bar the entry and exit
  rules are actually decided on — polling faster than the bar interval
  mostly re-reads a bar that has not closed yet. During fully-closed market
  hours (Fri 20:00 ET to Sun 20:00 ET) runs are skipped entirely; there's no
  new intraday data to react to. Each run may analyze, place, or cancel
  orders within the limits above.
- Every placed/filled/cancelled order triggers a push notification to the
  owner's phone. Silent when no action is taken.

## Owner controls
- "Pause trading" disables the routine; "resume" re-enables.
- Parameter changes take effect by editing this file / telling the agent.
