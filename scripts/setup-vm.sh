#!/usr/bin/env bash
# Run this ON the GCP VM to install everything needed for the Robinhood
# trading integration: git, Node.js 20, and the Claude Code CLI, then clone
# this repo so its .mcp.json (Robinhood trading MCP server) is picked up.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/rjlipari21/robinhood.git}"
REPO_DIR="${REPO_DIR:-$HOME/robinhood}"

# --- System packages ---
sudo apt-get update -y
sudo apt-get install -y git curl ca-certificates

# --- Node.js 20 (required by Claude Code CLI) ---
if ! command -v node >/dev/null 2>&1 || [ "$(node -v | cut -c2-3)" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

# --- Claude Code CLI ---
if ! command -v claude >/dev/null 2>&1; then
  sudo npm install -g @anthropic-ai/claude-code
fi

# --- Clone the repo (contains .mcp.json with the Robinhood MCP server) ---
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone "$REPO_URL" "$REPO_DIR"
fi

echo
echo "Setup complete. Next steps (interactive, one time only):"
echo "  1. cd $REPO_DIR"
echo "  2. claude            # log in to your Anthropic account when prompted"
echo "  3. Inside Claude Code, run /mcp and authorize 'robinhood-trading'"
echo "     (opens a Robinhood OAuth flow; complete it in your browser)."
echo
echo "After that, Claude Code on this VM can use the Robinhood trading tools."
