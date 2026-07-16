"""Tests for the batch tool apply_settings_to_folder."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from darktable_mcp import edits, server


@pytest.fixture
def source_with_edits(tmp_path: Path) -> Path:
    src = tmp_path / "source.jpg"
    Image.new("RGB", (50, 50), (128, 64, 32)).save(src)
    r = server.apply_adjustments(str(src), exposure_ev=0.7, contrast=20,
                                  temperature_kelvin=4200, vignette=-30)
    assert r["status"] == "ok"
    return src


def _make_jpg(path: Path, colour: tuple[int, int, int] = (50, 100, 150)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (50, 50), colour).save(path)


def test_apply_to_folder_copies_to_all_supported(tmp_path: Path, source_with_edits: Path):
    target = tmp_path / "dest"
    _make_jpg(target / "a.jpg")
    _make_jpg(target / "b.jpg")
    _make_jpg(target / "c.png")
    # Non-supported file — should be ignored
    (target / "ignore.txt").write_text("hello")

    r = server.apply_settings_to_folder(str(source_with_edits), str(target))

    assert r["status"] == "ok"
    assert r["copied_count"] == 3
    assert r["skipped_count"] == 0
    assert r["failed_count"] == 0
    # Each target has the same adjustment values
    for f in (target / "a.jpg", target / "b.jpg", target / "c.png"):
        loaded = edits.EditState.load(f)
        assert loaded is not None
        assert loaded.adjustments.exposure_ev == 0.7
        assert loaded.adjustments.contrast == 20
        assert loaded.adjustments.temperature_kelvin == 4200
        assert loaded.adjustments.vignette == -30


def test_apply_to_folder_glob_filter(tmp_path: Path, source_with_edits: Path):
    target = tmp_path / "dest"
    _make_jpg(target / "IMG_0001.jpg")
    _make_jpg(target / "IMG_0002.jpg")
    _make_jpg(target / "DSC_0001.jpg")

    r = server.apply_settings_to_folder(
        str(source_with_edits), str(target), file_pattern="IMG_*.jpg"
    )
    assert r["status"] == "ok"
    assert r["copied_count"] == 2
    # DSC file should NOT have been touched
    assert not (target / "DSC_0001.mcp.json").exists()


def test_apply_to_folder_recursive(tmp_path: Path, source_with_edits: Path):
    target = tmp_path / "dest"
    _make_jpg(target / "sub1" / "a.jpg")
    _make_jpg(target / "sub2" / "deep" / "b.jpg")
    _make_jpg(target / "top.jpg")

    r = server.apply_settings_to_folder(
        str(source_with_edits), str(target), recursive=True
    )
    assert r["copied_count"] == 3


def test_apply_to_folder_non_recursive_ignores_subdirs(
    tmp_path: Path, source_with_edits: Path
):
    target = tmp_path / "dest"
    _make_jpg(target / "top.jpg")
    _make_jpg(target / "sub" / "nested.jpg")

    r = server.apply_settings_to_folder(
        str(source_with_edits), str(target), recursive=False
    )
    assert r["copied_count"] == 1
    assert not (target / "sub" / "nested.mcp.json").exists()


def test_apply_to_folder_skip_existing(tmp_path: Path, source_with_edits: Path):
    target = tmp_path / "dest"
    _make_jpg(target / "a.jpg")
    _make_jpg(target / "b.jpg")
    # Give 'a' its own prior edit
    server.apply_adjustments(str(target / "a.jpg"), exposure_ev=-0.5)

    r = server.apply_settings_to_folder(
        str(source_with_edits), str(target), skip_existing_edits=True
    )
    assert r["copied_count"] == 1   # only 'b'
    assert r["skipped_count"] == 1
    # 'a' should still have its original -0.5, not the source's 0.7
    a_state = edits.EditState.load(target / "a.jpg")
    assert a_state is not None
    assert a_state.adjustments.exposure_ev == -0.5


def test_apply_to_folder_does_not_copy_to_self(
    tmp_path: Path, source_with_edits: Path
):
    target = tmp_path / "dest"
    target.mkdir()
    # Move source into target
    new_source = target / "source.jpg"
    new_source.write_bytes(source_with_edits.read_bytes())
    source_with_edits.unlink()
    # And give it the original mcp sidecar
    src_sidecar = source_with_edits.with_name(source_with_edits.stem + ".mcp.json")
    if src_sidecar.exists():
        (target / "source.mcp.json").write_bytes(src_sidecar.read_bytes())
        src_sidecar.unlink()

    r = server.apply_settings_to_folder(str(new_source), str(target))
    assert r["copied_count"] == 0
    assert r["skipped_count"] == 1


def test_apply_to_folder_rejects_source_without_edits(tmp_path: Path):
    src = tmp_path / "source.jpg"
    Image.new("RGB", (50, 50), (0, 0, 0)).save(src)
    target = tmp_path / "dest"
    target.mkdir()
    r = server.apply_settings_to_folder(str(src), str(target))
    assert r["status"] == "error"
    assert "no edits" in r["reason"]


def test_apply_to_folder_missing_directory(tmp_path: Path, source_with_edits: Path):
    r = server.apply_settings_to_folder(
        str(source_with_edits), str(tmp_path / "no-such-dir")
    )
    assert "error" in r
