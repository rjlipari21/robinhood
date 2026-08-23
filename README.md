# Robinhood Autonomous Trading Agent

An autonomous momentum-trading agent that runs Claude (via Claude Code in
headless mode) on a Linux server every 30 minutes during US market hours,
connected to Robinhood's official agentic MCP server.

**⚠️ This places real-money trades automatically.** Risk limits are enforced
in code (see Guardrails below), but you are responsible for everything it
does. Start small and watch it for the first few days.

## How it works

```
systemd timer (every 30 min, weekdays)
  └─ scripts/run-agent.sh          market-hours + kill-switch gate
       └─ claude -p prompts/trading-run.md      one headless agent run
            ├─ CLAUDE.md                        strategy mandate + rules
            ├─ .mcp.json                        Robinhood MCP server (OAuth)
            ├─ hooks/guardrails.py              HARD order caps (PreToolUse)
            └─ state/journal.md, ledger.json    memory between runs
```

- **Strategy:** intraday momentum on the tickers in `config/watchlist.txt`
  (edit freely). Buys are $25 fractional market orders; stop-loss −3%,
  take-profit +5%, max 4 positions. Details in `CLAUDE.md`.
- **Account:** only the agentic-enabled cash account listed in
  `config/limits.json`. Options and all other accounts are blocked.

## Guardrails (enforced in code, not just prompts)

`hooks/guardrails.py` runs as a Claude Code PreToolUse hook on every
`place_equity_order` call. An order is **rejected before it reaches
Robinhood** if it exceeds $25 (buy), would push today's buys past $100,
targets the wrong account, uses a disallowed order type, or if the kill
switch is set. Executed orders are recorded in `state/ledger.json`, which is
what the daily cap is computed from. Edit `config/limits.json` to change caps.

**Kill switch:** `touch state/HALT` stops all trading instantly (both the
runner and the hook check it). `rm state/HALT` resumes.

## Setup on a GCP Linux VM

### 1. Install prerequisites

```bash
sudo apt-get update && sudo apt-get install -y git python3 curl
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g @anthropic-ai/claude-code
```

### 2. Clone and prepare

```bash
git clone https://github.com/rjlipari21/robinhood.git ~/robinhood
cd ~/robinhood
chmod +x scripts/run-agent.sh hooks/guardrails.py
```

### 3. Authenticate Claude Code

Either set an API key (simplest for a server):

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > ~/robinhood/.env
chmod 600 ~/robinhood/.env
```

…or, to use a Claude subscription, run `claude setup-token` interactively
once and follow the prompts.

### 4. One-time Robinhood OAuth (over SSH)

The Robinhood MCP server uses OAuth with a `localhost` callback, so you
forward the callback port through SSH once:

1. On the VM: `cd ~/robinhood && claude`, then type `/mcp`, select
   `robinhood-trading`, and choose Authenticate. It prints an authorization
   URL — **don't open it yet**. Find the `redirect_uri=http://localhost:PORT`
   parameter in that URL and note the PORT.
2. On your local machine, open a second terminal:
   `gcloud compute ssh YOUR_VM -- -L PORT:localhost:PORT`
   (or `ssh -L PORT:localhost:PORT you@vm-ip`). Leave it open.
3. Open the authorization URL in your local browser, log in to Robinhood,
   approve. The redirect lands on `localhost:PORT`, tunnels to the VM, and
   Claude Code stores the tokens (refreshed automatically thereafter).
4. Back in the `claude` session, `/mcp` should now show the server as
   connected. Sanity check: ask "list my accounts" and confirm you see the
   Agentic account. Then exit.

### 5. Test one run manually

```bash
cd ~/robinhood && ./scripts/run-agent.sh
cat logs/run-$(date +%F).log
cat state/journal.md
```

Run this during market hours; outside them it exits with a "skipping" line.

### 6. Install the schedule

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

## Day-to-day

- **Watch it:** `tail -f ~/robinhood/logs/run-$(date +%F).log` and read
  `state/journal.md` — the agent writes a dated entry every run.
- **Pause:** `touch ~/robinhood/state/HALT` · **Resume:** `rm ~/robinhood/state/HALT`
- **Stop entirely:** `sudo systemctl disable --now "robinhood-agent@$USER.timer"`
- **Tune:** edit `config/watchlist.txt`, `config/limits.json`, or the strategy
  rules in `CLAUDE.md`. Changes take effect next run.

## Notes and caveats

- Cash account: the mandate forbids same-day round trips (except stop-losses)
  to avoid good-faith violations, and unsettled funds (T+1) naturally limit
  re-use of proceeds.
- Each run costs API/subscription tokens (~13 runs per trading day at the
  default cadence). Check your Claude usage after the first day.
- If OAuth tokens ever expire beyond refresh, repeat step 4.
