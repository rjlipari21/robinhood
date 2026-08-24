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

## Entry (buy the trending low)
- Uptrending or basing name pulled back to support: hourly RSI ≤ 35,
  price near lower end of its 5–10 day range, or a 2%+ dip in a name whose
  higher-timeframe trend is still up.
- Limit orders only (marketable limit at/near bid-ask), `all_day_hours`
  session so entries can fill overnight and pre/post market.

## Exit (sell the trending high)
- Profit target: +3–5% from entry, or hourly RSI ≥ 65 / price at upper end
  of recent range — sell into strength with a limit order.
- Protective exit: close any position down ≥5% from entry (stop orders are
  regular-hours-only, so this is enforced by the hourly check placing a
  limit sell).
- No averaging down more than once per position.

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
