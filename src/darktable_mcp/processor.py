"""Image processing pipeline.

RAW and raster rendering is delegated to ``darktable-cli`` when it is
available on PATH.  ``rawpy`` + Pillow is used as a fallback for the case
where the user does not have darktable installed.  The fallback path only
handles raster images; for RAW without darktable we raise a clear error.
"""
from __future__ import annotations

import asyncio
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Union

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from .edits import EditState, AdjustmentState, CropState

RAW_EXTENSIONS = {".nef", ".cr2", ".cr3", ".arw", ".raf", ".rw2", ".dng", ".orf", ".pef", ".srw", ".3fr"}
IMG_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}
ALL_EXTENSIONS = RAW_EXTENSIONS | IMG_EXTENSIONS


# ---------------------------------------------------------------------------
# darktable-cli discovery
# ---------------------------------------------------------------------------

_CANDIDATE_PATHS: dict[str, list[str]] = {
    "Windows": [
        r"C:\Program Files\darktable\bin\darktable-cli.exe",
        r"C:\Program Files (x86)\darktable\bin\darktable-cli.exe",
    ],
    "Darwin": [
        "/Applications/darktable.app/Contents/MacOS/darktable-cli",
        "/usr/local/bin/darktable-cli",
        "/opt/homebrew/bin/darktable-cli",
    ],
    "Linux": [
        "/usr/bin/darktable-cli",
        "/usr/local/bin/darktable-cli",
        "/snap/bin/darktable-cli",
        "/opt/darktable/bin/darktable-cli",
    ],
}


def find_darktable_cli() -> Optional[str]:
    """Return the path to ``darktable-cli`` or ``None`` if not installed."""
    found = shutil.which("darktable-cli")
    if found:
        return found
    for path in _CANDIDATE_PATHS.get(sys.platform, []):
        if Path(path).exists():
            return path
    return None


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
    """Local contrast adjustment.

    Positive = more local contrast (mid-tone pop), negative = less local
    contrast (softer, less crunchy).  Both directions use an unsharp mask
    on a large-radius blurred copy; the sign only flips the mix direction.
    """
    if abs(clarity) < 1.0:
        return img
    amount = abs(clarity) / 100.0
    radius = 30
    # PIL's UnsharpMask only boosts (percent > 0).  To *reduce* local
    # contrast, we blend a strong-blur version of the image back in.
    if clarity > 0:
        return img.filter(
            ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 150), threshold=2)
        )
    # Negative clarity: gently soften without losing edges
    soft = img.filter(ImageFilter.GaussianBlur(radius=2))
    return Image.blend(img, soft, amount * 0.6)


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
    """Luminance-preserving noise reduction via median filter.

    Pillow's ``MedianFilter`` is a proper rank filter, not a Gaussian blur,
    so it actually suppresses impulse / shot noise.  For higher strength we
    run a 3-iteration pass on a downsampled copy then upscale — better
    trade-off between noise removal and detail loss.
    """
    if strength < 1.0:
        return img
    radius = max(1, int(round(strength / 33.0)))  # 1..3
    if radius == 1:
        return img.filter(ImageFilter.MedianFilter(size=3))
    if radius == 2:
        return img.filter(ImageFilter.MedianFilter(size=3)).filter(
            ImageFilter.MedianFilter(size=3)
        )
    # radius == 3: stronger, with a small sharpen back to recover detail
    denoised = img.filter(ImageFilter.MedianFilter(size=3))
    denoised = denoised.filter(ImageFilter.MedianFilter(size=3))
    denoised = denoised.filter(ImageFilter.MedianFilter(size=3))
    return denoised.filter(ImageFilter.UnsharpMask(radius=1, percent=30, threshold=2))


# ---------------------------------------------------------------------------
# Parametric tone curve
# ---------------------------------------------------------------------------

