"""Edit state dataclasses with persistence and validation."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Parameter ranges (single source of truth for validation + docstrings)
# ---------------------------------------------------------------------------

RANGES: dict[str, tuple[float, float]] = {
    "exposure_ev":         (-5.0,   5.0),
    "black_level":         ( 0.0,   0.5),
    "highlight_recovery":  ( 0.0,   1.0),
    "shadow_lift":         ( 0.0,   1.0),
    "temperature_kelvin":  (2000.0, 12000.0),
    "tint":                (-100.0, 100.0),
    "contrast":            (-100.0, 100.0),
    "brightness":          (-100.0, 100.0),
    "highlights":          (-100.0, 100.0),
    "shadows":             (-100.0, 100.0),
    "whites":              (-100.0, 100.0),
    "blacks":              (-100.0, 100.0),
    "saturation":          (-100.0, 100.0),
    "vibrance":            (-100.0, 100.0),
    "sharpness":           (  0.0, 100.0),
    "noise_reduction":     (  0.0, 100.0),
    "vignette":            (-100.0, 100.0),
    "clarity":             (-100.0, 100.0),
    # Parametric tone curve
    "curve_shadows":       (-100.0, 100.0),
    "curve_darks":         (-100.0, 100.0),
    "curve_lights":        (-100.0, 100.0),
    "curve_highlights":    (-100.0, 100.0),
    # HSL color grading (8 colors × 3 axes)
    "hue_red":             (-100.0, 100.0),
    "hue_orange":          (-100.0, 100.0),
    "hue_yellow":          (-100.0, 100.0),
    "hue_green":           (-100.0, 100.0),
    "hue_aqua":            (-100.0, 100.0),
    "hue_blue":            (-100.0, 100.0),
    "hue_purple":          (-100.0, 100.0),
    "hue_magenta":         (-100.0, 100.0),
    "sat_red":             (-100.0, 100.0),
    "sat_orange":          (-100.0, 100.0),
    "sat_yellow":          (-100.0, 100.0),
    "sat_green":           (-100.0, 100.0),
    "sat_aqua":            (-100.0, 100.0),
    "sat_blue":            (-100.0, 100.0),
    "sat_purple":          (-100.0, 100.0),
    "sat_magenta":         (-100.0, 100.0),
    "lum_red":             (-100.0, 100.0),
    "lum_orange":          (-100.0, 100.0),
    "lum_yellow":          (-100.0, 100.0),
    "lum_green":           (-100.0, 100.0),
    "lum_aqua":            (-100.0, 100.0),
    "lum_blue":            (-100.0, 100.0),
    "lum_purple":          (-100.0, 100.0),
    "lum_magenta":         (-100.0, 100.0),
}


class ValidationError(ValueError):
    """Raised when a parameter value is outside its allowed range."""


def validate_param(name: str, value: Any) -> None:
    """Raise ValidationError if *value* is outside the range declared in RANGES."""
    if name not in RANGES:
        return
    lo, hi = RANGES[name]
    if not isinstance(value, (int, float)):
        raise ValidationError(f"{name!r} must be a number, got {type(value).__name__}")
    if not (lo <= float(value) <= hi):
        raise ValidationError(f"{name!r}={value} is out of range [{lo}, {hi}]")


# ---------------------------------------------------------------------------
# State dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CropState:
    left: float = 0.0      # normalised 0.0–1.0
    top: float = 0.0
    right: float = 1.0
    bottom: float = 1.0
    rotation: float = 0.0  # degrees, clockwise


@dataclass
class AdjustmentState:
    # --- Exposure ---
    exposure_ev: float = 0.0          # stops, -5 to +5
    black_level: float = 0.0          # 0.0–0.5
    highlight_recovery: float = 0.0   # 0.0–1.0
    shadow_lift: float = 0.0          # 0.0–1.0

    # --- White balance ---
    temperature_kelvin: Optional[float] = None  # None = use camera WB
    tint: float = 0.0                           # -100 to +100 (green↔magenta)

    # --- Tone ---
    contrast: float = 0.0     # -100 to +100
    brightness: float = 0.0   # -100 to +100
    highlights: float = 0.0   # -100 to +100
    shadows: float = 0.0      # -100 to +100
    whites: float = 0.0       # -100 to +100
    blacks: float = 0.0       # -100 to +100

    # --- Parametric tone curve (additive on top of the tone block) ---
    curve_shadows: float = 0.0     # -100 to +100 (lift / crush blacks)
    curve_darks: float = 0.0       # -100 to +100 (lower midtones)
    curve_lights: float = 0.0      # -100 to +100 (raise midtones)
    curve_highlights: float = 0.0  # -100 to +100 (pull highlights)

    # --- Colour ---
    saturation: float = 0.0   # -100 to +100
    vibrance: float = 0.0     # -100 to +100

    # --- HSL color grading (8 colour families × 3 axes) ---
    # Hue: shift the colour around the wheel (-100..+100 → ±30°)
    # Sat: boost or cut saturation within the colour band
    # Lum: brighten or darken within the colour band
    hue_red: float = 0.0
    hue_orange: float = 0.0
    hue_yellow: float = 0.0
    hue_green: float = 0.0
    hue_aqua: float = 0.0
    hue_blue: float = 0.0
    hue_purple: float = 0.0
    hue_magenta: float = 0.0
    sat_red: float = 0.0
    sat_orange: float = 0.0
    sat_yellow: float = 0.0
    sat_green: float = 0.0
    sat_aqua: float = 0.0
    sat_blue: float = 0.0
    sat_purple: float = 0.0
    sat_magenta: float = 0.0
    lum_red: float = 0.0
    lum_orange: float = 0.0
    lum_yellow: float = 0.0
    lum_green: float = 0.0
    lum_aqua: float = 0.0
    lum_blue: float = 0.0
    lum_purple: float = 0.0
    lum_magenta: float = 0.0

    # --- Detail ---
    sharpness: float = 0.0      # 0 to 100
    noise_reduction: float = 0.0  # 0 to 100

    # --- Effects ---
    vignette: float = 0.0   # -100 to +100 (negative = dark edges)
    clarity: float = 0.0    # -100 to +100 (local contrast)


@dataclass
class EditState:
    source_path: Path
    adjustments: AdjustmentState = field(default_factory=AdjustmentState)
    crop: Optional[CropState] = None
    output_name: Optional[str] = None  # stem only, no extension

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _sidecar(image_path: Path) -> Path:
        return image_path.with_name(image_path.stem + ".mcp.json")

    @classmethod
    def load(cls, image_path: Path) -> Optional[EditState]:
        sidecar = cls._sidecar(image_path)
        if not sidecar.exists():
            return None
        try:
            with open(sidecar) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        state = cls(source_path=image_path)
        adj_data = data.get("adjustments", {})
        valid = {k: v for k, v in adj_data.items() if hasattr(state.adjustments, k)}
        state.adjustments = AdjustmentState(**valid)
        crop_data = data.get("crop")
        state.crop = CropState(**crop_data) if crop_data else None
        state.output_name = data.get("output_name")
        return state

    def save(self) -> Path:
        sidecar = self._sidecar(self.source_path)
        data = {
            "adjustments": asdict(self.adjustments),
            "crop": asdict(self.crop) if self.crop else None,
            "output_name": self.output_name,
        }
        with open(sidecar, "w") as f:
            json.dump(data, f, indent=2)
        return sidecar

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def update(self, params: dict, *, validate: bool = True) -> None:
        """Apply *params* to adjustments. Raises ValidationError on bad input."""
        for key, value in params.items():
            if not hasattr(self.adjustments, key):
                continue
            if validate:
                validate_param(key, value)
            setattr(self.adjustments, key, value)

    def has_changes(self) -> bool:
        defaults = AdjustmentState()
        a = self.adjustments
        # The new fields (curve_*, hue_*, sat_*, lum_*) live on the
        # dataclass too; a simple dataclass comparison covers all of
        # them without having to enumerate.
        return any([
            a.exposure_ev != defaults.exposure_ev,
            a.contrast != defaults.contrast,
            a.saturation != defaults.saturation,
            a.temperature_kelvin is not None,
            a.sharpness != defaults.sharpness,
            a.noise_reduction != defaults.noise_reduction,
            a.vignette != defaults.vignette,
            a.clarity != defaults.clarity,
            a.curve_shadows != defaults.curve_shadows,
            a.curve_darks != defaults.curve_darks,
            a.curve_lights != defaults.curve_lights,
            a.curve_highlights != defaults.curve_highlights,
            a.hue_red != defaults.hue_red,
            a.sat_red != defaults.sat_red,
            a.lum_red != defaults.lum_red,
            self.crop is not None,
            self.output_name is not None,
        ])

    def to_dict(self) -> dict:
        return {
            "adjustments": asdict(self.adjustments),
            "crop": asdict(self.crop) if self.crop else None,
            "output_name": self.output_name,
        }

    def sidecar_paths(self) -> list[Path]:
        """All sidecar files owned by this edit state (.mcp.json + .xmp)."""
        paths = [self._sidecar(self.source_path)]
        xmp = self.source_path.with_suffix(".xmp")
        if xmp.exists():
            paths.append(xmp)
        return paths

    # ------------------------------------------------------------------
    # Undo / redo via history sidecars
    # ------------------------------------------------------------------

    @staticmethod
    def _history_dir(image_path: Path) -> Path:
        return image_path.with_name(image_path.stem + ".mcp.history")

    def save_with_history(self, *, keep: int = 20) -> Path:
        """Persist *self* and snapshot the previous sidecar into history.

        The current sidecar is moved to ``<stem>.mcp.history/<UTC>.json``;
        the next call to :meth:`EditState.load` will return the new state.
        The history directory is FIFO-trimmed to *keep* entries so it
        doesn't grow unbounded.
        """
        current = self._sidecar(self.source_path)
        hist_dir = self._history_dir(self.source_path)
        hist_dir.mkdir(parents=True, exist_ok=True)

        if current.exists():
            from datetime import datetime, timezone
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
            snapshot = hist_dir / f"{stamp}.json"
            # If the same microsecond collides, bump.
            n = 1
            while snapshot.exists():
                snapshot = hist_dir / f"{stamp}_{n}.json"
                n += 1
            snapshot.write_bytes(current.read_bytes())
            # FIFO-trim
            entries = sorted(hist_dir.glob("*.json"), key=lambda p: p.name)
            for old in entries[:-keep]:
                old.unlink()

        return self.save()

    def list_history(self) -> list[Path]:
        """Return the history sidecars in chronological order (oldest first)."""
        hist_dir = self._history_dir(self.source_path)
        if not hist_dir.is_dir():
            return []
        return sorted(hist_dir.glob("*.json"), key=lambda p: p.name)

    def restore_history_entry(self, entry: Path, *, snapshot: bool = True) -> Path:
        """Replace the current sidecar with *entry*.

        When *snapshot* is True (default), the current sidecar is
        committed to history first — useful for "save current and
        restore" flows.  When *snapshot* is False (e.g. for undo), the
        current sidecar is overwritten in place with no history push.
        """
        if not entry.exists():
            raise FileNotFoundError(f"History entry not found: {entry}")
        if snapshot:
            self.save_with_history()
        current = self._sidecar(self.source_path)
        current.write_bytes(entry.read_bytes())
        return current
