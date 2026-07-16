"""Tests for the preset system."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image

from darktable_mcp import edits, presets, server


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

def test_builtin_presets_listed():
    names = {p["name"] for p in presets.list_preset_names()}
    assert "moody_film" in names
    assert "golden_hour" in names
    assert "landscape_pop" in names
    assert "portrait_soft" in names


def test_builtin_preset_files_have_descriptions():
    for entry in presets.list_preset_names():
        if entry["source"] != "builtin":
            continue
        with open(entry["path"]) as f:
            data = json.load(f)
        assert "description" in data, f"preset {entry['name']} has no description"


# ---------------------------------------------------------------------------
# Loading & validation
# ---------------------------------------------------------------------------

def test_load_preset_returns_validated_dict():
    p = presets.load_preset("moody_film")
    assert "adjustments" in p
    assert "contrast" in p["adjustments"]
    assert p["adjustments"]["contrast"] == 30


def test_load_preset_missing_raises_filenotfound():
    with pytest.raises(FileNotFoundError):
        presets.load_preset("nonexistent_preset_xyz")


def test_load_preset_with_invalid_json_raises_valueerror(tmp_path: Path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{this is not valid json")
    monkeypatch.setenv("DARKTABLE_MCP_PRESETS", str(tmp_path))
    with pytest.raises(ValueError, match="not valid JSON"):
        presets.load_preset("bad")


def test_load_preset_with_unknown_adjustment_raises_valueerror(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"adjustments": {"not_a_real_field": 42}}))
    monkeypatch.setenv("DARKTABLE_MCP_PRESETS", str(tmp_path))
    with pytest.raises(ValueError, match="unknown adjustment"):
        presets.load_preset("bad")


def test_load_preset_with_out_of_range_value_raises_valueerror(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"adjustments": {"exposure_ev": 999.0}}))
    monkeypatch.setenv("DARKTABLE_MCP_PRESETS", str(tmp_path))
    with pytest.raises(ValueError, match="out of range"):
        presets.load_preset("bad")


def test_load_preset_with_invalid_crop_type_raises_valueerror(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"crop": {"left": "not a number"}}))
    monkeypatch.setenv("DARKTABLE_MCP_PRESETS", str(tmp_path))
    with pytest.raises(ValueError, match="must be a number"):
        presets.load_preset("bad")


# ---------------------------------------------------------------------------
# apply_preset
# ---------------------------------------------------------------------------

def test_apply_preset_overrides_only_listed_adjustments(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (50, 50), (100, 100, 100)).save(src)
    # Pre-set fields that moody_film does NOT override
    server.apply_adjustments(str(src), sharpness=45, noise_reduction=30, black_level=0.2)

    r = server.apply_preset(str(src), "moody_film")
    assert r["status"] == "ok", r

    state = edits.EditState.load(src)
    assert state is not None
    # Fields the preset sets:
    assert state.adjustments.contrast == 30
    assert state.adjustments.vignette == -35
    assert state.adjustments.exposure_ev == -0.4
    # Fields the preset does NOT touch keep their prior values:
    assert state.adjustments.sharpness == 45
    assert state.adjustments.noise_reduction == 30
    assert state.adjustments.black_level == 0.2


def test_apply_preset_with_crop_overrides_crop(tmp_path: Path):
    src = tmp_path / "x.jpg"
    Image.new("RGB", (50, 50), (100, 100, 100)).save(src)
    # Set a prior crop
    server.crop_image(str(src), aspect_ratio="16:9")

    custom_preset = {
        "crop": {"left": 0.1, "top": 0.2, "right": 0.9, "bottom": 0.8}
    }
    loaded = edits.EditState.load(src)
    assert loaded is not None
    presets.apply_preset(custom_preset, loaded)
    loaded.save()

    state = edits.EditState.load(src)
    assert state is not None
    assert state.crop is not None
    assert state.crop.left == 0.1
    assert state.crop.top == 0.2
    assert state.crop.right == 0.9
    assert state.crop.bottom == 0.8


def test_apply_preset_with_output_name_template(tmp_path: Path):
    src = tmp_path / "IMG_3500.jpg"
    Image.new("RGB", (50, 50), (100, 100, 100)).save(src)

    custom_preset = {
        "adjustments": {"contrast": 10},
        "output_name": "edited_{stem}",
    }
    # Create state in memory (no prior sidecar)
    state = edits.EditState(source_path=src)
    presets.apply_preset(custom_preset, state)
    assert state.output_name == "edited_IMG_3500"


def test_apply_preset_sanitises_output_name(tmp_path: Path):
    """Filenames with weird chars get sanitised."""
    src = tmp_path / "x.jpg"
    Image.new("RGB", (10, 10), (0, 0, 0)).save(src)
    state = edits.EditState(source_path=src)
    presets.apply_preset(
        {"output_name": "name with spaces & weird!chars"},
        state,
    )
    # The exact sanitisation: any non-[A-Za-z0-9._-] -> "_"
    assert state.output_name == "name_with_spaces___weird_chars"


# ---------------------------------------------------------------------------
# Server tools
# ---------------------------------------------------------------------------

def test_server_list_presets_returns_builtins():
    r = server.list_presets()
    assert isinstance(r, list)
    names = {p["name"] for p in r}
    assert "golden_hour" in names
    for entry in r:
        assert "name" in entry
        assert "path" in entry
        assert "source" in entry
        assert entry["source"] in ("builtin", "user")


def test_server_get_preset_returns_full_data():
    r = server.get_preset("moody_film")
    assert r["status"] == "ok"
    assert r["preset"] == "moody_film"
    assert "data" in r
    assert "adjustments" in r["data"]


def test_server_get_preset_missing_returns_error():
    r = server.get_preset("definitely_does_not_exist")
    assert "error" in r


def test_server_apply_preset_writes_sidecar(tmp_path: Path):
    src = tmp_path / "img.jpg"
    Image.new("RGB", (50, 50), (100, 100, 100)).save(src)
    r = server.apply_preset(str(src), "landscape_pop")
    assert r["status"] == "ok", r
    assert r["applied_crop"] is False   # no crop in this preset
    assert "saturation" in r["applied_adjustments"]
    # Sidecar was written
    sidecar = src.with_name(src.stem + ".mcp.json")
    assert sidecar.exists()
    loaded = json.loads(sidecar.read_text())
    assert loaded["adjustments"]["saturation"] == 25


def test_server_apply_preset_missing_returns_error(tmp_path: Path):
    src = tmp_path / "img.jpg"
    Image.new("RGB", (50, 50), (0, 0, 0)).save(src)
    r = server.apply_preset(str(src), "no_such_preset")
    assert "error" in r
    assert "not found" in r["error"]


# ---------------------------------------------------------------------------
# Env var override
# ---------------------------------------------------------------------------

def test_user_presets_via_env_override(tmp_path: Path, monkeypatch):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "my_look.json").write_text(json.dumps({
        "description": "Custom user preset",
        "adjustments": {"exposure_ev": 1.5, "vibrance": 50},
    }))
    monkeypatch.setenv("DARKTABLE_MCP_PRESETS", str(user_dir))

    names = {p["name"] for p in presets.list_preset_names()}
    assert "my_look" in names

    data = presets.load_preset("my_look")
    assert data["adjustments"]["exposure_ev"] == 1.5
    assert data["adjustments"]["vibrance"] == 50
