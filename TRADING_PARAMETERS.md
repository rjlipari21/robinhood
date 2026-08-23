# Agentic Swing-Trading Parameters

Authorized by account owner on 2026-08-23 for autonomous trading on the
"Agentic" account (••••7684). Strategy: capture intraday/multi-day price
swings — accumulate during trending lows, sell into trending highs — using
Robinhood's 24 Hour Market where available.

## Universe
- Liquid, high-volume US equities eligible for the 24 Hour Market
  (mega/large caps and high-beta names, e.g. NVDA, TSLA, AMD, PLTR, COIN,
  META, AAPL, MSFT, AMZN, GOOGL, MSTR, SMCI). Chosen per run from
  technicals; no penny stocks, no leveraged ETFs, no options, no crypto,
  no margin.

## Position sizing & limits
- Account risk capital: full account (~$1,000 starting).
- Max 3 concurrent positions; max ~$300 (30% of account value) per position.
- Keep ≥10% of account value in cash at all times.
- Max 4 new orders per day.
- Cash-account discipline: buy only with settled funds (avoid good-faith
  violations; T+1 settlement).

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
- Automated check runs hourly (scheduler minimum). Each run may analyze,
  place, or cancel orders within the limits above.
- Every placed/filled/cancelled order triggers a push notification to the
  owner's phone. Silent when no action is taken.

## Owner controls
- "Pause trading" disables the routine; "resume" re-enables.
- Parameter changes take effect by editing this file / telling the agent.
