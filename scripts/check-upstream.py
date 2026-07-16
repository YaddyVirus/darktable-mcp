#!/usr/bin/env python3
"""Check for new upstream releases and notify the user.

Designed to be run by the mavis cron tool weekly.  It:

  1. Fetches the latest release from YaddyVirus/darktable-mcp via the
     GitHub API (no auth, 60 req/h limit).
  2. Compares tag_name against a local state file in $DARKTABLE_MCP_STATE_DIR
     (default ``~/.cache/darktable-mcp/upstream-state.json``).
  3. If different, prints a notification block the cron can relay to
     the user, and updates the state file.

Exit code 0 = no new release, 10 = new release detected.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

UPSTREAM_REPO = "YaddyVirus/darktable-mcp"
API_URL = f"https://api.github.com/repos/{UPSTREAM_REPO}/releases/latest"
STATE_DIR = Path(
    os.environ.get("DARKTABLE_MCP_STATE_DIR", Path.home() / ".cache" / "darktable-mcp")
)
STATE_FILE = STATE_DIR / "upstream-state.json"


def fetch_latest() -> dict:
    """Fetch the latest release JSON from GitHub, or raise on failure."""
    req = urllib.request.Request(
        API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "darktable-mcp-cron"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main() -> int:
    try:
        latest = fetch_latest()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # No releases yet — silent skip
            return 0
        print(f"upstream-check: GitHub API error {e.code}: {e.reason}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"upstream-check: network error: {e}", file=sys.stderr)
        return 1

    tag = latest.get("tag_name", "")
    if not tag:
        print("upstream-check: no tag_name in response", file=sys.stderr)
        return 1

    state = load_state()
    last_seen = state.get("tag_name")

    if last_seen == tag:
        # No new release
        return 0

    # New release!
    save_state({
        "tag_name": tag,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    })

    name = latest.get("name") or tag
    url = latest.get("html_url", f"https://github.com/{UPSTREAM_REPO}/releases/tag/{tag}")
    published = latest.get("published_at", "")[:10]  # YYYY-MM-DD
    body = (latest.get("body") or "").strip()

    print(f"🆕 New upstream release: {name}")
    print(f"   Tag: {tag}  •  Published: {published}")
    print(f"   {url}")
    if last_seen:
        print(f"   Previous: {last_seen}")
    if body:
        # First paragraph of the release notes
        first_para = body.split("\n\n", 1)[0]
        if len(first_para) > 400:
            first_para = first_para[:397] + "..."
        print(f"\n   {first_para}")
    print(f"\n   Cherry-pick with:  ./scripts/sync-upstream.sh pull")
    return 10


if __name__ == "__main__":
    sys.exit(main())
