"""Unit tests for darktable_mcp.processor.

The processor depends on Pillow + numpy which are universal; rawpy is
optional and only needed for RAW files.  These tests focus on the Pillow
fallback path and on the darktable-cli discovery helper.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from darktable_mcp import edits
from darktable_mcp.processor import (
    ImageProcessor,
    _apply_clarity,
    _apply_noise_reduction,
    _apply_vignette,
    _apply_vibrance,
    _kelvin_to_rgb_shift,
    find_darktable_cli,
)


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def test_kelvin_neutral_at_5500():
    r, g, b = _kelvin_to_rgb_shift(5500.0, 0.0)
    assert r == pytest.approx(1.0, abs=1e-6)
    assert g == pytest.approx(1.0, abs=1e-6)
    assert b == pytest.approx(1.0, abs=1e-6)


def test_kelvin_warmer_increases_red():
    r_warm, _, _ = _kelvin_to_rgb_shift(7000.0, 0.0)
    r_cool, _, _ = _kelvin_to_rgb_shift(4000.0, 0.0)
    assert r_warm > 1.0
    assert r_cool < 1.0


def test_tint_positive_increases_green():
    _, g_pos, _ = _kelvin_to_rgb_shift(5500.0, tint=50.0)
    _, g_neg, _ = _kelvin_to_rgb_shift(5500.0, tint=-50.0)
    assert g_pos > 1.0
    assert g_neg < 1.0


# ---------------------------------------------------------------------------
# Per-effect helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def solid_image() -> Image.Image:
    return Image.new("RGB", (200, 200), (128, 128, 128))


def test_vignette_below_threshold_is_unchanged(solid_image):
    out = _apply_vignette(solid_image, 0.5)
    np.testing.assert_array_equal(np.array(out), np.array(solid_image))


def test_vignette_darkens_corners_more_than_centre(solid_image):
    out = _apply_vignette(solid_image, -50)
    arr = np.array(out)
    # Sample a 5x5 patch at the corner and at the centre
    corner = float(arr[0:5, 0:5].mean())
    centre = float(arr[97:102, 97:102].mean())
    assert centre > corner, f"centre {centre} should be brighter than corner {corner}"


def test_clarity_below_threshold_is_unchanged(solid_image):
    out = _apply_clarity(solid_image, 0.5)
    # Solid colour → no visible change
    np.testing.assert_array_equal(np.array(out), np.array(solid_image))


def test_noise_reduction_below_threshold_is_unchanged(solid_image):
    out = _apply_noise_reduction(solid_image, 0.5)
    np.testing.assert_array_equal(np.array(out), np.array(solid_image))


def test_noise_reduction_smoothing_preserves_dimensions(solid_image):
    out = _apply_noise_reduction(solid_image, 80)
    assert out.size == solid_image.size


def test_vibrance_does_not_change_grayscale(solid_image):
    # Solid grey → vibrance should not move the colour
    out = _apply_vibrance(solid_image, 50)
    px = np.array(out)[100, 100]
    assert all(abs(int(c) - 128) <= 2 for c in px), px


# ---------------------------------------------------------------------------
# ImageProcessor on raster images (the Pillow fallback)
# ---------------------------------------------------------------------------

def test_process_raster_pipeline_returns_image(tmp_path: Path):
    # Create a synthetic gradient JPEG (4:3 → 400x300)
    import numpy as np
    arr = (np.linspace(0, 255, 400 * 300 * 3)
           .reshape(300, 400, 3).astype("uint8"))
    src = tmp_path / "gradient.jpg"
    Image.fromarray(arr).save(src, quality=90)

    state = edits.EditState(source_path=src)
    state.adjustments.exposure_ev = 0.5
    state.adjustments.contrast = 20
    state.adjustments.saturation = 30
    state.adjustments.sharpness = 25
    state.adjustments.temperature_kelvin = 4500
    state.adjustments.tint = 5
    state.adjustments.vignette = -30
    state.adjustments.clarity = 15
    state.adjustments.noise_reduction = 40

    proc = ImageProcessor(src)
    out = proc.process(state, preview_size=300, use_darktable_cli=False)

    assert isinstance(out, Image.Image)
    assert out.size == (300, 225)  # 4:3 thumbnail with max edge 300


def test_process_raster_with_crop(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (400, 300), (200, 100, 50)).save(src)
    state = edits.EditState(source_path=src)
    state.crop = edits.CropState(left=0.0, top=0.0, right=0.5, bottom=0.5)
    out = ImageProcessor(src).process(state, use_darktable_cli=False)
    # Cropped to half → 200x150, then no thumbnail
    assert out.size == (200, 150)


# ---------------------------------------------------------------------------
# darktable-cli discovery
# ---------------------------------------------------------------------------

def test_find_darktable_cli_returns_string_or_none(monkeypatch):
    """find_darktable_cli should never raise — returns str | None."""
    # Force shutil.which to return None
    monkeypatch.setattr(shutil, "which", lambda _: None)
    # And make the candidate paths miss too (we don't know where darktable is)
    result = find_darktable_cli()
    assert result is None or isinstance(result, str)


def test_get_info_includes_darktable_cli_availability(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(src)
    info = ImageProcessor(src).get_info()
    assert "darktable_cli_available" in info
    assert isinstance(info["darktable_cli_available"], bool)
    assert info["width"] == 10
    assert info["height"] == 10
