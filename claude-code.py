#!/usr/bin/env python3
"""Maki dynamic provider: reuse Claude Code OAuth credentials."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OAUTH_TOKEN_URL = "https://claude.ai/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
ANTHROPIC_BETA = (
    "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14"
)
SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
PRIMARY_SERVICE = "Claude Code-credentials"
EXPIRY_SKEW_MS = 60_000
ACCOUNT_STATE = Path.home() / ".local" / "state" / "maki" / "claude-code-account"


@dataclass
class ClaudeCredentials:
    access_token: str
    refresh_token: str
    expires_at: int
    subscription_type: str | None = None


@dataclass
class ClaudeAccount:
    label: str
    source: str
    credentials: ClaudeCredentials


def credentials_file_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    if base:
        return Path(base).expanduser() / ".credentials.json"
    return Path.home() / ".claude" / ".credentials.json"


def parse_credentials(raw: str) -> ClaudeCredentials | None:
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    data = parsed.get("claudeAiOauth", parsed)
    if not isinstance(data, dict):
        return None

    access = data.get("accessToken")
    refresh = data.get("refreshToken")
    expires = data.get("expiresAt")
    # MCP-only blobs have mcpOAuth but no user accessToken.
    if parsed.get("mcpOAuth") and not isinstance(access, str):
        return None
    if not (
        isinstance(access, str)
        and isinstance(refresh, str)
        and isinstance(expires, (int, float))
    ):
        return None

    sub = data.get("subscriptionType")
    return ClaudeCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_at=int(expires),
        subscription_type=sub if isinstance(sub, str) else None,
    )


def read_keychain_service(service_name: str) -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service_name, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise RuntimeError(f"Keychain read failed for {service_name}: {e}") from e

    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 44:
        return None  # not found
    if result.returncode == 36:
        raise RuntimeError(
            "macOS Keychain is locked. Unlock it or run: "
            "security unlock-keychain ~/Library/Keychains/login.keychain-db"
        )
    if result.returncode == 128:
        raise RuntimeError("Keychain access was denied.")
    return None


def list_claude_keychain_services() -> list[str]:
    try:
        dump = subprocess.run(
            ["security", "dump-keychain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return [PRIMARY_SERVICE]

    if dump.returncode != 0:
        return [PRIMARY_SERVICE]

    seen: set[str] = set()
    services: list[str] = []
    for m in re.finditer(r'"Claude Code-credentials(?:-[0-9a-f]+)?"', dump.stdout):
        svc = m.group(0)[1:-1]
        if svc not in seen:
            seen.add(svc)
            services.append(svc)

    ordered: list[str] = []
    if PRIMARY_SERVICE in seen:
        ordered.append(PRIMARY_SERVICE)
    for svc in services:
        if svc != PRIMARY_SERVICE:
            ordered.append(svc)
    return ordered or [PRIMARY_SERVICE]


def read_credentials_file() -> ClaudeCredentials | None:
    path = credentials_file_path()
    try:
        return parse_credentials(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def build_account_labels(creds_list: list[ClaudeCredentials]) -> list[str]:
    base_labels: list[str] = []
    for c in creds_list:
        if c.subscription_type:
            tier = c.subscription_type[:1].upper() + c.subscription_type[1:]
            base_labels.append(f"Claude {tier}")
        else:
            base_labels.append("Claude")

    counts: dict[str, int] = {}
    for label in base_labels:
        counts[label] = counts.get(label, 0) + 1

    seen: dict[str, int] = {}
    out: list[str] = []
    for base in base_labels:
        if counts[base] <= 1:
            out.append(base)
        else:
            n = seen.get(base, 0) + 1
            seen[base] = n
            out.append(f"{base} {n}")
    return out


def read_all_accounts() -> list[ClaudeAccount]:
    raw_accounts: list[tuple[str, ClaudeCredentials]] = []

    if sys.platform == "darwin":
        for svc in list_claude_keychain_services():
            try:
                raw = read_keychain_service(svc)
            except RuntimeError:
                continue
            if not raw:
                continue
            creds = parse_credentials(raw)
            if creds:
                raw_accounts.append((svc, creds))

    if not raw_accounts:
        creds = read_credentials_file()
        if creds:
            raw_accounts.append(("file", creds))

    if not raw_accounts:
        return []

    labels = build_account_labels([c for _, c in raw_accounts])
    return [
        ClaudeAccount(label=labels[i], source=src, credentials=creds)
        for i, (src, creds) in enumerate(raw_accounts)
    ]


def load_persisted_source() -> str | None:
    try:
        text = ACCOUNT_STATE.read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


def save_account_source(source: str) -> None:
    ACCOUNT_STATE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNT_STATE.write_text(source, encoding="utf-8")


def clear_account_source() -> None:
    try:
        ACCOUNT_STATE.unlink()
    except FileNotFoundError:
        pass


def select_account(accounts: list[ClaudeAccount]) -> ClaudeAccount | None:
    if not accounts:
        return None
    preferred = load_persisted_source()
    if preferred:
        for a in accounts:
            if a.source == preferred:
                return a
    return accounts[0]


def refresh_account_from_source(source: str) -> ClaudeCredentials | None:
    if source == "file":
        return read_credentials_file()
    raw = read_keychain_service(source)
    if not raw:
        return None
    return parse_credentials(raw)


def update_credential_blob(existing_json: str, creds: ClaudeCredentials) -> str | None:
    try:
        parsed: Any = json.loads(existing_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    target = parsed.get("claudeAiOauth")
    if not isinstance(target, dict):
        target = parsed
    target["accessToken"] = creds.access_token
    target["refreshToken"] = creds.refresh_token
    target["expiresAt"] = creds.expires_at
    return json.dumps(parsed)


def keychain_account_name(service_name: str) -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service_name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    m = re.search(r'"acct"<blob>="([^"]*)"', result.stdout + result.stderr)
    if m:
        return m.group(1)
    # Older security output variants
    m = re.search(r'"acct"\s*=\s*"([^"]*)"', result.stdout + result.stderr)
    return m.group(1) if m else None


def write_back_credentials(source: str, creds: ClaudeCredentials) -> bool:
    new = ClaudeCredentials(
        access_token=creds.access_token,
        refresh_token=creds.refresh_token,
        expires_at=creds.expires_at,
        subscription_type=creds.subscription_type,
    )

    if source == "file":
        path = credentials_file_path()
        try:
            raw = path.read_text(encoding="utf-8")
            updated = update_credential_blob(raw, new)
            if not updated:
                return False
            path.write_text(updated, encoding="utf-8")
            path.chmod(0o600)
            return True
        except OSError:
            return False

    if sys.platform == "darwin":
        try:
            raw = read_keychain_service(source)
            if not raw:
                return False
            updated = update_credential_blob(raw, new)
            if not updated:
                return False
            account = keychain_account_name(source) or source
            subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-s",
                    source,
                    "-a",
                    account,
                    "-w",
                    updated,
                    "-U",
                ],
                capture_output=True,
                timeout=5,
                check=True,
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError):
            return False

    return False


def parse_oauth_response(
    raw: str, current_refresh_token: str, now_ms: int | None = None
) -> ClaudeCredentials | None:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        return None
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in", 36_000)
    if not isinstance(expires_in, (int, float)):
        expires_in = 36_000
    return ClaudeCredentials(
        access_token=access,
        refresh_token=refresh if isinstance(refresh, str) else current_refresh_token,
        expires_at=now + int(expires_in) * 1000,
    )


def refresh_via_oauth(refresh_token: str) -> ClaudeCredentials | None:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": OAUTH_CLIENT_ID,
            "refresh_token": refresh_token,
        }
    ).encode()
    req = urllib.request.Request(
        OAUTH_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    return parse_oauth_response(raw, refresh_token)


def refresh_via_cli() -> None:
    try:
        subprocess.run(
            ["claude", "-p", ".", "--model", "haiku"],
            cwd=tempfile.gettempdir(),
            env={**os.environ, "TERM": "dumb"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass


def ensure_fresh(
    account: ClaudeAccount, *, force: bool = False
) -> ClaudeCredentials | None:
    # Pick up external updates (Claude CLI / other tools).
    on_disk = refresh_account_from_source(account.source)
    if on_disk:
        account.credentials = on_disk

    creds = account.credentials
    now = int(time.time() * 1000)
    if not force and creds.expires_at > now + EXPIRY_SKEW_MS:
        return creds

    if creds.refresh_token:
        oauth = refresh_via_oauth(creds.refresh_token)
        if oauth and oauth.expires_at > now + EXPIRY_SKEW_MS:
            account.credentials = oauth
            write_back_credentials(account.source, oauth)
            return oauth

    refresh_via_cli()
    refreshed = refresh_account_from_source(account.source)
    if refreshed and refreshed.expires_at > now + EXPIRY_SKEW_MS:
        account.credentials = refreshed
        return refreshed
    return None


def auth_json(creds: ClaudeCredentials) -> dict[str, Any]:
    return {
        "headers": {
            "authorization": f"Bearer {creds.access_token}",
            "anthropic-beta": ANTHROPIC_BETA,
        }
    }


def cmd_info() -> int:
    accounts = read_all_accounts()
    print(
        json.dumps(
            {
                "display_name": "Claude Code",
                "base": "anthropic",
                "has_auth": bool(accounts),
                "system_prefix": SYSTEM_PREFIX,
            }
        )
    )
    return 0


def cmd_resolve(*, force: bool = False) -> int:
    accounts = read_all_accounts()
    account = select_account(accounts)
    if not account:
        print(
            "No Claude Code OAuth credentials found. Run `claude` and log in first.",
            file=sys.stderr,
        )
        return 1
    fresh = ensure_fresh(account, force=force)
    if not fresh:
        print(
            "Claude Code token expired and refresh failed. Re-authenticate with `claude`.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(auth_json(fresh)))
    return 0


def cmd_login() -> int:
    accounts = read_all_accounts()
    if not accounts:
        print(
            "No Claude Code OAuth credentials found.\n"
            "Run `claude` and complete login, then retry `maki auth login claude-code`.",
            file=sys.stderr,
        )
        return 1

    if len(accounts) == 1:
        save_account_source(accounts[0].source)
        print(f"Using {accounts[0].label} ({accounts[0].source})")
        return 0

    print("Multiple Claude Code accounts found:")
    for i, a in enumerate(accounts, 1):
        print(f"  {i}) {a.label}  [{a.source}]")
    while True:
        try:
            choice = input("Select account number: ").strip()
        except EOFError:
            return 1
        if choice.isdigit() and 1 <= int(choice) <= len(accounts):
            picked = accounts[int(choice) - 1]
            save_account_source(picked.source)
            print(f"Using {picked.label} ({picked.source})")
            return 0
        print("Invalid selection.", file=sys.stderr)


def cmd_logout() -> int:
    clear_account_source()
    print(
        "Cleared maki account selection. Claude Code credentials were left untouched."
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: claude-code <info|resolve|refresh|login|logout>",
            file=sys.stderr,
        )
        return 2
    cmd = argv[1]
    if cmd == "info":
        return cmd_info()
    if cmd == "resolve":
        return cmd_resolve(force=False)
    if cmd == "refresh":
        return cmd_resolve(force=True)
    if cmd == "login":
        return cmd_login()
    if cmd == "logout":
        return cmd_logout()
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
