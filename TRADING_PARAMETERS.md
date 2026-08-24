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
- Scanner coverage: each scan call returns at most 200 rows sorted by price
  (high to low), so a single broad scan can silently omit the low-priced
  tail once matches exceed 200. Run BOTH saved scans every pass:
  - `edb15197-727a-48e5-9119-2a77b280f915` — broad, no price ceiling.
  - `b440c52a-da3a-403d-9d9c-92bb53ac5322` — low-price band $5-$50,
    same technical filters, exists specifically to catch names that fall
    off the broad scan's first page.
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

### Laddering IN (accumulate into trending lows)
- Rung 1 opens on the MICRO dip signal — RSI ≤ 35 on the 5-min timeframe
  triggers it, don't wait for the hourly RSI to catch up; price at the
  lower end of the 5-10 day range also qualifies. Required gates: MICRO
  trend must not be actively DOWN at the moment of entry (a dip inside
  chop/basing is fine, a dip still falling on 5-min bars is not — wait one
  more 5-min bar for the micro low to hold); MACRO trend must not be
  DOWN-ACCEL (an ordinary DOWN or FLAT hourly reading no longer blocks the
  entry, only an accelerating one does).
- Rung 2 adds as soon as MICRO trend confirms UP: one completed 5-minute
  bar closing above its prior 5-minute high, price at/above the rung-1
  fill. Do not wait for an hourly close.
- Rung 3 adds on a second consecutive UP 5-minute bar, price at/above
  rung 2.
- Stop laddering in if MACRO trend prints DOWN-ACCEL, or the name is
  already at its cap. Rungs are added on confirmation, never on a further
  drop.

### Laddering OUT (distribute into trending highs)
- Sell one rung at +2% above average cost, a second at +4%, the last at
  +6% — each as a limit order into strength.
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
- Analysis/trade runs every 1 minute while the market is open (regular and
  extended hours), to react to micro (5-min) trend/RSI turns as close to
  live as possible. During fully-closed market hours, fall back to the
  15-30 min self-paced cadence — there's no new intraday data to react to.
  An hourly scheduled routine acts as backstop. Each run may analyze,
  place, or cancel orders within the limits above.
- Every placed/filled/cancelled order triggers a push notification to the
  owner's phone. Silent when no action is taken.

## Owner controls
- "Pause trading" disables the routine; "resume" re-enables.
- Parameter changes take effect by editing this file / telling the agent.
