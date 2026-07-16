"""Darktable MCP Server — photo editing tools for Claude."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP, Image

from .edits import CropState, EditState, ValidationError, validate_param
from .presets import apply_preset as _apply_preset, list_preset_names, load_preset
from .processor import (
    ALL_EXTENSIONS,
    IMG_EXTENSIONS,
    RAW_EXTENSIONS,
    ImageProcessor,
    find_darktable_cli,
)
from .xmp_writer import write_xmp

logger = logging.getLogger("darktable_mcp")

mcp = FastMCP("DarktableMCP")

# Lock for sidecar writes so concurrent tool calls don't clobber each other
_sidecar_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Error-handling decorator
# ---------------------------------------------------------------------------

def handle_path_errors(func: Callable) -> Callable:
    """Wrap a tool that takes ``image_path`` as its first arg and convert
    common failure modes into structured return values."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        image_path = kwargs.get("image_path") or (args[0] if args else None)
        try:
            return func(*args, **kwargs)
        except FileNotFoundError as exc:
            return {"error": str(exc)}
        except ValidationError as exc:
            return {"error": f"Invalid parameter: {exc}"}
        except Exception as exc:
            logger.exception("Tool %s failed", func.__name__)
            return {"error": f"{type(exc).__name__}: {exc}"}
    return wrapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_or_new(image_path: str) -> tuple[Path, EditState]:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")
    state = EditState.load(path) or EditState(source_path=path)
    return path, state


def _save_sidecar(state: EditState) -> Path:
    """Persist state with the global lock."""
    with _sidecar_lock:
        return state.save()


def _quick_preview(path: Path, state: EditState, max_size: int) -> tuple[Image.Image, bytes]:
    proc = ImageProcessor(path)
    img = proc.process(state, preview_size=max_size)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return img, buf.getvalue()


async def _quick_preview_async(path: Path, state: EditState, max_size: int) -> tuple[Image.Image, bytes]:
    proc = ImageProcessor(path)
    img = await proc.process_async(state, preview_size=max_size)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return img, buf.getvalue()


def _open_in_viewer(path: Path) -> Optional[str]:
    """Open a file in the OS default image viewer.  Returns a status string.

    Tries (in order): Windows ``os.startfile``, macOS ``open``, Linux
    ``xdg-open``, falling back to ``None`` if none is available.
    """
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return "opened via os.startfile (Windows)"
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
            return "opened via 'open' (macOS)"
        # Linux and others
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(path)])
            return "opened via xdg-open (Linux)"
        return None
    except Exception as exc:
        return f"failed: {exc}"


