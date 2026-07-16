"""Native async tests for the tools that became async in v0.4.

Uses ``pytest-asyncio`` to drive the event loop directly instead of
``asyncio.run()`` — these give us a tighter assertion surface on the
async code (cancellation, concurrency, exception propagation).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image

from darktable_mcp import edits, server

pytestmark = pytest.mark.asyncio


async def test_export_image_async_returns_dict(tmp_path: Path, monkeypatch):
    """Async export returns a plain dict (not a coroutine)."""
    monkeypatch.setattr("darktable_mcp.server.find_darktable_cli", lambda: None)
    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli", lambda: None)
    src = tmp_path / "x.jpg"
    Image.new("RGB", (50, 50), (200, 100, 50)).save(src)
    server.apply_adjustments(str(src), exposure_ev=0.3)

    r = await server.export_image(str(src), format="jpeg", quality=85)
    assert r["status"] == "ok", r
    assert r["rendered_via"] == "rawpy+pillow"
    assert Path(r["output_path"]).exists()


async def test_get_image_preview_async_returns_list(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("darktable_mcp.server.find_darktable_cli", lambda: None)
    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli", lambda: None)
    src = tmp_path / "x.jpg"
    Image.new("RGB", (100, 100), (128, 64, 32)).save(src)

    r = await server.get_image_preview(str(src), max_size=200)
    assert isinstance(r, list)
    # First item is a status message, second is the Image
    assert len(r) == 2
    # Preview sidecar was saved
    preview = src.with_name(src.stem + "__preview.jpg")
    assert preview.exists()


async def test_apply_settings_to_folder_async_runs_concurrently(
    tmp_path: Path, monkeypatch
):
    """When max_concurrent=4 and we have 8 files, calls must run in
    parallel-ish fashion (verified by tracking peak in-flight)."""
    import threading
    # Force sync sidecar saves (no darktable) so we test the I/O path
    monkeypatch.setattr("darktable_mcp.server.find_darktable_cli", lambda: None)
    monkeypatch.setattr("darktable_mcp.processor.find_darktable_cli", lambda: None)

    # Source with edits
    src = tmp_path / "source.jpg"
    Image.new("RGB", (50, 50), (128, 64, 32)).save(src)
    server.apply_adjustments(str(src), exposure_ev=0.5)

    # 8 targets
    target = tmp_path / "dest"
    target.mkdir()
    for i in range(8):
        Image.new("RGB", (50, 50), (i * 10, i * 5, 200)).save(target / f"img_{i:02d}.jpg")

    in_flight = 0
    peak = 0
    lock = threading.Lock()
    real_save = server._save_sidecar

    def slow_save(state):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            import time
            time.sleep(0.05)  # simulate slow I/O
            return real_save(state)
        finally:
            with lock:
                in_flight -= 1

    monkeypatch.setattr(server, "_save_sidecar", slow_save)
    r = await server.apply_settings_to_folder(
        str(src), str(target), max_concurrent=4
    )
    assert r["copied_count"] == 8
    # Peak should not exceed max_concurrent
    assert peak <= 4, f"peak concurrent was {peak}, expected <= 4"
    # And actually parallelize (peak > 1)
    assert peak >= 2, f"expected some concurrency, peak was {peak}"


async def test_apply_settings_to_folder_propagates_exceptions_as_failures(
    tmp_path: Path, monkeypatch
):
    """If a sidecar write raises, the file goes to 'failed', not crashing
    the whole batch."""
    src = tmp_path / "source.jpg"
    Image.new("RGB", (50, 50), (128, 64, 32)).save(src)
    server.apply_adjustments(str(src), exposure_ev=0.5)

    target = tmp_path / "dest"
    target.mkdir()
    for i in range(3):
        Image.new("RGB", (50, 50), (i * 10, 0, 0)).save(target / f"img_{i:02d}.jpg")

    real_save = server._save_sidecar

    def selective_save(state):
        if "img_01" in str(state.source_path):
            raise OSError("simulated disk error")
        return real_save(state)

    monkeypatch.setattr(server, "_save_sidecar", selective_save)
    r = await server.apply_settings_to_folder(str(src), str(target))

    assert r["copied_count"] == 2
    assert r["failed_count"] == 1
    assert r["status"] == "partial"
    failed_paths = {f["file"] for f in r["failed"]}
    assert any("img_01" in p for p in failed_paths)
