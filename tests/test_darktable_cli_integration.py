"""Integration tests for the darktable-cli code paths.

We mock the async subprocess invocation (``asyncio.create_subprocess_exec``)
and ``find_darktable_cli`` discovery so the tests exercise the full
control flow (XMP writing, command construction, output path handling,
library-dir cleanup) without requiring a real darktable install in CI.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from darktable_mcp import edits, server
from darktable_mcp.processor import (
    _arun_darktable_cli,
    _run_darktable_cli,
    find_darktable_cli,
)


def _call_async(coro_or_value):
    """Run an async tool from a sync test."""
    if asyncio.iscoroutine(coro_or_value):
        return asyncio.run(coro_or_value)
    return coro_or_value


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_darktable(tmp_path: Path):
    """Create a fake ``darktable-cli`` script that writes a tiny PNG.

    The output destination is the LAST positional argument.  Flag values
    (like the ``0`` after ``--width``) are technically not positionals in
    real darktable-cli, but for the fake we work around it by walking
    the argv and skipping any token that follows a known flag.
    """
    bin_path = tmp_path / "darktable-cli"
    bin_path.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "SKIP_NEXT = {'--width', '--height', '--quality', '--bpp',\n"
        "             '--library', '--out-ext', '--core', '--configdir'}\n"
        "positionals = []\n"
        "i = 1\n"
        "while i < len(sys.argv):\n"
        "    a = sys.argv[i]\n"
        "    if a in SKIP_NEXT:\n"
        "        i += 2  # skip flag + its value\n"
        "        continue\n"
        "    if a.startswith('--'):\n"
        "        i += 1  # unknown flag, skip just it\n"
        "        continue\n"
        "    positionals.append(a)\n"
        "    i += 1\n"
        "dst = positionals[-1] if positionals else ''\n"
        "if not dst:\n"
        "    sys.exit(2)\n"
        "os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)\n"
        "try:\n"
        "    from PIL import Image\n"
        "    Image.new('RGB', (4, 4), (255, 0, 0)).save(dst)\n"
        "except Exception:\n"
        "    with open(dst, 'wb') as f:\n"
        "        f.write(b'fake-output')\n"
        "sys.exit(0)\n"
    )
    bin_path.chmod(0o755)
    return bin_path


@pytest.fixture
def patched_darktable(fake_darktable, monkeypatch):
    """Make ``find_darktable_cli`` return the fake script path."""
    monkeypatch.setattr(
        "darktable_mcp.processor.find_darktable_cli", lambda: str(fake_darktable)
    )
    monkeypatch.setattr(
        "darktable_mcp.server.find_darktable_cli", lambda: str(fake_darktable)
    )


@pytest.fixture
def sample_image_with_edits(tmp_path: Path) -> Path:
    src = tmp_path / "DSC_0001.NEF"
    # The processor checks the suffix but never opens the file when the
    # darktable path is used; we just need a file to exist on disk.
    src.write_bytes(b"FAKE_RAW")
    r = server.apply_adjustments(str(src), exposure_ev=0.5, contrast=20,
                                  temperature_kelvin=4500)
    assert r["status"] == "ok"
    return src


# ---------------------------------------------------------------------------
# _run_darktable_cli
# ---------------------------------------------------------------------------

def test_run_darktable_cli_writes_output(tmp_path: Path, patched_darktable):
    src = tmp_path / "in.NEF"
    src.write_bytes(b"x")
    dst = tmp_path / "out.png"
    res = _run_darktable_cli(src, None, dst, out_ext="png")
    assert res["status"] == "ok", res
    assert dst.exists()
    assert res["size_bytes"] > 0


def test_run_darktable_cli_passes_xmp(tmp_path: Path, fake_darktable, monkeypatch):
    captured: dict = {}

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        # Pretend success by writing the dst file ourselves
        SKIP = {'--width', '--height', '--quality', '--bpp',
                '--library', '--out-ext', '--core', '--configdir'}
        positionals = []
        i = 0
        while i < len(cmd):
            a = cmd[i]
            if a in SKIP:
                i += 2
                continue
            if a.startswith('--'):
                i += 1
                continue
            positionals.append(a)
            i += 1
        dst = Path(positionals[-1])
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"x")
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli",
                        lambda: str(fake_darktable))
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    src = tmp_path / "a.NEF"
    src.write_bytes(b"x")
    xmp = tmp_path / "a.xmp"
    xmp.write_text("<x:xmpmeta/>")
    dst = tmp_path / "out.jpg"

    res = _run_darktable_cli(src, xmp, dst, width=800, height=600, quality=92)
    assert res["status"] == "ok"
    cmd = captured["cmd"]
    assert cmd[0] == str(fake_darktable)
    assert cmd[1] == str(src)
    assert cmd[2] == str(xmp)
    # dst comes after xmp, then flags
    assert str(dst) in cmd
    # Width, height, quality flags must be present
    assert "--width" in cmd and "800" in cmd
    assert "--height" in cmd and "600" in cmd
    assert "--quality" in cmd and "92" in cmd
    # Library should be a fresh tempdir each call
    assert "--library" in cmd
    libdir = Path(cmd[cmd.index("--library") + 1])
    assert libdir.name.startswith("dtcli_")


def test_run_darktable_cli_cleans_library_dir(tmp_path: Path, fake_darktable, monkeypatch):
    """The temp --library dir must be removed after the call, success or fail."""
    captured_libdirs: list[Path] = []

    async def fake_exec(*cmd, **kwargs):
        libdir = Path(cmd[list(cmd).index("--library") + 1])
        captured_libdirs.append(libdir)
        SKIP = {'--width', '--height', '--quality', '--bpp',
                '--library', '--out-ext', '--core', '--configdir'}
        positionals = []
        i = 0
        while i < len(cmd):
            a = cmd[i]
            if a in SKIP:
                i += 2
                continue
            if a.startswith('--'):
                i += 1
                continue
            positionals.append(a)
            i += 1
        dst = Path(positionals[-1])
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"x")
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli",
                        lambda: str(fake_darktable))
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    src = tmp_path / "a.NEF"
    src.write_bytes(b"x")
    dst = tmp_path / "out.jpg"
    res = _run_darktable_cli(src, None, dst)
    assert res["status"] == "ok"

    # The library dir existed during the call...
    assert len(captured_libdirs) == 1
    # ...and was cleaned afterwards.
    assert not captured_libdirs[0].exists(), "library dir was not cleaned"


def test_run_darktable_cli_returns_error_on_nonzero_exit(
    tmp_path: Path, fake_darktable, monkeypatch
):
    async def fake_exec(*cmd, **kwargs):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b"module 'foo' not found"))
        proc.returncode = 1
        return proc

    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli",
                        lambda: str(fake_darktable))
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    res = _run_darktable_cli(tmp_path / "a.NEF", None, tmp_path / "out.jpg")
    assert res["status"] == "error"
    assert "module 'foo' not found" in res["reason"]


def test_run_darktable_cli_handles_timeout(
    tmp_path: Path, fake_darktable, monkeypatch
):
    async def fake_exec(*cmd, **kwargs):
        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        proc.kill = MagicMock()
        return proc

    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli",
                        lambda: str(fake_darktable))
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    res = _run_darktable_cli(tmp_path / "a.NEF", None, tmp_path / "out.jpg",
                              timeout=10)
    assert res["status"] == "error"
    assert "timed out" in res["reason"]


def test_run_darktable_cli_returns_error_when_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli", lambda: None)
    res = _run_darktable_cli(tmp_path / "a.NEF", None, tmp_path / "out.jpg")
    assert res["status"] == "error"
    assert "not found" in res["reason"]


# ---------------------------------------------------------------------------
# ImageProcessor.process() with darktable-cli
# ---------------------------------------------------------------------------

def test_process_routes_to_darktable_cli_when_available(
    tmp_path: Path, sample_image_with_edits, patched_darktable
):
    from darktable_mcp.processor import ImageProcessor
    proc = ImageProcessor(sample_image_with_edits)
    state = edits.EditState.load(sample_image_with_edits)
    assert state is not None
    img = proc.process(state, preview_size=200, use_darktable_cli=True)
    assert isinstance(img, Image.Image)
    # Our fake darktable-cli writes a 4x4 PNG → after thumbnail it'd be 4x3
    # (aspect 4:4 = 1:1, max edge 200 → 200x200).  We just check it ran.
    assert img.size[0] > 0 and img.size[1] > 0


def test_process_falls_back_when_darktable_cli_unavailable(
    tmp_path: Path, sample_image_with_edits, monkeypatch
):
    """Without darktable-cli the processor must try the rawpy path, which
    in turn will surface a clear error for non-decodable files.
    """
    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli", lambda: None)
    from darktable_mcp.processor import ImageProcessor
    proc = ImageProcessor(sample_image_with_edits)
    state = edits.EditState.load(sample_image_with_edits)
    assert state is not None
    # Fake RAW bytes can't be decoded by rawpy — the call must surface
    # the underlying error rather than silently produce garbage.
    with pytest.raises(Exception):
        proc.process(state, use_darktable_cli=True)


# ---------------------------------------------------------------------------
# export_image end-to-end with darktable-cli
# ---------------------------------------------------------------------------

def test_export_image_uses_darktable_cli_when_available(
    tmp_path: Path, sample_image_with_edits, patched_darktable
):
    r = _call_async(server.export_image(str(sample_image_with_edits), format="jpeg", quality=90))
    assert r["status"] == "ok", r
    assert r["rendered_via"] == "darktable-cli"
    assert Path(r["output_path"]).exists()
    assert r["xmp_sidecar"] is not None
    assert Path(r["xmp_sidecar"]).exists()


def test_export_image_falls_back_to_pillow_for_raster(
    tmp_path: Path, monkeypatch
):
    """For JPEG/PNG without darktable-cli, Pillow fallback must still work."""
    # Force 'no darktable'
    monkeypatch.setattr("darktable_mcp.server.find_darktable_cli", lambda: None)
    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli", lambda: None)

    src = tmp_path / "a.jpg"
    Image.new("RGB", (100, 100), (200, 100, 50)).save(src)
    server.apply_adjustments(str(src), exposure_ev=0.3)

    r = _call_async(server.export_image(str(src), format="jpeg", quality=85))
    assert r["status"] == "ok", r
    assert r["rendered_via"] == "rawpy+pillow"
    assert Path(r["output_path"]).exists()


def test_export_raw_without_darktable_or_rawpy_raises_clear_error(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("darktable_mcp.server.find_darktable_cli", lambda: None)
    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli", lambda: None)
    # Hide rawpy so the fallback also fails
    import builtins
    orig_import = builtins.__import__
    def fake_import(name, *a, **kw):
        if name == "rawpy":
            raise ImportError("simulated: rawpy not installed")
        return orig_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    src = tmp_path / "IMG.CR2"
    src.write_bytes(b"x")
    r = _call_async(server.export_image(str(src)))
    assert r["status"] == "error"
    assert "darktable-cli" in r["reason"] or "rawpy" in r["reason"]
