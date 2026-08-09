"""What is physically loaded in the U1's four tools, per the spool's RFID tag.

The U1 reads an RFID tag on each Snapmaker spool and publishes it over Moonraker
as the `filament_detect` object. The tag carries the vendor's own recommended
temperatures for that exact filament, which can differ from the generic
OrcaSlicer profile -- PLA SnapSpeed tags 230C for the first layer where
`Snapmaker PLA @U1` says 220C.

Third-party spools have no tag. The printer still reports the slot, but with
`MAIN_TYPE: "NONE"`. That is *unknown*, not empty and not "probably PLA", so it
is modelled as `None` and left to the profile.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "LoadedFilament",
    "MaterialMismatch",
    "TOOL_COUNT",
    "check_material_match",
    "parse_filament_detect",
]

TOOL_COUNT = 4

_UNTAGGED = "NONE"


class MaterialMismatch(Exception):
    """Raised when a job asks for a material the printer isn't holding."""


@dataclass(frozen=True)
class LoadedFilament:
    """One spool, as the printer's RFID reader sees it."""

    tool_index: int
    material: str
    sub_type: str
    color: str
    first_layer_temp: int
    other_layer_temp: int
    bed_temp: int
    hotend_min_temp: int
    hotend_max_temp: int
    vendor: str

    @property
    def label(self) -> str:
        """Human-facing name, e.g. 'PLA SnapSpeed'."""
        if self.sub_type and self.sub_type != _UNTAGGED:
            return f"{self.material} {self.sub_type}"
        return self.material


def _colour_from_argb(argb: object) -> str:
    try:
        return "#%06X" % (int(argb) & 0xFFFFFF)
    except (TypeError, ValueError):
        return "#FFFFFF"


def parse_filament_detect(info: list[dict]) -> list[LoadedFilament | None]:
    """Turn Moonraker's `filament_detect.info` into one entry per tool.

    Slot order is tool order. Untagged or empty slots come back as `None` --
    the printer cannot tell us what they hold, and guessing is how you print
    PLA at ABS temperatures.
    """
    slots: list[LoadedFilament | None] = [None] * TOOL_COUNT
    for index, tag in enumerate(info or []):
        if index >= TOOL_COUNT:
            break
        material = str(tag.get("MAIN_TYPE") or _UNTAGGED)
        if material == _UNTAGGED:
            continue
        slots[index] = LoadedFilament(
            tool_index=index,
            material=material,
            sub_type=str(tag.get("SUB_TYPE") or ""),
            color=_colour_from_argb(tag.get("ARGB_COLOR")),
            first_layer_temp=int(tag.get("FIRST_LAYER_TEMP") or 0),
            other_layer_temp=int(tag.get("OTHER_LAYER_TEMP") or 0),
            bed_temp=int(tag.get("BED_TEMP") or 0),
            hotend_min_temp=int(tag.get("HOTEND_MIN_TEMP") or 0),
            hotend_max_temp=int(tag.get("HOTEND_MAX_TEMP") or 0),
            vendor=str(tag.get("VENDOR") or ""),
        )
    return slots


def check_material_match(
    *,
    tool_index: int,
    requested: str,
    loaded: LoadedFilament | None,
) -> None:
    """Verify the job's filament agrees with the spool actually in that tool.

    `requested` may be a bare material ("PLA") or a full preset name
    ("Snapmaker PLA SnapSpeed @U1"); both are checked by looking for the loaded
    material as a word in the request.

    Raises:
        MaterialMismatch: if the tag names a different material.
    """
    if loaded is None:
        return  # untagged spool -- nothing to verify against
    if loaded.material.upper() in requested.upper():
        return
    raise MaterialMismatch(
        f"tool {tool_index + 1} holds {loaded.label} but the job asks for "
        f"{requested!r}. Load the right spool, or name the loaded material."
    )