def _save_and_open_preview(path: Path, img_bytes: bytes) -> tuple[Path, Optional[str]]:
    """Save preview next to the source and try to open it in the OS viewer."""
    preview_path = path.with_name(path.stem + "__preview.jpg")
    preview_path.write_bytes(img_bytes)
    status = _open_in_viewer(preview_path)
    return preview_path, status


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_images(directory: str) -> list[dict]:
    """List all supported image files (RAW and raster) in *directory*.

    Returns a list of dicts with keys: path, name, type, has_edits, output_name.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return [{"error": f"Directory not found: {directory}"}]

    results = []
    for file in sorted(dir_path.iterdir()):
        if file.suffix.lower() not in ALL_EXTENSIONS:
            continue
        state = EditState.load(file)
        results.append({
            "path": str(file),
            "name": file.name,
            "type": "raw" if file.suffix.lower() in RAW_EXTENSIONS else "image",
            "has_edits": state.has_changes() if state else False,
            "output_name": state.output_name if state else None,
        })
    return results


@mcp.tool()
@handle_path_errors
def get_image_info(image_path: str) -> dict:
    """Return detailed metadata (EXIF, dimensions, camera info) and current edit state for an image."""
    path, state = _load_or_new(image_path)
    proc = ImageProcessor(path)
    info = proc.get_info()
    info["edits"] = state.to_dict()
    info["darktable_cli_path"] = find_darktable_cli()
    return info


@mcp.tool()
@handle_path_errors
async def get_image_preview(image_path: str, max_size: int = 1200) -> list:
    """Render the image with all current edits applied, save a preview file, and open it.

    Saves a __preview.jpg next to the source and opens it in the system image
    viewer (Windows: os.startfile, macOS: open, Linux: xdg-open).  Also
    returns the image so Claude can analyse it and suggest further
    improvements.

    *max_size* controls the longest edge of the preview in pixels (default 1200).
    """
    path, state = _load_or_new(image_path)
    _img, img_bytes = await _quick_preview_async(path, state, max_size)
    preview_path, open_status = _save_and_open_preview(path, img_bytes)

    open_msg = (
        f"Preview opened in your image viewer ({open_status})."
        if open_status and not open_status.startswith("failed")
        else (
            f"Preview saved to: {preview_path} (could not auto-open: {open_status})"
            if open_status
            else f"Preview saved to: {preview_path} (no system viewer found)."
        )
    )

    return [
        open_msg,
        Image(data=img_bytes, format="jpeg"),
    ]


@mcp.tool()
@handle_path_errors
def apply_adjustments(
    image_path: str,
    # Exposure
    exposure_ev: Optional[float] = None,
    black_level: Optional[float] = None,
    highlight_recovery: Optional[float] = None,
    shadow_lift: Optional[float] = None,
    # White balance
    temperature_kelvin: Optional[float] = None,
    tint: Optional[float] = None,
    # Tone
    contrast: Optional[float] = None,
    brightness: Optional[float] = None,
    highlights: Optional[float] = None,
    shadows: Optional[float] = None,
    whites: Optional[float] = None,
    blacks: Optional[float] = None,
    # Colour
    saturation: Optional[float] = None,
    vibrance: Optional[float] = None,
    # Detail
    sharpness: Optional[float] = None,
    noise_reduction: Optional[float] = None,
    # Effects
    vignette: Optional[float] = None,
    clarity: Optional[float] = None,
) -> dict:
    """Apply one or more non-destructive adjustments to an image.

    Only the parameters you supply are changed; the rest keep their current values.
    Call get_image_preview afterwards to see the result.

    All numeric parameters are validated against safe ranges; see
    ``darktable_mcp.edits.RANGES`` for the exact bounds.  Out-of-range
    values raise a ValidationError that is returned as ``{"error": "..."}``.

    Parameters
    ----------
    exposure_ev : float
        Exposure in stops (-5 to +5).  0 = no change.
    black_level : float
        Lift or crush the black point (0.0–0.5).
    highlight_recovery : float
        Recover blown highlights (0.0–1.0).
    shadow_lift : float
        Open up dark shadows (0.0–1.0).
    temperature_kelvin : float
        Colour temperature (2000–12000 K).  ~5500 K is daylight, ~3200 K is tungsten.
    tint : float
        Green (positive) / magenta (negative) tint correction (-100 to +100).
    contrast : float
        Global contrast (-100 to +100).
    brightness : float
        Global brightness (-100 to +100).
    highlights : float
        Recover or boost highlights (-100 to +100).
    shadows : float
        Open shadows (-100 to +100).
    whites : float
        White-point adjustment (-100 to +100).
    blacks : float
        Black-point adjustment (-100 to +100).
    saturation : float
        Global colour saturation (-100 to +100).  0 = original.
    vibrance : float
        Selective saturation boost for muted colours (-100 to +100).
    sharpness : float
        Sharpening strength (0 to 100).
    noise_reduction : float
        Noise reduction strength (0 to 100).
    vignette : float
        Vignette: negative values darken edges, positive lighten (-100 to +100).
    clarity : float
        Local contrast / clarity (-100 to +100).
    """
    path, state = _load_or_new(image_path)

    params = {k: v for k, v in {
        "exposure_ev": exposure_ev,
        "black_level": black_level,
        "highlight_recovery": highlight_recovery,
        "shadow_lift": shadow_lift,
        "temperature_kelvin": temperature_kelvin,
        "tint": tint,
        "contrast": contrast,
        "brightness": brightness,
        "highlights": highlights,
        "shadows": shadows,
        "whites": whites,
        "blacks": blacks,
        "saturation": saturation,
        "vibrance": vibrance,
        "sharpness": sharpness,
        "noise_reduction": noise_reduction,
        "vignette": vignette,
        "clarity": clarity,
    }.items() if v is not None}

    # Validate all up front so we never half-apply
    for k, v in params.items():
        validate_param(k, v)

    state.update(params, validate=False)
    _save_sidecar(state)

    return {"status": "ok", "applied": list(params.keys()), "edits": state.to_dict()}


@mcp.tool()
@handle_path_errors
def crop_image(
    image_path: str,
    left: Optional[float] = None,
    top: Optional[float] = None,
    right: Optional[float] = None,
    bottom: Optional[float] = None,
    aspect_ratio: Optional[str] = None,
) -> dict:
    """Crop an image.

    Provide either:
    - *left*, *top*, *right*, *bottom* as normalised coordinates (0.0–1.0), or
    - *aspect_ratio* as a string like "16:9", "4:3", "1:1", "3:2" for a centre crop.

    Both methods can be combined (apply aspect ratio first, then fine-tune coordinates).
    """
    path, state = _load_or_new(image_path)

    crop = state.crop or CropState()
    new_state: dict = {}

    if aspect_ratio:
        try:
            w_ratio, h_ratio = (float(x) for x in aspect_ratio.split(":"))
        except ValueError:
            return {"error": f"Invalid aspect_ratio format '{aspect_ratio}'. Use e.g. '16:9'."}

        cw = crop.right - crop.left
        ch = crop.bottom - crop.top
        centre_x = crop.left + cw / 2
        centre_y = crop.top + ch / 2

        target_ar = w_ratio / h_ratio
        current_ar = cw / ch if ch > 0 else 1.0

        if current_ar > target_ar:
            new_w = ch * target_ar
            new_h = ch
        else:
            new_w = cw
            new_h = cw / target_ar

        new_state = {
            "left":   max(0.0, centre_x - new_w / 2),
            "top":    max(0.0, centre_y - new_h / 2),
            "right":  min(1.0, centre_x + new_w / 2),
            "bottom": min(1.0, centre_y + new_h / 2),
        }

    if left   is not None: new_state["left"]   = max(0.0, min(1.0, float(left)))
    if top    is not None: new_state["top"]    = max(0.0, min(1.0, float(top)))
    if right  is not None: new_state["right"]  = max(0.0, min(1.0, float(right)))
    if bottom is not None: new_state["bottom"] = max(0.0, min(1.0, float(bottom)))

    # Build a tentative crop, validate, then commit.
    candidate = CropState(**{**asdict(crop), **new_state}) if new_state else crop
    if candidate.right <= candidate.left or candidate.bottom <= candidate.top:
        return {"error": "Invalid crop: right must be > left and bottom must be > top."}

    state.crop = candidate
    _save_sidecar(state)

    return {"status": "ok", "crop": {
        "left": candidate.left, "top": candidate.top,
        "right": candidate.right, "bottom": candidate.bottom,
        "rotation": candidate.rotation,
    }}


@mcp.tool()
@handle_path_errors
def rotate_image(image_path: str, degrees: float) -> dict:
    """Rotate / straighten the image by *degrees* (clockwise).

    Positive = clockwise, negative = counter-clockwise.
    Typical use: small corrections like +0.5 or -1.2 to straighten horizons.
    Large rotations (90, 180, 270) are also supported.
    """
    path, state = _load_or_new(image_path)
    crop = state.crop or CropState()
    crop.rotation = float(degrees)
    state.crop = crop
    _save_sidecar(state)
    return {"status": "ok", "rotation_degrees": float(degrees)}


@mcp.tool()
@handle_path_errors
def reset_crop(image_path: str) -> dict:
    """Remove all crop and rotation from the image, restoring the full frame."""
    path, state = _load_or_new(image_path)
    state.crop = None
    _save_sidecar(state)
    return {"status": "ok", "message": "Crop reset to full frame."}


@mcp.tool()
@handle_path_errors
def rename_output(image_path: str, new_name: str) -> dict:
    """Set the output filename stem (without extension) for the exported image.

    For example: rename_output("/photos/DSC001.NEF", "golden_hour_lake")
    will export as golden_hour_lake.jpg (or whichever format you choose in export_image).
    """
    path, state = _load_or_new(image_path)
    stem = Path(new_name).stem or new_name
    state.output_name = stem
    _save_sidecar(state)
    return {"status": "ok", "output_name": stem}


@mcp.tool()
@handle_path_errors
def reset_edits(image_path: str) -> dict:
    """Reset all adjustments, crop, and output name — back to the unedited original.

    Removes both the .mcp.json sidecar and any .xmp sidecar that was
    generated by previous exports, so the directory is left clean.
    """
    path, state = _load_or_new(image_path)
    removed: list[str] = []

    with _sidecar_lock:
        for sidecar in state.sidecar_paths():
            try:
                sidecar.unlink()
                removed.append(str(sidecar))
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Failed to remove %s: %s", sidecar, exc)

    return {
        "status": "ok",
        "message": "All edits reset to original.",
        "removed_sidecars": removed,
    }


@mcp.tool()
@handle_path_errors
async def export_image(
    image_path: str,
    output_directory: Optional[str] = None,
    format: str = "jpeg",
    quality: int = 92,
    use_darktable_cli: bool = True,
    write_xmp_sidecar: bool = True,
    max_dimension: Optional[int] = None,
) -> dict:
    """Export the edited image to a file.

    Parameters
    ----------
    image_path : str
        Source image path.
    output_directory : str, optional
        Destination folder.  Defaults to the same folder as the source.
    format : str
        Output format: "jpeg", "png", "tiff" (default "jpeg").
    quality : int
        JPEG quality 1–100 (default 92).  Ignored for PNG/TIFF.
    use_darktable_cli : bool
        Use darktable-cli for the final render when available.  Falls back
        to a rawpy + Pillow pipeline automatically if it is not installed
        (RAW files then raise a clear error pointing to the install).
    write_xmp_sidecar : bool
        Write a Darktable-compatible XMP sidecar alongside the source file.
    max_dimension : int, optional
        Resize so the longest edge is at most this many pixels.
    """
    path, state = _load_or_new(image_path)

    out_dir = Path(output_directory) if output_directory else path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = state.output_name or path.stem
    ext_map = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "tiff": ".tif", "tif": ".tif"}
    ext = ext_map.get(format.lower(), ".jpg")
    out_path = out_dir / (stem + ext)

    # Avoid collision (not thread-safe but acceptable for single-user MCP)
    counter = 1
    while out_path.exists():
        out_path = out_dir / f"{stem}_{counter}{ext}"
        counter += 1

    # Optionally write Darktable XMP sidecar (used as input by darktable-cli)
    xmp_path: Optional[Path] = None
    if write_xmp_sidecar:
        try:
            xmp_path = write_xmp(state)
        except Exception as exc:
            logger.warning("XMP write failed: %s", exc)
            xmp_path = None

    # Try darktable-cli when available AND we have an XMP for it to use
    if use_darktable_cli and find_darktable_cli() is not None:
        result = await _export_via_darktable_cli(
            path, out_path, xmp_path, format, quality, max_dimension
        )
        if result.get("status") == "ok":
            result["xmp_sidecar"] = str(xmp_path) if xmp_path else None
            return result
        # Fall through to Pillow path if darktable-cli failed for some reason
        logger.info("darktable-cli failed (%s); falling back to Pillow", result.get("reason"))

    # Fallback: rawpy for RAW, Pillow for raster
    if path.suffix.lower() in RAW_EXTENSIONS:
        return {
            "status": "error",
            "reason": (
                "RAW rendering requires darktable-cli (preferred) or rawpy. "
                "Install darktable from https://www.darktable.org/ or set "
                "use_darktable_cli=False after installing rawpy locally."
            ),
        }

    proc = ImageProcessor(path)
    img = await proc.process_async(state, use_darktable_cli=False)

    if max_dimension:
        from PIL import Image as PILImage
        img.thumbnail((max_dimension, max_dimension), PILImage.Resampling.LANCZOS)

    save_kwargs: dict = {}
    if format.lower() in ("jpeg", "jpg"):
        save_kwargs = {"format": "JPEG", "quality": quality, "optimize": True}
    elif format.lower() == "png":
        save_kwargs = {"format": "PNG", "optimize": True}
    elif format.lower() in ("tiff", "tif"):
        save_kwargs = {"format": "TIFF"}

    img.save(out_path, **save_kwargs)

    return {
        "status": "ok",
        "output_path": str(out_path),
        "format": format,
        "size_bytes": out_path.stat().st_size,
        "xmp_sidecar": str(xmp_path) if xmp_path else None,
        "rendered_via": "rawpy+pillow",
    }


async def _export_via_darktable_cli(
    src: Path,
    dst: Path,
    xmp: Optional[Path],
    format: str,
    quality: int,
    max_dimension: Optional[int],
) -> dict:
    """Run darktable-cli asynchronously to render *src* (+ optional *xmp*) to *dst*."""
    from .processor import _arun_darktable_cli

    width = height = int(max_dimension) if max_dimension else 0
    ext_map = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "tiff": "tif", "tif": "tif"}
    out_ext = ext_map.get(format.lower(), "jpg")

    return await _arun_darktable_cli(
        src, xmp, dst,
        width=width, height=height, quality=quality, out_ext=out_ext,
        bpp=8, timeout=300,
    )


# ---------------------------------------------------------------------------
# Preset tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_presets() -> list[dict]:
    """List all available edit presets.

    Presets are JSON files in the package's ``presets/`` directory and
    any directory listed in the ``DARKTABLE_MCP_PRESETS`` env var
    (colon-separated on POSIX, semicolon on Windows).

    Each entry has ``name``, ``path``, and ``source`` (``"builtin"`` or
    ``"user"``).
    """
    return list_preset_names()


@mcp.tool()
@handle_path_errors
def apply_preset(image_path: str, preset_name: str) -> dict:
    """Apply a named preset to *image_path*.

    Only the fields present in the preset are changed; everything else
    keeps its current value.  Use ``get_image_preview`` to verify the
    result.

    The preset's ``output_name`` template (if any) is applied to the
    image's sidecar so a subsequent ``export_image`` writes to the
    preset's filename pattern.
    """
    path, state = _load_or_new(image_path)
    try:
        preset = load_preset(preset_name)
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    except ValueError as exc:
        return {"error": f"Invalid preset: {exc}"}

    _apply_preset(preset, state)
    _save_sidecar(state)

    return {
        "status": "ok",
        "preset": preset_name,
        "applied_adjustments": list((preset.get("adjustments") or {}).keys()),
        "applied_crop": preset.get("crop") is not None,
        "output_name": state.output_name,
        "edits": state.to_dict(),
    }


@mcp.tool()
@handle_path_errors
def get_preset(preset_name: str) -> dict:
    """Return the full contents of a preset (for inspection / debugging)."""
    try:
        data = load_preset(preset_name)
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    except ValueError as exc:
        return {"error": f"Invalid preset: {exc}"}
    return {"status": "ok", "preset": preset_name, "data": data}


@mcp.tool()
@handle_path_errors
def get_histogram(image_path: str, bins: int = 64) -> dict:
    """Compute a brightness/channel histogram for the image (with current edits).

    Returns per-channel (R, G, B) and luminance histograms as *bins*-bin
    arrays (default 64 — coarser than the 256 full-resolution but much
    lighter to send back over MCP).  Useful for analysing exposure,
    clipping, and tonal distribution.

    Set *bins=256* if you need pixel-perfect resolution (e.g. to detect
    a single clipped pixel).
    """
    import numpy as np

    if bins < 8 or bins > 1024:
        return {"error": "bins must be between 8 and 1024"}

    path, state = _load_or_new(image_path)
    proc = ImageProcessor(path)
    img = proc.process(state, preview_size=800)
    arr = np.array(img, dtype=np.uint8)

    def _hist(channel: np.ndarray) -> list[int]:
        counts, _ = np.histogram(channel.ravel(), bins=bins, range=(0, 256))
        return counts.tolist()

    r_hist = _hist(arr[..., 0])
    g_hist = _hist(arr[..., 1])
    b_hist = _hist(arr[..., 2])
    lum = (0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]).astype(np.uint8)
    lum_hist = _hist(lum)

    clipped_highlights = int((arr == 255).all(axis=2).sum())
    clipped_shadows = int((arr == 0).all(axis=2).sum())
    total_pixels = arr.shape[0] * arr.shape[1]

    return {
        "bins": bins,
        "r": r_hist,
        "g": g_hist,
        "b": b_hist,
        "luminance": lum_hist,
        "clipped_highlights_px": clipped_highlights,
        "clipped_shadows_px": clipped_shadows,
        "total_pixels": total_pixels,
        "highlight_clip_pct": round(clipped_highlights / total_pixels * 100, 4),
        "shadow_clip_pct": round(clipped_shadows / total_pixels * 100, 4),
    }


@mcp.tool()
@handle_path_errors
def get_raw_histogram(image_path: str, bins: int = 64) -> dict:
    """Compute a histogram of the *linear* sensor data, before any tone curve.

    Only meaningful for RAW files (sensor data in 16-bit linear space).
    For JPEG/PNG the result is the same as ``get_histogram`` since the
    input is already display-referred.

    Useful to:
      - detect true sensor clipping (highlights that the tone curve would
        otherwise "rescue" in a render),
      - read the actual dynamic range of the captured scene,
      - decide whether the exposure needs a real pull-down vs. just a
        tone-curve adjustment.

    Returns per-channel (R, G, B) and luminance histograms as *bins*-bin
    arrays, plus min/max/median values so you can quickly judge the
    actual exposure headroom.
    """
    import numpy as np

    if bins < 8 or bins > 1024:
        return {"error": "bins must be between 8 and 1024"}

    path, _state = _load_or_new(image_path)

    if path.suffix.lower() not in RAW_EXTENSIONS:
        return {
            "status": "ok",
            "note": f"{path.suffix!r} is not a RAW file; returned display-referred histogram.",
            "rendered": True,
        } | get_histogram(image_path, bins=bins)

    try:
        import rawpy
    except ImportError:
        return {"error": "rawpy is not installed; cannot read RAW linear data."}

    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            use_camera_wb=True,
            output_bps=16,
            no_auto_bright=True,
            bright=1.0,
            output_color=rawpy.ColorSpace.sRGB,
        )

    arr = np.asarray(rgb, dtype=np.uint16)
    # Per-channel histogram in 16-bit space
    def _hist16(channel: np.ndarray) -> list[int]:
        counts, _ = np.histogram(channel.ravel(), bins=bins, range=(0, 65536))
        return counts.tolist()

    r_hist = _hist16(arr[..., 0])
    g_hist = _hist16(arr[..., 1])
    b_hist = _hist16(arr[..., 2])
    lum16 = (
        0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    ).astype(np.uint32)
    lum_hist, _ = np.histogram(lum16.ravel(), bins=bins, range=(0, 65536))

    # 14-bit is the typical sensor ceiling (highlight clip in linear space)
    sensor_ceiling = 16383  # 2^14 - 1
    near_clip = int((lum16 >= sensor_ceiling * 0.99).sum())
    near_black = int((lum16 <= 1).sum())
    total = int(lum16.size)

    return {
        "status": "ok",
        "rendered": False,  # linear sensor data
        "bins": bins,
        "bit_depth": 16,
        "r": r_hist,
        "g": g_hist,
        "b": b_hist,
        "luminance": lum_hist.tolist(),
        "min_luminance": int(lum16.min()),
        "max_luminance": int(lum16.max()),
        "median_luminance": float(np.median(lum16)),
        "near_highlight_clip_px": near_clip,
        "near_shadow_floor_px": near_black,
        "near_highlight_clip_pct": round(near_clip / total * 100, 4),
        "near_shadow_floor_pct": round(near_black / total * 100, 4),
        "total_pixels": total,
        "sensor_bit_depth": 14,
        "sensor_ceiling": sensor_ceiling,
    }


@mcp.tool()
@handle_path_errors
def copy_settings(source_image_path: str, target_image_path: str) -> dict:
    """Copy all edit settings (adjustments + crop) from one image to another.

    Useful for batch-editing a set of photos shot under the same conditions.
    The output_name is NOT copied — each image keeps its own name.
    """
    src_path, src_state = _load_or_new(source_image_path)
    tgt_path, tgt_state = _load_or_new(target_image_path)

    from dataclasses import replace
    tgt_state.adjustments = replace(src_state.adjustments)
    tgt_state.crop = replace(src_state.crop) if src_state.crop else None
    _save_sidecar(tgt_state)

    return {"status": "ok", "copied_to": str(tgt_path), "edits": tgt_state.to_dict()}


@mcp.tool()
@handle_path_errors
async def apply_settings_to_folder(
    source_image_path: str,
    target_directory: str,
    file_pattern: str = "*",
    recursive: bool = False,
    skip_existing_edits: bool = False,
    max_concurrent: int = 4,
) -> dict:
    """Batch-apply edit settings (adjustments + crop) from *source_image_path*
    to every supported image in *target_directory*.

    Parameters
    ----------
    source_image_path : str
        Path to the image whose edit state should be copied.
    target_directory : str
        Folder containing target images.
    file_pattern : str
        Glob pattern (e.g. ``"*.NEF"``, ``"IMG_*.CR2"``).  Default ``"*"`` matches
        every supported image.
    recursive : bool
        If True, walk subdirectories too.
    skip_existing_edits : bool
        If True, skip files that already have a non-empty sidecar.
    max_concurrent : int
        Maximum number of sidecar writes in flight at once.  Defaults to 4
        — the writes are I/O bound so more than 8 rarely helps.

    Returns
    -------
    dict
        Summary: ``{"status": "ok", "copied": N, "skipped": M, "failed": K,
        "details": [...]}``.
    """
    from dataclasses import replace

    src_path, src_state = _load_or_new(source_image_path)
    src_has_changes = src_state.has_changes()
    if not src_has_changes:
        return {
            "status": "error",
            "reason": (
                f"Source {src_path} has no edits to apply "
                "(apply some adjustments first)."
            ),
        }

    dir_path = Path(target_directory)
    if not dir_path.is_dir():
        return {"error": f"Directory not found: {target_directory}"}

    if recursive:
        candidates = list(dir_path.rglob(file_pattern))
    else:
        candidates = list(dir_path.glob(file_pattern))

    targets: list[Path] = []
    skipped_self: list[str] = []
    for file in sorted(candidates):
        if not file.is_file():
            continue
        if file.suffix.lower() not in ALL_EXTENSIONS:
            continue
        if file.resolve() == src_path.resolve():
            skipped_self.append(str(file))
            continue
        targets.append(file)

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def _copy_one(file: Path) -> tuple[str, str, Optional[str]]:
        """Return (file, status, error).  status in {"copied", "skipped", "failed"}."""
        async with semaphore:
            try:
                existing = EditState.load(file)
                if skip_existing_edits and existing and existing.has_changes():
                    return (str(file), "skipped", None)
                tgt = existing or EditState(source_path=file)
                tgt.adjustments = replace(src_state.adjustments)
                tgt.crop = replace(src_state.crop) if src_state.crop else None
                # Run the blocking sidecar write in a thread so the event
                # loop isn't blocked under heavy concurrency.
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, _save_sidecar, tgt)
                return (str(file), "copied", None)
            except Exception as exc:
                logger.exception("Failed to copy settings to %s", file)
                return (str(file), "failed", str(exc))

    results = await asyncio.gather(*[_copy_one(t) for t in targets])

    copied = [f for f, s, _ in results if s == "copied"]
    skipped = [f for f, s, _ in results if s == "skipped"] + skipped_self
    failed = [{"file": f, "error": e} for f, s, e in results if s == "failed"]

    return {
        "status": "ok" if not failed else "partial",
        "source": str(src_path),
        "copied_count": len(copied),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "copied": copied,
        "skipped": skipped,
        "failed": failed,
    }
