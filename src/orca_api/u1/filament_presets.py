"""Resolve real OrcaSlicer filament presets out of the installed vendor bundle.

Why this exists: the U1 project `.3mf` we build embeds a *fully resolved* config
(that is what lets it bypass OrcaSlicer's vendor compatibility check). OrcaSlicer
therefore slices from the values we embed -- writing a preset *name* into
`filament_settings_id` changes nothing. Every thermal, flow and cooling value has
to be spliced in per slot, and the honest source for those numbers is Snapmaker's
own profile bundle that ships with OrcaSlicer.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "FilamentPresetError",
    "available_presets",
    "resolve_filament_preset",
    "resolve_preset_name",
]

DEFAULT_VENDOR = "Snapmaker"

#: Keys that describe the preset itself rather than how to print with it.
_META_KEYS = frozenset({
    "type",
    "name",
    "inherits",
    "from",
    "setting_id",
    "filament_id",
    "instantiation",
    "compatible_printers",
    "compatible_printers_condition",
    "compatible_prints",
    "compatible_prints_condition",
    "version",
})

_MAX_INHERITS_DEPTH = 32


class FilamentPresetError(Exception):
    """Raised when a filament preset cannot be resolved from the vendor bundle."""


def _filament_dir(datadir: str | Path, vendor: str) -> Path:
    return Path(datadir) / "system" / vendor / "filament"


def available_presets(datadir: str | Path, vendor: str = DEFAULT_VENDOR) -> list[str]:
    """Every filament preset name the vendor bundle offers, sorted."""
    fdir = _filament_dir(datadir, vendor)
    if not fdir.is_dir():
        return []
    return sorted(p.stem for p in fdir.glob("*.json"))


def _read_profile(fdir: Path, name: str) -> dict:
    path = fdir / f"{name}.json"
    if not path.is_file():
        raise FilamentPresetError(
            f"filament preset {name!r} not found at {path}"
        )
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise FilamentPresetError(f"filament preset {name!r} is not valid JSON: {exc}") from exc


def _resolve_name(fdir: Path, requested: str, vendor: str) -> str:
    """Map a job-spec filament to a concrete preset name.

    Accepts either a full preset name ("Snapmaker PLA Matte @U1") or a bare
    material ("PLA"), which resolves to that vendor's U1 profile.
    """
    for candidate in (requested, f"{vendor} {requested} @U1"):
        if (fdir / f"{candidate}.json").is_file():
            return candidate

    known = sorted(p.stem for p in fdir.glob("*.json")) if fdir.is_dir() else []
    u1_only = [n for n in known if n.endswith("@U1")]
    raise FilamentPresetError(
        f"no filament preset matches {requested!r} in {fdir}. "
        f"Available U1 presets: {', '.join(u1_only) or '(none)'}"
    )


def resolve_preset_name(
    filament: str,
    datadir: str | Path,
    vendor: str = DEFAULT_VENDOR,
) -> str:
    """Concrete preset name a job-spec filament resolves to.

    Raises:
        FilamentPresetError: if nothing in the bundle matches.
    """
    return _resolve_name(_filament_dir(datadir, vendor), filament, vendor)


def resolve_filament_preset(
    filament: str,
    datadir: str | Path,
    vendor: str = DEFAULT_VENDOR,
) -> dict:
    """Return the flattened print settings for `filament`.

    Walks the preset's `inherits` chain (parent first, child overrides) and drops
    preset metadata, leaving only keys that describe how to print the material.

    Raises:
        FilamentPresetError: unknown filament, missing parent, or an `inherits` cycle.
    """
    fdir = _filament_dir(datadir, vendor)
    name = _resolve_name(fdir, filament, vendor)

    chain: list[dict] = []
    seen: list[str] = []
    current: str | None = name
    while current is not None:
        if current in seen:
            raise FilamentPresetError(
                f"inherits cycle in filament preset chain: {' -> '.join([*seen, current])}"
            )
        seen.append(current)
        if len(seen) > _MAX_INHERITS_DEPTH:
            raise FilamentPresetError(
                f"inherits chain for {name!r} exceeds {_MAX_INHERITS_DEPTH} levels"
            )
        profile = _read_profile(fdir, current)
        chain.append(profile)
        current = profile.get("inherits") or None

    merged: dict = {}
    for profile in reversed(chain):  # root ancestor first, child last
        merged.update(profile)

    return {k: v for k, v in merged.items() if k not in _META_KEYS}