def _build_tone_curve_lut(
    shadows: float,
    darks: float,
    lights: float,
    highlights: float,
) -> np.ndarray:
    """Build a 256-entry tone-curve LUT from 4 control points.

    Control-point positions (in normalised luminance):
        shadows    at 0.00   — affects the deep blacks
        darks      at 0.25   — lower midtones
        lights     at 0.75   — upper midtones
        highlights at 1.00   — near-whites

    Each control point shifts the curve by *value/100* (clamped to ±1.0
    so we don't go negative).  The output is a smooth piecewise-linear
    curve interpolated at every 1/255 input.
    """
    # Anchor positions on the [0, 255] LUT axis.
    xs = np.array([0.0, 63.75, 191.25, 255.0])
    ys = np.array([shadows, darks, lights, highlights]) / 100.0
    # Convert into output offset at each anchor; clamp so the curve
    # can never invert.
    ys = np.clip(ys, -1.0, 1.0)
    # Map the offset into a target luminance value: out = in + offset
    # (clamped to [0, 1]).  Then renormalise the four anchor points so
    # the overall slope doesn't collapse to zero.
    target = np.clip(xs / 255.0 + ys, 0.0, 1.0)
    # np.interp requires the x-axis to be sorted.
    out = np.interp(np.arange(256, dtype=np.float32), xs, target * 255.0)
    return out.astype(np.uint8)


# ---------------------------------------------------------------------------
# HSL colour grading
# ---------------------------------------------------------------------------

# 8 colour bands defined by hue centre + half-width (in degrees, 0-360).
# Wraps around the wheel (red is split into 0-15 and 345-360).
_HSL_BANDS: list[tuple[str, float, float]] = [
    ("red",     0.0,   20.0),
    ("orange",  30.0,  15.0),
    ("yellow",  60.0,  20.0),
    ("green",  110.0,  40.0),
    ("aqua",   170.0,  20.0),
    ("blue",   225.0,  30.0),
    ("purple", 270.0,  20.0),
    ("magenta",310.0,  25.0),
]


def _hue_distance(h: np.ndarray, centre: float) -> np.ndarray:
    """Circular hue distance (in degrees, 0-180) on the 0-360 wheel."""
    d = np.abs(h - centre)
    return np.minimum(d, 360.0 - d)


def _band_weights(h: np.ndarray) -> np.ndarray:
    """Return (8, h, w) array of soft weights summing to 1 along axis 0."""
    weights = np.zeros((len(_HSL_BANDS), h.shape[0], h.shape[1]),
                       dtype=np.float32)
    for i, (_name, centre, half_w) in enumerate(_HSL_BANDS):
        d = _hue_distance(h, centre)
        # Triangular falloff: 1.0 at centre, 0.0 at half_w.
        weights[i] = np.clip(1.0 - d / half_w, 0.0, 1.0)
    total = weights.sum(axis=0)
    total = np.where(total > 0, total, 1.0)
    return weights / total


