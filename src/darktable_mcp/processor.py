"""Image processing pipeline using rawpy (for RAW) and Pillow (for all types)."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageDraw

from .edits import EditState, AdjustmentState, CropState

RAW_EXTENSIONS = {".nef", ".cr2", ".cr3", ".arw", ".raf", ".rw2", ".dng", ".orf", ".pef", ".srw", ".3fr"}
IMG_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}
ALL_EXTENSIONS = RAW_EXTENSIONS | IMG_EXTENSIONS


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _kelvin_to_rgb_shift(temp_k: float, tint: float = 0.0) -> tuple[float, float, float]:
    """Return (r_scale, g_scale, b_scale) for a Pillow-based white-balance shift.

    Follows Lightroom convention: higher K = warmer (more red, less blue).
    Reference is 5500 K (daylight = neutral).
    """
    ref_k = 5500.0
    ratio = temp_k / ref_k   # >1 → warmer, <1 → cooler
    r = ratio ** 0.6          # more red when warmer
    g = 1.0 + (tint / 100.0) * 0.1
    b = (1.0 / ratio) ** 0.6  # less blue when warmer
    return r, g, b


# ---------------------------------------------------------------------------
# EXIF helpers
# ---------------------------------------------------------------------------

def _ratio_to_float(value) -> float:
    return float(value.num) / float(value.den) if hasattr(value, "den") else float(value)


def _format_shutter(seconds: float) -> str:
    if seconds >= 1 or seconds <= 0:
        return f"{seconds:.1f}s".replace(".0s", "s")
    return f"1/{round(1 / seconds)}s"


def _read_exif(path: Path) -> dict:
    """Read camera EXIF from any RAW (TIFF-based) or raster file via exifread.

    Some cameras (e.g. Google Pixel DNGs) store the shooting tags in IFD0
    ("Image ...") instead of the EXIF sub-IFD ("EXIF ..."), so both are checked.
    """
    import exifread

    with open(path, "rb") as f:
        tags = exifread.process_file(f, details=False)

    def first(name: str):
        tag = tags.get(f"EXIF {name}") or tags.get(f"Image {name}")
        return tag.values[0] if tag and isinstance(tag.values, list) and tag.values else (tag.values if tag else None)

    info: dict = {}
    make, model = first("Make"), first("Model")
    if make or model:
        info["camera"] = " ".join(str(s).strip() for s in (make, model) if s)
    if (v := first("ISOSpeedRatings")) is not None:
        info["iso"] = int(v)
    if (v := first("ExposureTime")) is not None:
        seconds = _ratio_to_float(v)
        info["shutter"] = round(seconds, 6)
        info["shutter_display"] = _format_shutter(seconds)
    if (v := first("FNumber")) is not None:
        info["aperture"] = round(_ratio_to_float(v), 2)
    if (v := first("FocalLength")) is not None:
        info["focal_len"] = round(_ratio_to_float(v), 2)
    if (v := first("DateTimeOriginal") or first("DateTime")) is not None:
        info["timestamp"] = str(v)
    return info


# ---------------------------------------------------------------------------
# Tone-curve helpers
# ---------------------------------------------------------------------------

def _build_shadow_highlight_lut(
    highlights: float = 0.0,
    shadows: float = 0.0,
    whites: float = 0.0,
    blacks: float = 0.0,
) -> np.ndarray:
    """Build an 8-bit LUT that adjusts highlights/shadows/whites/blacks."""
    lut = np.arange(256, dtype=np.float32)

    # Whites/blacks shift the endpoints
    white_point = 255.0 * (1.0 - whites / 200.0)
    black_point = 255.0 * (-blacks / 200.0)
    lut = (lut - black_point) / (white_point - black_point) * 255.0

    # Highlights: bring down the top end
    hl = highlights / 100.0
    mask_hl = lut / 255.0
    lut = lut - hl * mask_hl ** 2 * 80.0

    # Shadows: lift the bottom end
    sh = shadows / 100.0
    mask_sh = 1.0 - lut / 255.0
    lut = lut + sh * mask_sh ** 2 * 80.0

    return np.clip(lut, 0, 255).astype(np.uint8)


def _apply_lut(img: Image.Image, lut: np.ndarray) -> Image.Image:
    arr = np.array(img)
    arr = lut[arr]
    return Image.fromarray(arr.astype(np.uint8))


# ---------------------------------------------------------------------------
# Per-effect Pillow helpers
# ---------------------------------------------------------------------------

def _apply_vignette(img: Image.Image, strength: float) -> Image.Image:
    """Darken (strength < 0) or lighten (strength > 0) the image edges.

    Uses pure numpy blending — avoids PIL composite quirks.
    """
    if abs(strength) < 1.0:
        return img
    w, h = img.size

    # Normalised elliptical distance from centre (0 = centre, 1 = corner)
    y_idx, x_idx = np.ogrid[:h, :w]
    cx, cy = w / 2.0, h / 2.0
    dist = np.sqrt(((x_idx - cx) / cx) ** 2 + ((y_idx - cy) / cy) ** 2)
    # Smooth S-curve falloff; clamp so corners don't blow past 1
    edge = np.clip(dist, 0.0, 1.0) ** 1.5   # shape (h, w)

    arr = np.array(img, dtype=np.float32)    # shape (h, w, 3)
    factor = abs(strength) / 100.0

    if strength < 0:
        # Dark vignette: multiply edges toward black
        multiplier = 1.0 - edge * factor      # 1 at centre, (1-factor) at edge
    else:
        # Light vignette: multiply edges toward white
        multiplier = 1.0 + edge * factor * 0.5

    arr = arr * multiplier[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _apply_clarity(img: Image.Image, clarity: float) -> Image.Image:
    """Local contrast via unsharp mask with a large radius."""
    if abs(clarity) < 1.0:
        return img
    amount = abs(clarity) / 100.0
    radius = 30
    if clarity > 0:
        return img.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 80), threshold=2))
    else:
        return img.filter(ImageFilter.GaussianBlur(radius=amount * 3))


def _apply_vibrance(img: Image.Image, vibrance: float) -> Image.Image:
    """Boost saturation selectively on less-saturated pixels."""
    if abs(vibrance) < 1.0:
        return img
    arr = np.array(img, dtype=np.float32) / 255.0
    hsv_like_sat = arr.max(axis=2) - arr.min(axis=2)   # per-pixel saturation proxy
    boost = (vibrance / 100.0) * (1.0 - hsv_like_sat[..., None])
    grey = arr.mean(axis=2, keepdims=True)
    arr = arr + (arr - grey) * boost
    arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _apply_highlight_shadow_recovery(
    img: Image.Image, highlight_recovery: float, shadow_lift: float
) -> Image.Image:
    """Simple tone-compression for highlights and shadow lift."""
    arr = np.array(img, dtype=np.float32) / 255.0

    if highlight_recovery > 0:
        hl = highlight_recovery
        arr = np.where(arr > 0.7, arr - hl * (arr - 0.7) * (1.5 - arr), arr)

    if shadow_lift > 0:
        sl = shadow_lift
        arr = np.where(arr < 0.3, arr + sl * (0.3 - arr) * (1.0 - arr * 2), arr)

    return Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))


def _apply_wb_pillow(img: Image.Image, r: float, g: float, b: float) -> Image.Image:
    """Scale RGB channels independently for white-balance adjustment."""
    arr = np.array(img, dtype=np.float32)
    arr[..., 0] = np.clip(arr[..., 0] * r, 0, 255)
    arr[..., 1] = np.clip(arr[..., 1] * g, 0, 255)
    arr[..., 2] = np.clip(arr[..., 2] * b, 0, 255)
    return Image.fromarray(arr.astype(np.uint8))


def _apply_noise_reduction(img: Image.Image, strength: float) -> Image.Image:
    if strength < 1.0:
        return img
    radius = strength / 100.0 * 2.0
    return img.filter(ImageFilter.GaussianBlur(radius=radius))


# ---------------------------------------------------------------------------
# Main processor
# ---------------------------------------------------------------------------

class ImageProcessor:
    def __init__(self, path: Path):
        self.path = path
        self.is_raw = path.suffix.lower() in RAW_EXTENSIONS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, edit_state: EditState, preview_size: Optional[int] = None) -> Image.Image:
        if self.is_raw:
            img = self._process_raw(edit_state.adjustments)
        else:
            img = self._process_raster(edit_state.adjustments)

        img = self._apply_post_raw(img, edit_state.adjustments)

        if edit_state.crop:
            img = self._apply_crop(img, edit_state.crop)

        if preview_size:
            img.thumbnail((preview_size, preview_size), Image.LANCZOS)

        return img

    def get_info(self) -> dict:
        info: dict = {
            "path": str(self.path),
            "name": self.path.name,
            "type": "raw" if self.is_raw else "image",
            "size_bytes": self.path.stat().st_size,
        }
        try:
            if self.is_raw:
                import rawpy
                with rawpy.imread(str(self.path)) as raw:
                    info["width"] = raw.sizes.width
                    info["height"] = raw.sizes.height
                    info["raw_type"] = raw.raw_type.name
            else:
                with Image.open(self.path) as img:
                    info["width"] = img.width
                    info["height"] = img.height
                    info["mode"] = img.mode
        except Exception as exc:
            info["read_error"] = str(exc)
        try:
            info.update(_read_exif(self.path))
        except Exception as exc:
            info["exif_error"] = str(exc)
        return info

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_raw(self, adj: AdjustmentState) -> Image.Image:
        import rawpy

        # Always use camera WB for RAW decode — temperature is applied in
        # _apply_post_raw via a Pillow channel shift, which is more predictable
        # than feeding user_wb to libraw (sensor-specific, easy to over-green).
        params = dict(
            use_camera_wb=True,
            output_color=rawpy.ColorSpace.sRGB,
            no_auto_bright=True,
            exp_shift=2 ** adj.exposure_ev,   # rawpy exp_shift is a linear multiplier
            exp_preserve_highlights=max(0.0, adj.highlight_recovery),
            bright=1.0,
            output_bps=8,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.DHT,
        )

        with rawpy.imread(str(self.path)) as raw:
            rgb = raw.postprocess(**params)

        return Image.fromarray(rgb)

    def _process_raster(self, adj: AdjustmentState) -> Image.Image:
        img = Image.open(self.path).convert("RGB")

        # Exposure via brightness multiplier
        if adj.exposure_ev != 0.0:
            factor = 2 ** adj.exposure_ev
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(factor)

        # Temperature/WB is handled in _apply_post_raw for both RAW and raster
        return img

    def _apply_post_raw(self, img: Image.Image, adj: AdjustmentState) -> Image.Image:
        """Pillow-based adjustments applied after RAW decode or raster open."""

        # White balance / temperature shift (applied first, before tone work)
        if adj.temperature_kelvin is not None:
            r, g, b = _kelvin_to_rgb_shift(adj.temperature_kelvin, adj.tint)
            img = _apply_wb_pillow(img, r, g, b)

        # Highlight & shadow recovery
        if adj.highlight_recovery > 0 or adj.shadow_lift > 0:
            img = _apply_highlight_shadow_recovery(img, adj.highlight_recovery, adj.shadow_lift)

        # Tone LUT (highlights / shadows / whites / blacks)
        if any([adj.highlights, adj.shadows, adj.whites, adj.blacks]):
            lut = _build_shadow_highlight_lut(adj.highlights, adj.shadows, adj.whites, adj.blacks)
            img = _apply_lut(img, lut)

        # Brightness (for non-RAW path it was already done; for RAW it's additive)
        if adj.brightness != 0.0:
            factor = 1.0 + adj.brightness / 100.0
            img = ImageEnhance.Brightness(img).enhance(max(0.0, factor))

        # Contrast
        if adj.contrast != 0.0:
            factor = 1.0 + adj.contrast / 100.0
            img = ImageEnhance.Contrast(img).enhance(max(0.0, factor))

        # Saturation
        if adj.saturation != 0.0:
            factor = 1.0 + adj.saturation / 100.0
            img = ImageEnhance.Color(img).enhance(max(0.0, factor))

        # Vibrance
        if adj.vibrance != 0.0:
            img = _apply_vibrance(img, adj.vibrance)

        # Clarity
        if adj.clarity != 0.0:
            img = _apply_clarity(img, adj.clarity)

        # Sharpness
        if adj.sharpness > 0:
            factor = 1.0 + adj.sharpness / 50.0
            img = ImageEnhance.Sharpness(img).enhance(factor)

        # Noise reduction
        if adj.noise_reduction > 0:
            img = _apply_noise_reduction(img, adj.noise_reduction)

        # Vignette
        if adj.vignette != 0.0:
            img = _apply_vignette(img, adj.vignette)

        return img

    def _apply_crop(self, img: Image.Image, crop: CropState) -> Image.Image:
        w, h = img.size

        # Apply rotation first (expand so no content is lost, then re-crop)
        if abs(crop.rotation) > 0.01:
            img = img.rotate(-crop.rotation, expand=True, resample=Image.BICUBIC)
            w, h = img.size

        left = int(crop.left * w)
        top = int(crop.top * h)
        right = int(crop.right * w)
        bottom = int(crop.bottom * h)

        # Safety clamp
        left, right = max(0, left), min(w, right)
        top, bottom = max(0, top), min(h, bottom)

        if right > left and bottom > top:
            img = img.crop((left, top, right, bottom))

        return img
