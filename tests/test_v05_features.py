"""Tests for v0.5 features: HSL grading, tone curve, undo/redo, compare."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from darktable_mcp import edits, server
from darktable_mcp.processor import (
    _apply_hsl_grading,
    _build_tone_curve_lut,
    _hsl_to_rgb,
    _rgb_to_hsl,
)


# ---------------------------------------------------------------------------
# HSL grading
# ---------------------------------------------------------------------------

def test_rgb_hsl_roundtrip_preserves_pixel_values():
    """Encoding RGB → HSL → RGB and back should be near-lossless for
    low-saturation patches where the hue is well-defined."""
    arr = np.array([
        [[255, 0, 0], [0, 255, 0], [0, 0, 255], [128, 128, 128]],
        [[200, 100, 50], [50, 200, 100], [100, 50, 200], [10, 10, 10]],
    ], dtype=np.uint8)
    h, s, l = _rgb_to_hsl(arr)
    back = _hsl_to_rgb(h, s, l)
    # Allow ±1 rounding per channel
    np.testing.assert_allclose(back, arr, atol=1)


def test_hsl_grading_zero_returns_same_image():
    """With all HSL params at 0, the helper must return the input unchanged."""
    arr = np.random.RandomState(0).randint(0, 256, (50, 50, 3), dtype=np.uint8)
    adj = edits.AdjustmentState()  # everything default
    out = _apply_hsl_grading(arr, adj)
    np.testing.assert_array_equal(out, arr)


def test_hsl_grading_shifts_red_hue():
    """Shifting red hue +30 deg should move the colour toward orange."""
    arr = np.full((20, 20, 3), [220, 30, 30], dtype=np.uint8)  # red
    adj = edits.AdjustmentState()
    adj.hue_red = 100  # max shift: +30 deg
    out = _apply_hsl_grading(arr, adj)
    # New colour should have a more yellow/green tint (higher G)
    assert out[..., 1].mean() > arr[..., 1].mean() + 5, \
        f"red→orange shift should boost green: in={arr[...,1].mean()} out={out[...,1].mean()}"


def test_hsl_grading_saturation_increase():
    """A +100 sat on a low-saturation input should boost colour contrast."""
    # yellow-ish: high R, high G, low B
    arr = np.full((20, 20, 3), [180, 170, 80], dtype=np.uint8)
    adj = edits.AdjustmentState()
    adj.sat_yellow = 100
    out = _apply_hsl_grading(arr, adj)
    # Saturation boost → colour distance from grey should grow
    grey = out.mean(axis=2, keepdims=True)
    spread = np.abs(out.astype(int) - grey).mean()
    spread_in = np.abs(arr.astype(int) - arr.mean(axis=2, keepdims=True)).mean()
    assert spread > spread_in, f"saturation should grow: in={spread_in:.1f} out={spread:.1f}"


def test_hsl_grading_clamps_outputs():
    """Outputs must stay in [0, 255] uint8 range even at extreme settings."""
    arr = np.full((10, 10, 3), [100, 50, 200], dtype=np.uint8)
    adj = edits.AdjustmentState()
    adj.lum_blue = 100
    adj.sat_blue = 100
    out = _apply_hsl_grading(arr, adj)
    assert out.dtype == np.uint8
    assert out.min() >= 0 and out.max() <= 255


def test_apply_adjustments_accepts_hsl_fields(tmp_path: Path):
    """apply_adjustments with HSL params must validate and persist."""
    src = tmp_path / "x.jpg"
    Image.new("RGB", (50, 50), (128, 64, 32)).save(src)
    r = server.apply_adjustments(
        str(src), hue_red=30, sat_blue=-40, lum_green=20,
    )
    assert r["status"] == "ok", r
    loaded = edits.EditState.load(src)
    assert loaded is not None
    assert loaded.adjustments.hue_red == 30
    assert loaded.adjustments.sat_blue == -40
    assert loaded.adjustments.lum_green == 20


def test_apply_adjustments_rejects_out_of_range_hsl(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (50, 50), (0, 0, 0)).save(src)
    r = server.apply_adjustments(str(src), hue_red=200)
    assert "error" in r
    assert "out of range" in r["error"]


# ---------------------------------------------------------------------------
# Tone curve
# ---------------------------------------------------------------------------

def test_tone_curve_lut_clamps_to_uint8():
    lut = _build_tone_curve_lut(100, 100, 100, 100)
    assert lut.dtype == np.uint8
    assert lut.shape == (256,)
    assert int(lut[0]) >= 0
    assert int(lut[255]) <= 255


def test_tone_curve_lut_pure_neutral():
    """All-zero params should give a near-identity curve."""
    lut = _build_tone_curve_lut(0, 0, 0, 0)
    # We don't expect exactly identity (the curve clamps), but the
    # endpoints should be close to 0 and 255.
    assert abs(int(lut[0]) - 0) < 5
    assert abs(int(lut[255]) - 255) < 5


def test_tone_curve_lut_shadows_lift_raises_blacks():
    """Lifting shadows should make the dark end brighter."""
    lut = _build_tone_curve_lut(shadows=80, darks=0, lights=0, highlights=0)
    # The black end (input 0) should now map to a higher output
    assert int(lut[10]) > 10, f"shadows lift didn't raise lut[10]={lut[10]}"


def test_tone_curve_lut_highlights_pull_drops_whites():
    """Pulling highlights should make the bright end dimmer."""
    lut = _build_tone_curve_lut(shadows=0, darks=0, lights=0, highlights=-80)
    assert int(lut[245]) < 245


def test_apply_adjustments_accepts_curve_fields(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (50, 50), (128, 128, 128)).save(src)
    r = server.apply_adjustments(
        str(src), curve_shadows=30, curve_highlights=-25,
    )
    assert r["status"] == "ok", r
    loaded = edits.EditState.load(src)
    assert loaded is not None
    assert loaded.adjustments.curve_shadows == 30
    assert loaded.adjustments.curve_highlights == -25


# ---------------------------------------------------------------------------
# Undo / redo via history sidecars
# ---------------------------------------------------------------------------

def test_apply_creates_history_entry(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (20, 20), (0, 0, 0)).save(src)

    server.apply_adjustments(str(src), exposure_ev=0.5)
    # First apply has no prior sidecar → no history entry
    history = list((tmp_path / "x.mcp.history").glob("*.json"))
    assert history == []

    server.apply_adjustments(str(src), contrast=20)
    # Second apply snapshots the first → exactly one history entry
    history = list((tmp_path / "x.mcp.history").glob("*.json"))
    assert len(history) == 1


def test_undo_restores_previous_state(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (20, 20), (0, 0, 0)).save(src)

    server.apply_adjustments(str(src), exposure_ev=0.5)
    server.apply_adjustments(str(src), contrast=20)
    server.apply_adjustments(str(src), saturation=10)

    state = edits.EditState.load(src)
    assert state is not None
    assert state.adjustments.saturation == 10
    assert state.adjustments.contrast == 20
    assert state.adjustments.exposure_ev == 0.5

    r = server.undo_last_edit(str(src))
    assert r["status"] == "ok"
    assert r["undone_steps"] == 1

    state = edits.EditState.load(src)
    assert state is not None
    assert state.adjustments.saturation == 0   # back to "contrast only" state
    assert state.adjustments.contrast == 20
    assert state.adjustments.exposure_ev == 0.5


def test_undo_multiple_steps(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (20, 20), (0, 0, 0)).save(src)
    for v in (1, 2, 3, 4, 5):
        server.apply_adjustments(str(src), exposure_ev=float(v))
    r = server.undo_last_edit(str(src), steps=3)
    assert r["status"] == "ok"
    assert r["undone_steps"] == 3
    state = edits.EditState.load(src)
    assert state is not None
    assert state.adjustments.exposure_ev == 2  # was 5, undone 3 times → 2


def test_undo_with_no_history_is_noop(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (20, 20), (0, 0, 0)).save(src)
    r = server.undo_last_edit(str(src))
    assert r["status"] == "noop"


def test_undo_does_not_push_new_history(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (20, 20), (0, 0, 0)).save(src)
    server.apply_adjustments(str(src), saturation=10)
    server.apply_adjustments(str(src), contrast=20)
    before_history = len(list((tmp_path / "x.mcp.history").glob("*.json")))
    server.undo_last_edit(str(src))
    after_history = len(list((tmp_path / "x.mcp.history").glob("*.json")))
    # Undo must not add a new history entry
    assert after_history == before_history


def test_history_is_fifo_trimmed(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (20, 20), (0, 0, 0)).save(src)
    # 30 successive applies using vibrance (range -100..+100) so we don't
    # trip exposure's stricter -5..+5 bound.
    for v in range(30):
        r = server.apply_adjustments(str(src), vibrance=float(v))
        assert r["status"] == "ok", r
    history = list((tmp_path / "x.mcp.history").glob("*.json"))
    # Default keep=20 → 29 candidates trimmed to 20
    assert len(history) == 20


def test_reset_edits_is_undoable(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (20, 20), (0, 0, 0)).save(src)
    server.apply_adjustments(str(src), saturation=10)
    server.reset_edits(str(src))
    r = server.undo_last_edit(str(src))
    assert r["status"] == "ok", r
    state = edits.EditState.load(src)
    assert state is not None
    assert state.adjustments.saturation == 10


def test_list_edit_history(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (20, 20), (0, 0, 0)).save(src)
    server.apply_adjustments(str(src), exposure_ev=1.0)
    server.apply_adjustments(str(src), exposure_ev=2.0)
    server.apply_adjustments(str(src), exposure_ev=3.0)
    r = server.list_edit_history(str(src))
    assert r["status"] == "ok"
    assert r["history_count"] == 2  # 3 applies, 2 history entries
    assert len(r["entries"]) == 2


# ---------------------------------------------------------------------------
# Before/after compare
# ---------------------------------------------------------------------------

def _call_async(coro_or_value):
    import asyncio
    if asyncio.iscoroutine(coro_or_value):
        return asyncio.run(coro_or_value)
    return coro_or_value


def test_compare_returns_image_and_saves_file(tmp_path: Path):
    from PIL import Image
    src = tmp_path / "x.jpg"
    Image.new("RGB", (200, 150), (200, 100, 50)).save(src)
    server.apply_adjustments(str(src), exposure_ev=1.0, contrast=30)

    r = _call_async(server.compare_before_after(str(src), max_size=200))
    assert isinstance(r, list)
    assert len(r) == 2
    # First item is a status message
    assert "comparison" in r[0].lower() or "saved" in r[0].lower()
    # Side-by-side file saved
    preview = src.with_name(src.stem + "__compare.jpg")
    assert preview.exists()
    # Image dimensions: 200 + divider + 200 = 404+ px wide
    with Image.open(preview) as im:
        assert im.size[0] > im.size[1]  # wider than tall


def test_compare_with_no_edits(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (100, 100), (0, 0, 0)).save(src)
    r = _call_async(server.compare_before_after(str(src), max_size=100))
    # Should still work — before == after == the same image
    assert isinstance(r, list)
    assert len(r) == 2


def test_compare_missing_file(tmp_path: Path):
    r = _call_async(server.compare_before_after(str(tmp_path / "nope.jpg")))
    assert isinstance(r, list)
    assert "error" in r[0]
