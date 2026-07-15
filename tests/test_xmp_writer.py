"""Unit tests for darktable_mcp.xmp_writer."""
from __future__ import annotations

from pathlib import Path

import pytest

from darktable_mcp import edits, xmp_writer


def test_xmp_contains_required_namespaces(tmp_path: Path):
    p = tmp_path / "DSC_0001.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    state.adjustments.exposure_ev = 0.5
    xmp_path = xmp_writer.write_xmp(state)
    text = xmp_path.read_text()
    assert 'xmlns:darktable="http://darktable.sf.net/"' in text
    assert "<x:xmpmeta" in text
    assert "darktable:operation=\"exposure\"" in text


def test_xmp_omits_unset_modules(tmp_path: Path):
    p = tmp_path / "DSC_0001.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    # No edits set — no history items
    xmp_path = xmp_writer.write_xmp(state)
    text = xmp_path.read_text()
    assert "darktable:operation=" not in text
    assert 'darktable:history_end="0"' in text


def test_xmp_history_end_matches_item_count(tmp_path: Path):
    p = tmp_path / "DSC_0001.CR2"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    state.adjustments.exposure_ev = 0.5
    state.adjustments.contrast = 20
    state.adjustments.sharpness = 30
    state.adjustments.temperature_kelvin = 4500
    state.adjustments.tint = 5
    state.adjustments.vignette = -20
    state.crop = edits.CropState(left=0.1, top=0.1, right=0.9, bottom=0.9, rotation=0.5)
    xmp_path = xmp_writer.write_xmp(state)
    text = xmp_path.read_text()
    # exposure + temperature + brightness_contrast + sharpen + vignette + crop = 6
    assert 'darktable:history_end="6"' in text
    # 6 history <rdf:li ... /> entries
    assert text.count("<rdf:li") == 6


def test_xmp_module_params_have_expected_byte_length(tmp_path: Path):
    """Each encoded params blob must match the documented struct size."""
    p = tmp_path / "x.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    state.adjustments.exposure_ev = 0.5
    state.adjustments.contrast = 10
    state.adjustments.sharpness = 50
    state.adjustments.temperature_kelvin = 4000
    state.adjustments.tint = 0
    state.adjustments.vignette = -30
    state.crop = edits.CropState(left=0.1, top=0.1, right=0.9, bottom=0.9, rotation=0.5)
    xmp_path = xmp_writer.write_xmp(state)
    text = xmp_path.read_text()

    # Each `darktable:params="<hex>"` should be 2*struct_size chars
    import re
    for op, expected_bytes in [
        ("exposure",            20),  # 5 * 4
        ("temperature",         44),  # see struct comment
        ("brightness_contrast",  8),  # 2 * 4
        ("sharpen",             12),  # 3 * 4
        ("vignette",            24),  # 4 floats + 2 ints
        ("crop",                20),  # 5 * 4
    ]:
        m = re.search(
            rf'darktable:operation="{op}"[^>]*darktable:params="([0-9a-f]+)"',
            text,
        )
        assert m, f"no params for {op}"
        assert len(m.group(1)) == expected_bytes * 2, (
            f"{op}: expected {expected_bytes} bytes ({expected_bytes*2} hex chars), "
            f"got {len(m.group(1))//2}"
        )


def test_xmp_writes_to_lowercase_xmp_extension(tmp_path: Path):
    p = tmp_path / "IMG.jpg"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    xmp_path = xmp_writer.write_xmp(state)
    # Must be lowercase ".xmp" — darktable expects this
    assert xmp_path.suffix == ".xmp"
    assert xmp_path.name == "IMG.xmp"