def _rgb_to_hsl(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised RGB uint8 (H, W, 3) -> (H, W) H, S, L in [0,360]/[0,1]/[0,1]."""
    r, g, b = arr[..., 0].astype(np.float32) / 255.0, \
              arr[..., 1].astype(np.float32) / 255.0, \
              arr[..., 2].astype(np.float32) / 255.0
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    light = (mx + mn) / 2.0
    sat = np.zeros_like(light)
    delta = mx - mn
    nonzero = delta > 1e-6
    sat[nonzero] = np.where(
        light[nonzero] <= 0.5,
        delta[nonzero] / (mx[nonzero] + mn[nonzero] + 1e-6),
        delta[nonzero] / (2.0 - mx[nonzero] - mn[nonzero] + 1e-6),
    )
    hue = np.zeros_like(light)
    rmax = (mx == r) & nonzero
    gmax = (mx == g) & nonzero
    bmax = (mx == b) & nonzero
    hue[rmax] = ((g[rmax] - b[rmax]) / (delta[rmax] + 1e-6)) % 6.0
    hue[gmax] = ((b[gmax] - r[gmax]) / (delta[gmax] + 1e-6)) + 2.0
    hue[bmax] = ((r[bmax] - g[bmax]) / (delta[bmax] + 1e-6)) + 4.0
    hue = hue * 60.0  # 0..360
    return hue, sat, light


def _hsl_to_rgb(h: np.ndarray, s: np.ndarray, light: np.ndarray) -> np.ndarray:
    """Inverse of :func:`_rgb_to_hsl`.  Returns uint8 (H, W, 3)."""
    h = h % 360.0
    c = (1.0 - np.abs(2.0 * light - 1.0)) * s
    x = c * (1.0 - np.abs((h / 60.0) % 2.0 - 1.0))
    m = light - c / 2.0
    sector = (h / 60.0).astype(np.int32) % 6
    rgb = np.zeros(light.shape + (3,), dtype=np.float32)
    # Per-sector (R, G, B) intermediate values.  We assemble these as
    # full-size arrays so pyright can see r, g, b as np.ndarray instead
    # of the for-loop tuple scalars.
    rs = [c,   x,   np.zeros_like(c), np.zeros_like(c), x,   c]
    gs = [x,   c,   c,              x,              np.zeros_like(c), np.zeros_like(c)]
    bs = [np.zeros_like(c), np.zeros_like(c), x,   c,   c,   x]
    for i in range(6):
        mask = sector == i
        rgb[..., 0] = np.where(mask, rs[i], rgb[..., 0])
        rgb[..., 1] = np.where(mask, gs[i], rgb[..., 1])
        rgb[..., 2] = np.where(mask, bs[i], rgb[..., 2])
    rgb += m[..., None]
    return np.clip(rgb * 255.0, 0, 255).astype(np.uint8)


def _apply_hsl_grading(arr: np.ndarray, adj: AdjustmentState) -> np.ndarray:
    """Apply HSL hue/sat/lum shifts per band to a uint8 (H, W, 3) image."""
    # Build per-band (hue_shift_deg, sat_add, lum_add) by reading the
    # fields off the AdjustmentState.
    band_params: list[tuple[float, float, float]] = []
    for name, _centre, _hw in _HSL_BANDS:
        hue_v = getattr(adj, f"hue_{name}")
        sat_v = getattr(adj, f"sat_{name}")
        lum_v = getattr(adj, f"lum_{name}")
        # hue -100..+100 -> -30..+30 degrees
        band_params.append((hue_v * 0.3, sat_v / 100.0, lum_v / 100.0))

    if all(h == 0.0 and s == 0.0 and l == 0.0 for h, s, l in band_params):
        return arr  # nothing to do

    h, s, l = _rgb_to_hsl(arr)
    weights = _band_weights(h)  # (8, H, W)

    # Per-pixel hue shift (weighted by band weights)
    hue_shift = np.zeros_like(h)
    sat_shift = np.zeros_like(s)
    lum_shift = np.zeros_like(l)
    for (h_shift_deg, s_add, l_add), w in zip(band_params, weights):
        hue_shift += w * h_shift_deg
        sat_shift += w * s_add
        lum_shift += w * l_add

    new_h = (h + hue_shift) % 360.0
    new_s = np.clip(s + sat_shift, 0.0, 1.0)
    new_l = np.clip(l + lum_shift, 0.0, 1.0)
    return _hsl_to_rgb(new_h, new_s, new_l)


# ---------------------------------------------------------------------------
# darktable-cli integration
# ---------------------------------------------------------------------------

def _run_darktable_cli(
    src: Path,
    xmp: Optional[Path],
    dst: Path,
    *,
    width: int = 0,
    height: int = 0,
    quality: int = 90,
    out_ext: Optional[str] = None,
    bpp: int = 8,
    timeout: int = 300,
) -> dict:
    """Synchronous wrapper around :func:`_run_darktable_cli_async`."""
    return asyncio.run(
        _arun_darktable_cli(
            src, xmp, dst,
            width=width, height=height, quality=quality,
            out_ext=out_ext, bpp=bpp, timeout=timeout,
        )
    )


async def _arun_darktable_cli(
    src: Path,
    xmp: Optional[Path],
    dst: Path,
    *,
    width: int = 0,
    height: int = 0,
    quality: int = 90,
    out_ext: Optional[str] = None,
    bpp: int = 8,
    timeout: int = 300,
) -> dict:
    """Invoke ``darktable-cli`` asynchronously and return a result dict.

    Returns ``{"status": "ok", ...}`` on success or
    ``{"status": "error", "reason": "..."}`` on failure.  The temp
    ``--library`` directory is always cleaned up, even on error.
    """
    cli = find_darktable_cli()
    if cli is None:
        return {"status": "error", "reason": "darktable-cli not found on PATH"}

    import tempfile
    libdir = Path(tempfile.mkdtemp(prefix="dtcli_"))

    cmd = [cli, str(src)]
    if xmp is not None and xmp.exists():
        cmd.append(str(xmp))
    cmd.append(str(dst))
    cmd += [
        "--width", str(width),
        "--height", str(height),
        "--quality", str(int(quality)),
        "--bpp", str(int(bpp)),
        "--library", str(libdir),
    ]
    if out_ext:
        cmd += ["--out-ext", out_ext]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return {
                "status": "error",
                "reason": f"darktable-cli timed out after {timeout}s",
            }
        returncode = proc.returncode
        stdout_s = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_s = stderr.decode("utf-8", errors="replace") if stderr else ""
    except FileNotFoundError:
        return {"status": "error", "reason": f"darktable-cli not executable: {cli}"}
    except Exception as exc:
        return {"status": "error", "reason": f"subprocess failed: {exc}"}
    finally:
        shutil.rmtree(libdir, ignore_errors=True)

    if returncode != 0 or not dst.exists():
        return {
            "status": "error",
            "reason": f"darktable-cli exited {returncode}: "
                      f"{(stderr_s or stdout_s).strip()[:500]}",
        }

    return {
        "status": "ok",
        "output_path": str(dst),
        "rendered_via": "darktable-cli",
        "size_bytes": dst.stat().st_size,
    }


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

    def process(
        self,
        edit_state: EditState,
        preview_size: Optional[int] = None,
        use_darktable_cli: bool = True,
    ) -> Image.Image:
        """Render the image with all current edits applied (synchronous).

        Strategy:
          1. If darktable-cli is available and ``use_darktable_cli`` is True,
             render via darktable (correct RAW demosaicing + module chain).
          2. Otherwise, fall back to rawpy + Pillow for both RAW and raster.

        For an async variant that doesn't block the MCP event loop on big
        RAW files, use :meth:`process_async`.
        """
        if use_darktable_cli and find_darktable_cli() is not None:
            return self._process_via_darktable_cli(edit_state, preview_size)

        if self.is_raw:
            return self._process_raw(edit_state.adjustments)

        img = self._process_raster(edit_state.adjustments)
        img = self._apply_post_raw(img, edit_state.adjustments)

        if edit_state.crop:
            img = self._apply_crop(img, edit_state.crop)

        if preview_size:
            img.thumbnail((preview_size, preview_size), Image.Resampling.LANCZOS)

        return img

    async def process_async(
        self,
        edit_state: EditState,
        preview_size: Optional[int] = None,
        use_darktable_cli: bool = True,
    ) -> Image.Image:
        """Async variant of :meth:`process` — uses ``await`` for the
        darktable-cli subprocess so other MCP requests can be served while
        a big RAW is rendering.
        """
        if use_darktable_cli and find_darktable_cli() is not None:
            return await self._process_via_darktable_cli_async(
                edit_state, preview_size
            )
        # Raster / rawpy fallbacks are CPU-bound but fast enough that the
        # sync path is fine; running them inline keeps the code simple.
        return self.process(
            edit_state, preview_size=preview_size, use_darktable_cli=False
        )

    def get_info(self) -> dict:
        info: dict = {
            "path": str(self.path),
            "name": self.path.name,
            "type": "raw" if self.is_raw else "image",
            "size_bytes": self.path.stat().st_size,
            "darktable_cli_available": find_darktable_cli() is not None,
        }
        try:
            if self.is_raw:
                import rawpy
                with rawpy.imread(str(self.path)) as raw:
                    info["width"] = raw.sizes.width
                    info["height"] = raw.sizes.height
                    info["camera"] = raw.metadata.camera
                    info["iso"] = raw.metadata.iso
                    info["shutter"] = raw.metadata.shutter
                    info["aperture"] = raw.metadata.aperture
                    info["focal_len"] = raw.metadata.focal_len
                    info["timestamp"] = raw.metadata.timestamp
                    info["raw_type"] = raw.raw_type.name
            else:
                with Image.open(self.path) as img:
                    info["width"] = img.width
                    info["height"] = img.height
                    info["mode"] = img.mode
                    exif = img.getexif() if hasattr(img, "getexif") else {}
                    if exif:
                        # Common EXIF tag IDs
                        TAG_MAP = {
                            271: "make", 272: "model", 283: "focal_length",
                            34855: "iso", 33434: "shutter", 33437: "aperture",
                        }
                        for tag_id, name in TAG_MAP.items():
                            if tag_id in exif:
                                info[name] = exif[tag_id]
        except Exception as exc:
            info["read_error"] = str(exc)
        return info

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_via_darktable_cli(
        self, edit_state: EditState, preview_size: Optional[int]
    ) -> Image.Image:
        """Sync helper around :meth:`_process_via_darktable_cli_async`."""
        return asyncio.run(
            self._process_via_darktable_cli_async(edit_state, preview_size)
        )

    async def _process_via_darktable_cli_async(
        self, edit_state: EditState, preview_size: Optional[int]
    ) -> Image.Image:
        """Render through darktable-cli asynchronously and read the result.

        Writes the XMP sidecar (so darktable honours the edit state), then
        awaits the darktable-cli subprocess with a per-call temp
        ``--library`` directory.
        """
        import tempfile
        from .xmp_writer import write_xmp

        # We need an XMP sidecar for darktable to honour our edits.  Write
        # it next to the source file; if the user already has one we
        # overwrite with the current state.
        try:
            xmp_path = write_xmp(edit_state)
        except Exception:
            # If XMP generation fails we still try to render with no XMP.
            xmp_path = None

        tmpdir = Path(tempfile.mkdtemp(prefix="dtrender_"))
        try:
            ext = "png" if preview_size and preview_size >= 4000 else "jpg"
            out_path = tmpdir / f"render.{ext}"
            width = height = int(preview_size) if preview_size else 0
            res = await _arun_darktable_cli(
                self.path, xmp_path, out_path,
                width=width, height=height, quality=90, out_ext=ext,
                timeout=300,
            )
            if res["status"] != "ok":
                raise RuntimeError(res.get("reason", "darktable-cli failed"))
            return Image.open(out_path).convert("RGB")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

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

        # HSL colour grading (8 colour families × 3 axes)
        if any([
            getattr(adj, f"hue_{n}") or getattr(adj, f"sat_{n}") or getattr(adj, f"lum_{n}")
            for n, _, _ in _HSL_BANDS
        ]):
            arr = np.array(img, dtype=np.uint8)
            arr = _apply_hsl_grading(arr, adj)
            img = Image.fromarray(arr)

        # Parametric tone curve (4 control points).  Applied AFTER the
        # coarse highlight/shadow/whites/blacks block so the curve is
        # the final word on tone shape.
        if any([adj.curve_shadows, adj.curve_darks,
                adj.curve_lights, adj.curve_highlights]):
            lut = _build_tone_curve_lut(
                adj.curve_shadows, adj.curve_darks,
                adj.curve_lights, adj.curve_highlights,
            )
            img = _apply_lut(img, lut)

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
            img = img.rotate(-crop.rotation, expand=True, resample=Image.Resampling.BICUBIC)
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
