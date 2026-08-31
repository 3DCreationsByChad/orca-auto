"""Designate loaded spools as support body and/or support interface material.

Why this matters on a multi-tool machine: PETG barely bonds to PLA or ASA. Print
the support *interface* in PETG under a PLA part and it peels off in one piece --
which means the support gap can be closed to zero and the support-facing surface
comes out flat instead of scarred. The material incompatibility does the releasing
that an air gap normally does, badly.

That only holds while the materials genuinely don't bond. A PLA interface under a
PLA part welds, so the gap is left alone unless every part on the plate is a
different material from the interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orca_api.u1.loaded_filament import LoadedFilament
from orca_api.u1.tool_resolution import resolve_tool_by_material

__all__ = ["SupportSpec", "apply_support"]

#: Material pairs that do not bond well enough to weld, so a zero support gap is
#: safe. Deliberately conservative -- anything not listed keeps the air gap.
_RELEASES_FROM = {
    "PETG": {"PLA", "ASA", "ABS", "PLA-CF", "PA-CF"},
    "PLA": {"PETG", "PET"},
    "PET": {"PLA"},
    "PVA": {"PLA", "PETG", "ASA", "ABS"},
    "TPU": {"PLA", "PETG", "ASA", "ABS"},
}


@dataclass(frozen=True)
class SupportSpec:
    """Which spool prints the support body and which prints the interface.

    Each is either a material name ("PETG") resolved against the loaded spools,
    or an explicit 0-based tool index. Omit either to leave it as the slicer's
    default (the model's own filament).
    """

    body: str | int | None = None
    interface: str | int | None = None

    @classmethod
    def from_dict(cls, raw: dict | None) -> "SupportSpec | None":
        """Build from a job spec's `support` block. Returns None if unspecified."""
        if not raw:
            return None
        spec = cls(body=raw.get("body"), interface=raw.get("interface"))
        return spec if (spec.body is not None or spec.interface is not None) else None


def _tool_for(value: str | int, loaded: list[LoadedFilament | None]) -> int:
    """0-based tool index for a material name or an explicit index."""
    if isinstance(value, int):
        return value
    return resolve_tool_by_material(value, loaded)


def _material_of(tool_index: int, loaded: list[LoadedFilament | None]) -> str | None:
    if 0 <= tool_index < len(loaded) and loaded[tool_index] is not None:
        return loaded[tool_index].material.upper()
    return None


def _tokens(name: str) -> set[str]:
    """The words of a filament name, e.g. 'Snapmaker PLA SnapSpeed @U1' -> {..., 'PLA', ...}.

    Whole tokens, not substrings: 'PETG-CF' must not read as 'PLA' or 'PA-CF'
    just because the letters appear somewhere in the string.
    """
    return {t.strip(",;()").upper() for t in re.split(r"[\s/]+", name) if t.strip(",;()")}


def _declared_material(preset: str | None) -> str | None:
    """The releasing material named in a preset, or None if it names none.

    Only materials we hold release data for are recognised. Anything else would
    resolve to an empty safe-set and keep the gap regardless, so it is the same
    answer said earlier.
    """
    if not preset:
        return None
    for token in _tokens(preset):
        if token in _RELEASES_FROM:
            return token
    return None


def _releases_from_every_part(interface_material: str | None, part_materials: list[str]) -> bool:
    """True only if the interface will peel off *every* part on the plate.

    One part sharing the interface's material is enough to keep the air gap --
    that part would weld to its supports.

    `part_materials` are whatever the job named its parts' filament, which in
    practice is a full preset ("Snapmaker PLA SnapSpeed @U1"), not a bare
    material. Match on whole tokens so both spellings work.
    """
    if not interface_material or not part_materials:
        return False
    safe = _RELEASES_FROM.get(interface_material, set())
    if not safe:
        return False
    return all(safe & _tokens(m) for m in part_materials)


def apply_support(
    cfg: dict,
    spec: SupportSpec | None,
    loaded: list[LoadedFilament | None],
    *,
    part_materials: list[str],
    declared: dict[int, str] | None = None,
) -> None:
    """Apply a support spec to a project config, in place.

    Args:
        part_materials: the material of every part on the plate, used to decide
            whether a zero support gap is safe.
        declared: the job's `filaments` block, 0-based tool -> preset name. Names
            what an untagged spool holds. Only consulted when the printer's RFID
            reader cannot say, and the tag wins whenever it can.

    Raises:
        ToolResolutionError: if a named material is not loaded.
    """
    if spec is None:
        return

    if spec.body is not None:
        # str, not int: OrcaSlicer's config is stringly typed and silently keeps
        # the old value for a type it does not expect. Writing 1 here produced a
        # .3mf saying enable_support=1 and G-code reporting enable_support=0,
        # with no error anywhere. Read the setting back out of the G-code.
        cfg["support_filament"] = str(_tool_for(spec.body, loaded) + 1)  # 1-based
        cfg["enable_support"] = "1"

    if spec.interface is not None:
        tool = _tool_for(spec.interface, loaded)
        cfg["support_interface_filament"] = str(tool + 1)
        cfg["enable_support"] = "1"

        # Measured beats declared: the RFID tag is what the printer read off the
        # spool, the `filaments` block is what someone typed. Fall through to the
        # declaration only for an untagged spool, where there is nothing to read.
        if isinstance(spec.interface, str):
            interface_material = spec.interface.strip().upper()
        else:
            interface_material = _material_of(tool, loaded) or _declared_material(
                (declared or {}).get(tool)
            )
        if _releases_from_every_part(interface_material, part_materials):
            # the materials won't bond, so the interface can touch the part
            cfg["support_top_z_distance"] = "0"
            cfg["support_bottom_z_distance"] = "0"
