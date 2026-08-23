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

## Notes

- The MCP server URL requires no API keys in this repo; all credentials
  live in the OAuth tokens Claude Code stores locally on the VM after
  step 3. Nothing sensitive is committed here.
- Trading tools can place real orders. Consider keeping sessions in a
  permission mode that prompts before order-placing tool calls.
