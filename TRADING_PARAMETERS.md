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
  The saved scan returns up to 200 rows sorted by price (high to low); when
  total matches exceed 200, re-run with a narrower filter (e.g. price
  BETWEEN $5-$50) to see the lower-priced tail rather than assuming those
  names were excluded — they're eligible, just off the first page.
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

## Hourly trend ladder, with micro-trend confirmation (real-time trend state)

Every run, recompute each candidate's and each holding's trend state on TWO
timeframes from LIVE data (get_equity_technical_indicators /
get_equity_historicals) — never rely on a stored state from a previous run:

- MACRO trend (context, from the last 6 completed HOURLY bars): sets
  whether the name is eligible to trade at all.
- MICRO trend (trigger, from the last 6-8 completed 5-MINUTE bars, extended
  hours): sets exact timing — when to actually fire the order. Don't wait
  for an hourly bar to close to confirm what 5-minute bars already show;
  use minute-level data (1min/5min interval, plus live RSI on the same
  timeframe) to catch a turn as it happens, the way MET/TAL's RSI cooling
  from 100→72→63 was tracked intraday rather than waiting for the hour to
  close.

Trend state (same UP/FLAT/DOWN definition on either timeframe):
- UP     — higher highs and higher lows, or close above the 20-bar EMA
           (hourly EMA for macro, 5-min EMA for micro) with a rising slope.
- FLAT   — neither; chop or a basing range.
- DOWN   — lower highs and lower lows, or close below a falling 20-bar EMA.

Rung size = 1/3 of the name's intended full position (so 3 rungs reach the
target; the 50%-of-account cap is the ceiling on the FULL position, not on
a rung). Never hold more than 3 rungs in one name.

### Laddering IN (accumulate into trending lows)
- Rung 1 opens on the dip signal — RSI ≤ 35 on the MICRO (5-min) timeframe
  is enough to trigger, don't wait for the hourly RSI to catch up — price
  at the lower end of the 5-10 day range also qualifies. Required gate:
  MACRO trend is UP or FLAT (never DOWN — a falling ladder averages into a
  downtrend); MICRO trend must not be actively DOWN at the moment of entry
  (a dip inside chop/basing is fine, a dip still falling on 5-min bars is
  not — wait one more 5-min bar for the micro low to hold).
- Rung 2 adds as soon as MICRO trend confirms UP: one completed 5-minute
  bar closing above its prior 5-minute high, price at/above the rung-1
  fill. Do not wait for an hourly close — the 5-min confirmation is enough
  as long as MACRO trend is still UP or FLAT.
- Rung 3 adds on a second consecutive UP 5-minute bar (or the equivalent
  hourly confirmation, whichever comes first), price at/above rung 2.
- Stop laddering in if MACRO trend prints DOWN, or the name is already at
  its cap. Rungs are added on confirmation, never on a further drop.

### Laddering OUT (distribute into trending highs)
- Sell one rung at +2% above average cost, a second at +4%, the last at
  +6% — each as a limit order into strength.
- Accelerate the ladder out (sell the next rung immediately, regardless of
  the price step) when MICRO (5-min) RSI ≥ 65 — this fires faster than
  waiting for hourly RSI — or when MACRO trend flips to DOWN while the
  position is green, or MICRO trend flips DOWN with two consecutive lower
  5-minute highs (an early warning ahead of the hourly turn).
- Hold the remaining rungs while MACRO trend state stays UP — a runner is
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
- Analysis/trade runs self-pace every 15-30 minutes based on volume:
  ~15 min during high-volume stretches (regular-hours open/close), ~30 min
  in quiet overnight periods, max hold while the market is fully closed.
  An hourly scheduled routine acts as backstop. Each run may analyze,
  place, or cancel orders within the limits above.
- Every placed/filled/cancelled order triggers a push notification to the
  owner's phone. Silent when no action is taken.

## Owner controls
- "Pause trading" disables the routine; "resume" re-enables.
- Parameter changes take effect by editing this file / telling the agent.
