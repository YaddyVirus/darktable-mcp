"""Tests for scripts/check-upstream.py."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check-upstream.py"


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch):
    """Point the state file at a tmp dir so we don't touch the real one."""
    monkeypatch.setenv("DARKTABLE_MCP_STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def script_module():
    """Import the script as a module so we can patch its urlopen ref."""
    # Use a sanitised module name (no hyphens) so importlib can find it
    spec = importlib.util.spec_from_file_location("check_upstream_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_release(tag: str, name: str = "", body: str = "", published: str = "2026-07-10") -> dict:
    return {
        "tag_name": tag,
        "name": name or tag,
        "html_url": f"https://github.com/YaddyVirus/darktable-mcp/releases/tag/{tag}",
        "published_at": published + "T12:00:00Z",
        "body": body or "Some release notes.\n\nMore details here.",
    }


def _run_main(monkeypatch, side_effect_or_return):
    """Patch urlopen on the imported module's namespace and run main()."""
    if isinstance(side_effect_or_return, BaseException):
        with patch("check_upstream.urllib.request.urlopen",
                   side_effect=side_effect_or_return):
            return script_module.main()
    else:
        # side_effect_or_return is a JSON string
        with patch("check_upstream.urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = side_effect_or_return.encode()
            return script_module.main()


def test_no_release_exits_0(monkeypatch, isolated_state, script_module):
    """When the state file is empty AND no release exists, exit 0 (or 10 if a release
    exists but state is fresh — both are fine; we just don't crash)."""
    import email.message
    import urllib.error
    hdrs = email.message.Message()
    with patch.object(script_module.urllib.request, "urlopen",
                      side_effect=urllib.error.HTTPError("url", 404, "Not Found", hdrs, None)):
        rc = script_module.main()
    assert rc == 0


def test_new_release_detected(isolated_state, script_module):
    """When the latest tag differs from the stored one, exit 10 with notification."""
    fake = _fake_release("v1.2.3", name="Awesome release",
                         body="Adds cool features.\n\nDetailed notes.")
    payload = json.dumps(fake)
    with patch.object(script_module.urllib.request, "urlopen") as mock_urlopen:
        mock_resp = mock_urlopen.return_value.__enter__.return_value
        mock_resp.read.return_value = payload.encode()
        rc = script_module.main()
    assert rc == 10
    # State file should be updated
    state = json.loads((isolated_state / "upstream-state.json").read_text())
    assert state["tag_name"] == "v1.2.3"


def test_no_new_release_exits_0(isolated_state, script_module, capsys):
    """When the latest tag matches stored, exit 0 silently."""
    (isolated_state / "upstream-state.json").write_text(
        json.dumps({"tag_name": "v1.2.3"})
    )
    payload = json.dumps(_fake_release("v1.2.3"))
    with patch.object(script_module.urllib.request, "urlopen") as mock_urlopen:
        mock_resp = mock_urlopen.return_value.__enter__.return_value
        mock_resp.read.return_value = payload.encode()
        rc = script_module.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "🆕" not in out


def test_404_silently_returns_0(isolated_state, script_module):
    """When the repo has no releases (404), don't error."""
    import email.message
    import urllib.error
    hdrs = email.message.Message()
    with patch.object(script_module.urllib.request, "urlopen",
                      side_effect=urllib.error.HTTPError("url", 404, "Not Found", hdrs, None)):
        rc = script_module.main()
    assert rc == 0


def test_network_error_returns_1(isolated_state, script_module, capsys):
    import urllib.error
    with patch.object(script_module.urllib.request, "urlopen",
                      side_effect=urllib.error.URLError("connection refused")):
        rc = script_module.main()
    assert rc == 1
    assert "network error" in capsys.readouterr().err


def test_release_body_truncation(isolated_state, script_module, capsys):
    """Long release body is truncated to 397 chars + ellipsis."""
    long_body = "X" * 1000
    fake = _fake_release("v9.9.9", name="Big release", body=long_body)
    with patch.object(script_module.urllib.request, "urlopen") as mock_urlopen:
        mock_resp = mock_urlopen.return_value.__enter__.return_value
        mock_resp.read.return_value = json.dumps(fake).encode()
        script_module.main()
    out = capsys.readouterr().out
    # 397 X's + "..." = exactly 400 chars of body shown
    assert "X" * 397 + "..." in out
    # The raw 1000-char body must NOT appear in full
    assert "X" * 500 not in out
