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


def _releases_from_every_part(interface_material: str | None, part_materials: list[str]) -> bool:
    """True only if the interface will peel off *every* part on the plate.

    One part sharing the interface's material is enough to keep the air gap --
    that part would weld to its supports.
    """
    if not interface_material or not part_materials:
        return False
    safe = _RELEASES_FROM.get(interface_material, set())
    return all(m.strip().upper() in safe for m in part_materials)


def apply_support(
    cfg: dict,
    spec: SupportSpec | None,
    loaded: list[LoadedFilament | None],
    *,
    part_materials: list[str],
) -> None:
    """Apply a support spec to a project config, in place.

    Args:
        part_materials: the material of every part on the plate, used to decide
            whether a zero support gap is safe.

    Raises:
        ToolResolutionError: if a named material is not loaded.
    """
    if spec is None:
        return

    if spec.body is not None:
        cfg["support_filament"] = _tool_for(spec.body, loaded) + 1  # OrcaSlicer is 1-based
        cfg["enable_support"] = 1

    if spec.interface is not None:
        tool = _tool_for(spec.interface, loaded)
        cfg["support_interface_filament"] = tool + 1
        cfg["enable_support"] = 1

        interface_material = (
            spec.interface.strip().upper()
            if isinstance(spec.interface, str)
            else _material_of(tool, loaded)
        )
        if _releases_from_every_part(interface_material, part_materials):
            # the materials won't bond, so the interface can touch the part
            cfg["support_top_z_distance"] = 0
            cfg["support_bottom_z_distance"] = 0
