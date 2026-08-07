# maki-claude-oauth

Maki [dynamic provider](https://maki.sh/docs/providers/#dynamic-providers) that reuses your existing **Claude Code** OAuth session — no separate Anthropic API key or browser login.

Inspired by [pi-claude-auth](https://github.com/pankajudhas81/pi-claude-auth).

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) installed and authenticated (`claude` at least once)
- [Maki](https://maki.sh) installed
- Python 3

On macOS, credentials come from the Keychain entry `Claude Code-credentials` (and any `Claude Code-credentials-*` account variants). On Linux, `~/.claude/.credentials.json` is used.

## Install

No clone required:

```bash
curl -fsSL https://raw.githubusercontent.com/arxdeus/maki-claude-oauth/main/install.sh | bash
```

Or from a local checkout: `./install.sh`.

Both install to `~/.config/maki/providers/claude-code`. Override the download base with `MAKI_CLAUDE_OAUTH_RAW` if needed.

## Usage

1. Start a new Maki session (`/new` or restart).
2. Open `/model` and select a model under `claude-code/...` (catalog inherited from Anthropic).
3. If you have multiple Claude Code accounts:

```bash
maki auth login claude-code
```

`logout` only clears Maki’s account selection; it does not remove Claude Code credentials.

## How it works

- Reads Claude Code OAuth tokens from Keychain or `~/.claude/.credentials.json`
- Refreshes via Anthropic’s OAuth endpoint when near expiry (Claude CLI fallback)
- Writes rotated tokens back so Claude Code and Maki stay in sync
- Sends Claude Code `system_prefix` and `anthropic-beta` headers on resolve

This provider does **not** inject Claude Code’s request-body billing header. Subscription vs API-credit billing may differ from the official Claude Code CLI.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| No Claude Code credentials found | Run `claude` and complete login |
| Keychain locked / access denied | Unlock Keychain or grant access when prompted |
| Token expired and refresh failed | Re-authenticate with `claude` |
| Provider not listed | Confirm `~/.config/maki/providers/claude-code` is executable and that `~/.config/maki/providers/claude-code info` prints JSON; start a new session |
| `unknown provider 'claude-code'` on Termux | Termux has no `/usr/bin/env`, so the shebang cannot resolve. Run `pkg install python`, then re-run `install.sh` — it pins the shebang to the real interpreter |

## Disclaimer

This uses Claude Code OAuth credentials with a third-party client. Anthropic’s Terms of Service may restrict subscription tokens to official clients. This is a community workaround and may break if Anthropic changes OAuth. Use at your own discretion.

## License

MIT
