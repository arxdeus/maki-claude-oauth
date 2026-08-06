#!/usr/bin/env bash
# Install the Claude Code dynamic provider for Maki.
#
#   curl -fsSL https://raw.githubusercontent.com/arxdeus/maki-claude-oauth/main/install.sh | bash
#
# Or from a local clone: ./install.sh
set -euo pipefail

REPO_RAW="${MAKI_CLAUDE_OAUTH_RAW:-https://raw.githubusercontent.com/arxdeus/maki-claude-oauth/main}"
DEST_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/maki/providers"
DEST="$DEST_DIR/claude-code"

# Prefer sibling file when run from a clone; otherwise fetch from GitHub.
SRC=""
if [[ "${BASH_SOURCE[0]:-}" != */dev/fd/* && "${BASH_SOURCE[0]:-}" != /dev/fd/* && -n "${BASH_SOURCE[0]:-}" ]]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$ROOT/claude-code.py" ]]; then
    SRC="$ROOT/claude-code.py"
  fi
fi

mkdir -p "$DEST_DIR"

if [[ -n "$SRC" ]]; then
  cp "$SRC" "$DEST"
else
  echo "Downloading claude-code.py from $REPO_RAW ..."
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$REPO_RAW/claude-code.py" -o "$tmp"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$tmp" "$REPO_RAW/claude-code.py"
  else
    echo "error: need curl or wget to download the provider" >&2
    exit 1
  fi
  # shebang must stay first line
  if ! head -1 "$tmp" | grep -q '^#!'; then
    echo "error: downloaded file does not look like the provider script" >&2
    exit 1
  fi
  cp "$tmp" "$DEST"
fi

chmod +x "$DEST"

echo "Installed: $DEST"
echo
echo "Next steps:"
echo "  1. Start a new Maki session (/new or restart maki)."
echo "  2. Open /model and pick a model under claude-code/..."
echo "  3. Multiple Claude Code accounts? Run: maki auth login claude-code"
