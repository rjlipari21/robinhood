# Robinhood Trading Integration on GCP

This repo wires the [Robinhood trading MCP server](https://agent.robinhood.com/mcp/trading)
into Claude Code, and includes scripts to run the whole thing on a GCP
Compute Engine VM.

## What's here

| File | Purpose |
| --- | --- |
| `.mcp.json` | Project-level MCP config: registers the `robinhood-trading` HTTP server so Claude Code sessions in this repo get Robinhood trading tools (quotes, positions, orders, watchlists, scans). |
| `scripts/create-vm.sh` | Creates a small Debian 12 VM (`e2-small`) in your GCP project using `gcloud`. |
| `scripts/setup-vm.sh` | Run on the VM: installs git, Node.js 20, and the Claude Code CLI, then clones this repo. |
| `scripts/sync-vm.sh` | Pulls the latest strategy (`TRADING_PARAMETERS.md`, scan configs) from the active branch. Safe to run anytime. |
| `scripts/install-sync-cron.sh` | One-time setup: installs a cron job that runs `sync-vm.sh` every 5 minutes, so the VM's checkout stays current automatically. |
| `TRADING_PARAMETERS.md` | The live strategy spec (universe, position sizing, entry/exit rules, saved scan IDs). This is what changes as the strategy evolves — sync it to any machine that needs the current rules. |

## Setup

### 1. Create the VM (from your laptop)

Requires the `gcloud` CLI, authenticated against your project:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
./scripts/create-vm.sh          # override VM_NAME / ZONE / MACHINE_TYPE via env vars
```

If you already have a GCP machine, skip this step.

### 2. Provision the VM

```bash
gcloud compute ssh robinhood-trader --zone=us-central1-a
# then, on the VM:
curl -fsSL https://raw.githubusercontent.com/rjlipari21/robinhood/main/scripts/setup-vm.sh | bash
```

(Or clone the repo first and run `scripts/setup-vm.sh` from it.)

### 3. Authorize (interactive, one time)

Two logins have to happen in an interactive terminal on the VM — they
cannot be scripted:

1. **Anthropic**: run `claude` inside the repo directory and follow the
   login prompt.
2. **Robinhood**: inside Claude Code, run `/mcp`, select
   `robinhood-trading`, and complete the OAuth flow in your browser.
   Until this is done, the Robinhood tools are unavailable.

If you use the integration through claude.ai instead of the CLI, authorize
the Robinhood connector under **claude.ai → Settings → Connectors**.

### 4. Verify

In a Claude Code session on the VM, ask for something read-only, e.g.
"show my portfolio" or "quote AAPL" — if the Robinhood tools respond,
the integration is working.

### 5. Keep an existing VM in sync

If you already have a VM provisioned and just want its copy of the strategy
to stay current with what this trading session is using:

```bash
# on the VM, one time:
cd ~/robinhood   # or wherever it's cloned
git pull origin claude/robinhood-gcp-integration-banfc3   # make sure you're tracking the right branch/clone first
chmod +x scripts/*.sh
./scripts/install-sync-cron.sh
```

This installs a cron job that runs `git pull` every 5 minutes, so
`TRADING_PARAMETERS.md` and the saved scan IDs on the VM always match what
gets committed here. Check `~/robinhood-sync.log` on the VM to confirm it's
running. This only syncs files — it does **not** make the VM start trading
on its own; the live trading loop runs in this Claude session. If you want
a Claude Code session on the VM to actually act on the synced file, point
it at `TRADING_PARAMETERS.md` explicitly each time you start one there.

## Notes

- The MCP server URL requires no API keys in this repo; all credentials
  live in the OAuth tokens Claude Code stores locally on the VM after
  step 3. Nothing sensitive is committed here.
- Trading tools can place real orders. Consider keeping sessions in a
  permission mode that prompts before order-placing tool calls.
