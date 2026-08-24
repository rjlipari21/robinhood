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
- Liquidity floor: price ≥ $5 (no penny stocks) and sufficient average
  volume for a clean limit fill at the intended size.
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

## Hourly trend ladder (real-time trend state)

Every run, recompute each candidate's and each holding's trend state from
LIVE hourly bars (get_equity_technical_indicators / get_equity_historicals,
interval=hour). Do not rely on a stored state from a previous run — the
ladder is re-derived from current data each time.

Trend state, from the last 6 completed hourly bars:
- UP     — higher highs and higher lows, or hourly close above the 20-bar
           hourly EMA with a rising EMA slope.
- FLAT   — neither; chop or a basing range.
- DOWN   — lower highs and lower lows, or hourly close below a falling
           20-bar hourly EMA.

Rung size = 1/3 of the name's intended full position (so 3 rungs reach the
target; the 50%-of-account cap is the ceiling on the FULL position, not on
a rung). Never hold more than 3 rungs in one name.

### Laddering IN (accumulate into trending lows)
- Rung 1 opens on the dip signal: hourly RSI ≤ 35 or price at the lower end
  of the 5-10 day range, AND trend state is UP or FLAT (never DOWN — a
  falling ladder averages into a downtrend).
- Rung 2 adds only after the hourly trend turns back UP: one completed
  hourly bar closing above the prior hourly high, and price at/above the
  rung-1 fill.
- Rung 3 adds on a second consecutive UP hourly bar, price at/above rung 2.
- Stop laddering in if trend state prints DOWN, or the name is already at
  its cap. Rungs are added on confirmation, never on a further drop.

### Laddering OUT (distribute into trending highs)
- Sell one rung at +2% above average cost, a second at +4%, the last at
  +6% — each as a limit order into strength.
- Accelerate the ladder out (sell the next rung immediately, regardless of
  the price step) when hourly RSI ≥ 65, or the hourly trend state flips to
  DOWN while the position is green.
- Hold the remaining rungs while trend state stays UP — a runner is how the
  ladder pays for the small losses.

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
