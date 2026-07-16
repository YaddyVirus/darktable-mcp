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
    # (no v0.5 params set)
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


# ---------------------------------------------------------------------------
# v0.5: HSL grading + tone curve round-trip into XMP
# ---------------------------------------------------------------------------

def test_xmp_levels_module_emitted_for_curve(tmp_path: Path):
    """Any non-zero curve_shadows/darks/lights/highlights must produce a
    ``levels`` history item of 28 bytes (modversion 2)."""
    p = tmp_path / "x.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    state.adjustments.curve_shadows = 40
    state.adjustments.curve_darks = 10
    state.adjustments.curve_lights = -10
    state.adjustments.curve_highlights = -30
    xmp_path = xmp_writer.write_xmp(state)
    text = xmp_path.read_text()
    assert 'darktable:operation="levels"' in text
    assert 'darktable:modversion="2"' in text

    import re
    m = re.search(
        r'darktable:operation="levels"[^>]*darktable:params="([0-9a-f]+)"',
        text,
    )
    assert m, "no params for levels"
    assert len(m.group(1)) // 2 == 28, (
        f"levels module expected 28 bytes, got {len(m.group(1)) // 2}"
    )


def test_xmp_levels_omitted_when_curve_is_zero(tmp_path: Path):
    p = tmp_path / "x.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    xmp_path = xmp_writer.write_xmp(state)
    text = xmp_path.read_text()
    assert 'darktable:operation="levels"' not in text


def test_xmp_levels_clamps_to_0_100_range(tmp_path: Path):
    """Black/gray/white must stay within darktable's 0..100 range even
    when our -100..+100 input pushes them past the limit."""
    p = tmp_path / "x.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    state.adjustments.curve_shadows = 100   # would push black to 100
    state.adjustments.curve_highlights = -100  # would push white to 0
    xmp_path = xmp_writer.write_xmp(state)
    import re
    m = re.search(
        r'darktable:operation="levels"[^>]*darktable:params="([0-9a-f]+)"',
        xmp_path.read_text(),
    )
    assert m is not None
    data = bytes.fromhex(m.group(1))
    # mode (int) | black (float) | gray (float) | white (float) | levels[3]
    import struct
    mode, black, gray, white = struct.unpack_from("<ifff", data, 0)
    assert 0.0 <= black <= 100.0
    assert 0.0 <= gray <= 100.0
    assert 0.0 <= white <= 100.0


def test_xmp_colorbalancergb_module_emitted_for_hsl(tmp_path: Path):
    p = tmp_path / "x.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    state.adjustments.hue_red = 30
    state.adjustments.sat_blue = -40
    state.adjustments.lum_yellow = 25
    xmp_path = xmp_writer.write_xmp(state)
    text = xmp_path.read_text()
    assert 'darktable:operation="colorbalancergb"' in text
    assert 'darktable:modversion="5"' in text

    import re
    m = re.search(
        r'darktable:operation="colorbalancergb"[^>]*darktable:params="([0-9a-f]+)"',
        text,
    )
    assert m, "no params for colorbalancergb"
    assert len(m.group(1)) // 2 == 132, (
        f"colorbalancergb module expected 132 bytes, got {len(m.group(1)) // 2}"
    )


def test_xmp_colorbalancergb_omitted_when_all_zero(tmp_path: Path):
    p = tmp_path / "x.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    xmp_path = xmp_writer.write_xmp(state)
    text = xmp_path.read_text()
    assert 'darktable:operation="colorbalancergb"' not in text


def test_xmp_colorbalancergb_all_24_params_round_trip(tmp_path: Path):
    """Every hue_*, sat_*, lum_* field must contribute to the encoded
    blob (no band silently dropped)."""
    import struct
    p = tmp_path / "x.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    for band in ("red", "orange", "yellow", "green",
                 "aqua", "blue", "purple", "magenta"):
        setattr(state.adjustments, f"hue_{band}", 25)
        setattr(state.adjustments, f"sat_{band}", 15)
        setattr(state.adjustments, f"lum_{band}", 10)
    xmp_path = xmp_writer.write_xmp(state)
    import re
    m = re.search(
        r'darktable:operation="colorbalancergb"[^>]*darktable:params="([0-9a-f]+)"',
        xmp_path.read_text(),
    )
    assert m is not None
    data = bytes.fromhex(m.group(1))
    # hue_angle at offset 23*4=92
    hue_angle = struct.unpack_from("<f", data, 92)[0]
    # hue 25*8 / 8 / 200 = 0.125
    assert abs(hue_angle - 0.125) < 1e-5, f"hue_angle: {hue_angle}"
    # saturation_global at offset 19*4=76
    sat_global = struct.unpack_from("<f", data, 76)[0]
    # 1.0 + 15*8/8/100 = 1.15
    assert abs(sat_global - 1.15) < 1e-5, f"sat_global: {sat_global}"


def test_xmp_v05_modules_increment_history_end(tmp_path: Path):
    """Confirm both v0.5 modules add to history_end when present."""
    p = tmp_path / "x.NEF"
    p.write_bytes(b"x")
    state = edits.EditState(source_path=p)
    state.adjustments.exposure_ev = 0.5
    state.adjustments.curve_shadows = 20
    state.adjustments.hue_blue = 30
    xmp_path = xmp_writer.write_xmp(state)
    text = xmp_path.read_text()
    # exposure + levels + colorbalancergb = 3
    assert 'darktable:history_end="3"' in text
    assert text.count("<rdf:li") == 3
