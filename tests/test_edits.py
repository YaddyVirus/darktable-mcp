"""Unit tests for darktable_mcp.edits."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from darktable_mcp import edits


def test_adjustment_state_defaults_are_in_range():
    """Every default AdjustmentState value must lie inside its declared RANGES."""
    state = edits.AdjustmentState()
    for name, value in state.__dict__.items():
        if name not in edits.RANGES:
            continue
        lo, hi = edits.RANGES[name]
        # None (used for temperature_kelvin) is also acceptable
        assert value is None or lo <= value <= hi, (
            f"default {name}={value} is outside [{lo}, {hi}]"
        )


def test_validate_param_accepts_in_range():
    edits.validate_param("exposure_ev", 2.0)
    edits.validate_param("temperature_kelvin", 5500.0)
    edits.validate_param("sharpness", 0)
    edits.validate_param("vibrance", -50)


def test_validate_param_rejects_out_of_range():
    with pytest.raises(edits.ValidationError):
        edits.validate_param("exposure_ev", 99.0)
    with pytest.raises(edits.ValidationError):
        edits.validate_param("temperature_kelvin", 100.0)
    with pytest.raises(edits.ValidationError):
        edits.validate_param("sharpness", -1.0)


def test_validate_param_rejects_wrong_type():
    with pytest.raises(edits.ValidationError):
        edits.validate_param("exposure_ev", "two")  # type: ignore[arg-type]


def test_edit_state_roundtrip(tmp_path: Path):
    p = tmp_path / "DSC_0001.NEF"
    p.write_bytes(b"fake raw content")

    original = edits.EditState(source_path=p)
    original.adjustments.exposure_ev = 1.5
    original.adjustments.contrast = 20
    original.adjustments.saturation = -10
    original.adjustments.temperature_kelvin = 4200
    original.adjustments.tint = 5
    original.adjustments.sharpness = 40
    original.adjustments.vignette = -25
    original.adjustments.clarity = 15
    original.adjustments.noise_reduction = 30
    original.output_name = "golden_hour"
    original.crop = edits.CropState(
        left=0.05, top=0.1, right=0.95, bottom=0.9, rotation=1.2
    )

    original.save()
    loaded = edits.EditState.load(p)

    assert loaded is not None
    assert loaded.adjustments.exposure_ev == 1.5
    assert loaded.adjustments.contrast == 20
    assert loaded.adjustments.saturation == -10
    assert loaded.adjustments.temperature_kelvin == 4200
    assert loaded.adjustments.tint == 5
    assert loaded.adjustments.sharpness == 40
    assert loaded.adjustments.vignette == -25
    assert loaded.adjustments.clarity == 15
    assert loaded.adjustments.noise_reduction == 30
    assert loaded.output_name == "golden_hour"
    assert loaded.crop is not None
    assert loaded.crop.left == 0.05
    assert loaded.crop.rotation == 1.2
    assert loaded.has_changes()


def test_edit_state_load_returns_none_when_missing(tmp_path: Path):
    p = tmp_path / "no_sidecar.jpg"
    p.write_bytes(b"x")
    assert edits.EditState.load(p) is None


def test_edit_state_load_returns_none_on_corrupt_json(tmp_path: Path):
    p = tmp_path / "corrupt.cr2"
    p.write_bytes(b"x")
    sidecar = p.with_name("corrupt.mcp.json")
    sidecar.write_text("{this is not valid json")
    assert edits.EditState.load(p) is None


def test_edit_state_update_validates_by_default(tmp_path: Path):
    p = tmp_path / "x.dng"
    p.write_bytes(b"x")
    s = edits.EditState(source_path=p)
    with pytest.raises(edits.ValidationError):
        s.update({"exposure_ev": 99.0})


def test_edit_state_update_ignores_unknown_keys(tmp_path: Path):
    p = tmp_path / "x.dng"
    p.write_bytes(b"x")
    s = edits.EditState(source_path=p)
    s.update({"not_a_real_field": 42, "exposure_ev": 0.5})
    assert s.adjustments.exposure_ev == 0.5
    assert not hasattr(s.adjustments, "not_a_real_field")


def test_sidecar_paths_lists_xmp_when_present(tmp_path: Path):
    p = tmp_path / "DSC.NEF"
    p.write_bytes(b"x")
    s = edits.EditState(source_path=p)
    paths = s.sidecar_paths()
    assert paths == [p.with_name("DSC.mcp.json")]

    xmp = p.with_suffix(".xmp")
    xmp.write_text("<x:xmpmeta/>")
    paths2 = s.sidecar_paths()
    assert xmp in paths2
    xmp.unlink()


def test_save_writes_valid_json(tmp_path: Path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"x")
    s = edits.EditState(source_path=p)
    s.adjustments.exposure_ev = 0.5
    s.save()
    data = json.loads(s._sidecar(p).read_text())
    assert data["adjustments"]["exposure_ev"] == 0.5
    assert data["crop"] is None
