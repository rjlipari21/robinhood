# Robinhood Autonomous Trading Agent on GCP

An autonomous momentum-trading agent that runs Claude (via Claude Code in
headless mode) on a GCP Compute Engine VM during market hours, connected to
[Robinhood's official agentic MCP server](https://agent.robinhood.com/mcp/trading).

**⚠️ This places real-money trades automatically.** Risk limits are enforced
in code (see Guardrails below), but you are responsible for everything it
does. Start small and watch it for the first few days.

## How it works

```
systemd timer (robinhood-agent@USER.timer)
  └─ scripts/run-agent.sh          market-hours + kill-switch gate
       └─ claude -p prompts/trading-run.md      one headless agent run
            ├─ CLAUDE.md                        strategy mandate + rules
            ├─ .mcp.json                        Robinhood MCP server (OAuth)
            ├─ hooks/guardrails.py              HARD order caps (PreToolUse)
            └─ state/journal.md, ledger.json    memory between runs

systemd timer (rh-token-refresh@USER.timer)
  └─ scripts/refresh-rh-token.py   keeps the Robinhood OAuth token alive
```

- **Strategy:** swing trading — buy pullbacks, sell into strength. Universe is
  every US-listed common stock, picked in real time each run (no watchlist);
  ETFs, funds, and sub-$5 names excluded. Owner intent lives in
  `TRADING_PARAMETERS.md`; the agent's mandate in `CLAUDE.md`; the hard caps
  in `config/limits.json`, enforced by `hooks/guardrails.py`.
- **Account:** only the agentic-enabled cash account listed in
  `config/limits.json`. Options and all other accounts are blocked.

## What's here

| File | Purpose |
| --- | --- |
| `TRADING_PARAMETERS.md` | The live strategy spec — owner intent. Universe, position sizing, the micro-led trend ladder, circuit breakers, cadence. This is the document that changes as the strategy evolves. |
| `CLAUDE.md` | The agent's operating mandate, derived from `TRADING_PARAMETERS.md`. |
| `prompts/trading-run.md` | The prompt for a single headless run. |
| `config/limits.json` | Hard numeric caps the guardrail hook enforces. |
| `hooks/guardrails.py` | PreToolUse hook: rejects non-conforming orders before they reach Robinhood. |
| `hooks/notify.py` | PostToolUse hook: pushes placed/cancelled orders to your phone via ntfy. |
| `scripts/run-agent.sh` | One scheduled trading run, with market-hours and kill-switch gates. |
| `scripts/refresh-rh-token.py` | Renews the Robinhood MCP OAuth token headlessly. |
| `scripts/install-token-refresh.sh` | Installs the daily token-refresh timer. |
| `scripts/create-vm.sh` | Creates a small Debian VM (`e2-small`) via `gcloud`. |
| `scripts/setup-vm.sh` | Run on the VM: installs git, Node.js, Claude Code CLI, clones this repo. |
| `scripts/sync-vm.sh` | Pulls the latest strategy from the active branch. Safe anytime. |
| `scripts/install-sync-cron.sh` | Installs a cron job running `sync-vm.sh` every 5 minutes. |
| `systemd/` | Templated units for the trading agent timer. |
| `.mcp.json` | Registers the `robinhood-trading` HTTP MCP server for this repo. |

## Guardrails (enforced in code, not just prompts)

`hooks/guardrails.py` runs as a Claude Code PreToolUse hook on every
`place_equity_order` call. An order is **rejected before it reaches
Robinhood** if the buy exceeds the per-position notional cap, the limit price
is under `min_price_usd`, today's buy count would pass `max_orders_per_day`,
it targets the wrong account, it is not a limit order, it names a session
outside `allowed_market_hours`, or the kill switch is set. Executed orders
land in `state/ledger.json`, which the daily count is computed from. Edit
`config/limits.json` to change caps.

Notional is computed as `quantity × limit_price`, so the per-position cap
cannot be sidestepped by switching order shape.

Two caps apply to **buys only**, deliberately, so an exit is never blocked:
the notional limit and the daily count. A position that grows past the cap
must stay sellable in one order, and a protective sell must never be refused
because the day's order budget is spent.

**What the hook cannot check.** It runs offline with no broker access, so the
9-position ceiling, the 10% cash reserve, the $850 circuit breaker,
settled-funds discipline, and — since the universe is every US-listed common
stock — the ETF/fund exclusion and liquidity screen are all agent judgment.
They are stated in `CLAUDE.md`; nothing enforces them mechanically.

**Kill switch:** `touch state/HALT` stops all trading instantly (both the
runner and the hook check it). `rm state/HALT` resumes.

### Keeping intent and enforcement in sync

`TRADING_PARAMETERS.md` is owner intent; `config/limits.json`, `CLAUDE.md`,
and `prompts/trading-run.md` are what actually constrain the agent. **These
drift apart silently** — the doc is prose, the caps are code, and nothing
cross-checks them. When you tune the strategy doc, walk the same numbers
through all four files, or the agent will quietly trade to the old rules.

## Push notifications

`hooks/notify.py` fires as a PostToolUse hook and pushes to your phone via
[ntfy.sh](https://ntfy.sh) — no account, no API key, stdlib only.

Setup: install the ntfy app (iOS/Android), then **subscribe to the exact topic
string** in `NOTIFY_URL` in your `.env`. This is the step that is easy to miss —
publishing succeeds with HTTP 200 whether or not anything is listening, so a
topic nobody has subscribed to looks identical to a working setup from the
sending side. If alerts do not arrive, check the subscription first.

`NOTIFY_TOKEN` is optional: set it to an ntfy access token (`tk_...`) for
reserved topics or any instance that denies anonymous publishing. Plain
ntfy.sh topics need no token. Unset `NOTIFY_URL` to disable notifications
entirely.

To verify from the VM without a phone:

```bash
set -a; . ./.env; set +a
curl -s "$NOTIFY_URL/json?poll=1" | tail -3     # messages retained on the topic
```

An unreserved ntfy.sh topic is readable by anyone who learns the string, which
is why it is long, random, and lives only in the gitignored `.env`.

Covered: **placed** and **cancelled** orders, each driven by the tool call
itself. **Fills are NOT covered** — a fill happens at the broker minutes or
hours after the order, so no PostToolUse hook can observe it. Catching fills
needs a separate poller comparing `get_equity_orders` against the ledger; the
journal is the record of fills until that exists.

A notification never blocks a trade: every failure path in `notify.py` exits 0,
so an unreachable endpoint, bad URL, or malformed payload loses the alert and
leaves the order untouched.

## Setup on a GCP Linux VM

### 1. Create the VM (from your laptop)

Requires the `gcloud` CLI, authenticated against your project:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
./scripts/create-vm.sh          # override VM_NAME / ZONE / MACHINE_TYPE via env vars
```

If you already have a GCP machine, skip this step.

### 2. Install prerequisites and clone

```bash
sudo apt-get update && sudo apt-get install -y git python3 curl
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g @anthropic-ai/claude-code

git clone https://github.com/rjlipari21/robinhood.git ~/robinhood
cd ~/robinhood
chmod +x scripts/*.sh hooks/*.py
```

`scripts/setup-vm.sh` automates this.

### 3. Authenticate Claude Code

Either set an API key (simplest for a server):

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > ~/robinhood/.env
chmod 600 ~/robinhood/.env
```

…or, to use a Claude subscription, run `claude setup-token` interactively
once and follow the prompts.

### 4. One-time Robinhood OAuth

The Robinhood MCP server uses OAuth with a `localhost` callback, and the
callback listener runs **on the VM** — so the redirect has to reach the VM,
not your laptop. Forward the port through SSH:

1. On the VM: `cd ~/robinhood && claude`, then type `/mcp`, select
   `robinhood-trading`, and choose Authenticate. It prints an authorization
   URL — **don't open it yet**. Find the `redirect_uri=http://localhost:PORT`
   parameter in that URL and note the PORT (it changes every attempt).
2. On your local machine, open a second terminal:
   `gcloud compute ssh YOUR_VM --zone=YOUR_ZONE -- -L PORT:localhost:PORT`
   (or `ssh -L PORT:localhost:PORT you@vm-ip`). Leave it open.
3. Open the authorization URL in your local browser, log in to Robinhood,
   approve. The redirect lands on `localhost:PORT`, tunnels to the VM, and
   Claude Code stores the token in `~/.claude/.credentials.json`.
4. Sanity check: ask "list my accounts" and confirm you see the Agentic
   account. Then exit.

**Without a tunnel**, the redirect fails (nothing listens on your laptop's
port). Recover by copying the failed URL from the address bar and passing it
to the `complete_authentication` tool — but the listener times out in about
two minutes, so have it ready.

### 5. Install the token auto-renewal timer

The access token lasts ~7.8 days. Renewal uses the refresh grant, which is a
public client needing no secret and no browser, so it runs unattended:

```bash
./scripts/install-token-refresh.sh
```

This installs a daily timer that refreshes when the token is within 96h of
expiry — about four days of retries before an expiry could bite. On failure
it writes `state/TOKEN_REFRESH_FAILED` and logs to `logs/token-refresh.log`.

Check status anytime, without contacting Robinhood:

```bash
python3 scripts/refresh-rh-token.py --check-only
```

Robinhood **rotates the refresh token on every use**, so a lost write means a
dead credential. Writes are atomic with a timestamped backup, and `flock`
serialises against concurrent runs.

Step 4 is only needed again if the refresh chain breaks outright — revocation,
password change, MFA reset, or downtime longer than the refresh token's
lifetime (which Robinhood does not publish).

### 6. Test one run manually

```bash
cd ~/robinhood && ./scripts/run-agent.sh
cat logs/run-$(date +%F).log
cat state/journal.md
```

Run this during market hours; outside them it exits with a "skipping" line.

### 7. Install the schedule

```bash
sudo cp systemd/robinhood-agent@.service systemd/robinhood-agent@.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "robinhood-agent@$USER.timer"
systemctl list-timers "robinhood-agent@$USER.timer"
```

Both units are templates. The instance name after the `@` is your username,
which fills in `User=` and the `/home/<user>/robinhood` paths in the service —
so `robinhood-agent@alice.timer` triggers `robinhood-agent@alice.service` with
no file edits. It assumes the repo lives at `~/robinhood`; if it doesn't, edit
`WorkingDirectory=` and `ExecStart=` in the service file.

The unit references `scripts/run-agent.sh` by absolute path. If the VM's
checkout is on a branch that lacks that file, every timer firing fails with
`203/EXEC` and **no trades happen at all** — silently, since a failed unit
writes nothing to the run log. Check with
`systemctl status "robinhood-agent@$USER.service"` if the agent seems idle.

### 8. Keep the VM in sync

```bash
./scripts/install-sync-cron.sh
```

Installs a cron job running `git pull` every 5 minutes, so the strategy on the
VM matches what gets committed here. Check `~/robinhood-sync.log` to confirm.
This only syncs files — the trading loop is the systemd timer from step 7.

## Day-to-day

- **Watch it:** `tail -f ~/robinhood/logs/run-$(date +%F).log` and read
  `state/journal.md` — the agent writes a dated entry every run.
- **Pause:** `touch ~/robinhood/state/HALT` · **Resume:** `rm ~/robinhood/state/HALT`
- **Stop entirely:** `sudo systemctl disable --now "robinhood-agent@$USER.timer"`
- **Token health:** `python3 scripts/refresh-rh-token.py --check-only`
- **Tune:** edit `TRADING_PARAMETERS.md` (owner intent), then bring
  `config/limits.json`, `hooks/guardrails.py`, and `CLAUDE.md` into line with
  it. Changes take effect next run.

## Notes and caveats

- Cash account: the mandate forbids same-day round trips (except stop-losses)
  to avoid good-faith violations, and unsettled funds (T+1) naturally limit
  re-use of proceeds.
- **Inference cost is the dominant running expense, not trading fees.**
  Measured across this project's session history — 859 turns, 98.6M tokens —
  usage is 95.8% cache reads, 3.3% cache writes, 0.9% output, with a floor of
  ~22–25K tokens per turn that grows as history accumulates. At the one-minute
  cadence that works out to roughly 150–400M tokens/day. `run-agent.sh` pins
  `claude-sonnet-5` and `--max-turns 30` to hold that down; on an Opus default
  with 60 turns the same schedule costs several hundred dollars a day, which
  for a ~$1,000 account exceeds the capital within days. Check actual spend
  after the first day, and re-check after changing the cadence, the model, or
  the turn ceiling.
- All credentials live in OAuth tokens Claude Code stores on the VM
  (`~/.claude/.credentials.json`) and in the gitignored `.env`. Nothing
  sensitive is committed here.
- Trading tools can place real orders. The guardrail hook is the only hard
  stop; prompt-level rules are not enforcement.
