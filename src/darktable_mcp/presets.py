"""Named edit presets — a small DSL for "looks".

A preset is a JSON file with two sections:

  - ``adjustments``: same keys as ``darktable_mcp.edits.AdjustmentState``,
    only the ones you set override the image's current values.
  - ``crop``: optional ``CropState`` (left, top, right, bottom, rotation).
  - ``output_name``: optional rename template.  ``{stem}`` is replaced
    with the source filename stem.

Presets live in the ``presets/`` directory next to the package, OR in
any directory passed via the ``DARKTABLE_MCP_PRESETS`` env var.  The
``list_presets`` tool merges them with a stable ordering (env dir first,
then built-in) and dedupes by name.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .edits import AdjustmentState, CropState, EditState, validate_param


_BUILTIN_PRESETS_DIR = Path(__file__).resolve().parent.parent.parent / "presets"


# ---------------------------------------------------------------------------
# Preset discovery
# ---------------------------------------------------------------------------

def _candidate_dirs() -> list[Path]:
    """Return preset directories in priority order: env var, then built-in."""
    dirs: list[Path] = []
    env = os.environ.get("DARKTABLE_MCP_PRESETS")
    if env:
        for part in env.split(os.pathsep):
            p = Path(part).expanduser().resolve()
            if p.is_dir():
                dirs.append(p)
    if _BUILTIN_PRESETS_DIR.is_dir():
        dirs.append(_BUILTIN_PRESETS_DIR)
    return dirs


def list_preset_names() -> list[dict[str, str]]:
    """Return ``[{"name": ..., "path": ..., "source": "builtin"|"user"}]``."""
    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for d in _candidate_dirs():
        for path in sorted(d.glob("*.json")):
            name = path.stem
            if name in seen:
                continue
            seen.add(name)
            results.append({
                "name": name,
                "path": str(path),
                "source": "builtin" if d == _BUILTIN_PRESETS_DIR else "user",
            })
    return results


def load_preset(name: str) -> dict[str, Any]:
    """Load a preset by name.  Raises ``FileNotFoundError`` if missing,
    ``ValueError`` on parse / validation failure.
    """
    for d in _candidate_dirs():
        candidate = d / f"{name}.json"
        if candidate.exists():
            try:
                with open(candidate) as f:
                    data = json.load(f)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Preset {name!r} is not valid JSON: {exc}") from exc
            _validate_preset(name, data)
            return data
    raise FileNotFoundError(
        f"Preset {name!r} not found.  "
        f"Searched: {[str(d) for d in _candidate_dirs()]}"
    )


def _validate_preset(name: str, data: dict[str, Any]) -> None:
    """Raise ValueError if *data* doesn't conform to the preset schema."""
    if not isinstance(data, dict):
        raise ValueError(f"Preset {name!r} must be a JSON object")
    if "adjustments" in data:
        adj = data["adjustments"]
        if not isinstance(adj, dict):
            raise ValueError(f"Preset {name!r}: 'adjustments' must be an object")
        valid_keys = {f.name for f in AdjustmentState.__dataclass_fields__.values()}
        for k, v in adj.items():
            if k not in valid_keys:
                raise ValueError(
                    f"Preset {name!r}: unknown adjustment {k!r} "
                    f"(valid keys: {sorted(valid_keys)})"
                )
            validate_param(k, v)
    if "crop" in data and data["crop"] is not None:
        crop = data["crop"]
        if not isinstance(crop, dict):
            raise ValueError(f"Preset {name!r}: 'crop' must be an object")
        for k in ("left", "top", "right", "bottom", "rotation"):
            if k in crop and not isinstance(crop[k], (int, float)):
                raise ValueError(
                    f"Preset {name!r}: crop.{k} must be a number"
                )
    if "output_name" in data:
        if data["output_name"] is not None and not isinstance(data["output_name"], str):
            raise ValueError(f"Preset {name!r}: 'output_name' must be a string or null")


# ---------------------------------------------------------------------------
# Apply preset to an EditState
# ---------------------------------------------------------------------------

def _substitute_output_name(template: str, source_path: Path) -> str:
    """Replace ``{stem}`` and ``{ext}`` in the output_name template."""
    out = template
    out = out.replace("{stem}", source_path.stem)
    out = out.replace("{ext}", source_path.suffix.lstrip(".").lower())
    out = out.replace("{name}", source_path.name)
    return re.sub(r"[^A-Za-z0-9._-]", "_", out)


def apply_preset(preset: dict[str, Any], state: EditState) -> None:
    """Mutate *state* in place by applying a preset dict.

    Only fields present in the preset are touched; everything else keeps
    its current value.  All numeric values pass through
    :func:`edits.validate_param` first.
    """
    adj = preset.get("adjustments") or {}
    for k, v in adj.items():
        validate_param(k, v)
        setattr(state.adjustments, k, v)

    crop = preset.get("crop")
    if crop is not None:
        merged = asdict(state.crop) if state.crop else {}
        merged.update({k: v for k, v in crop.items() if v is not None})
        state.crop = CropState(**merged)

    if "output_name" in preset and preset["output_name"] is not None:
        state.output_name = _substitute_output_name(
            preset["output_name"], state.source_path
        )
