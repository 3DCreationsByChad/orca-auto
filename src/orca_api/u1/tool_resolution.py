"""Resolve a tool by what is loaded in it, rather than by slot number.

A job spec that hard-codes `tool_index` is only correct until someone reloads the
spools in a different order. The printer already knows each tool's material and
colour from its RFID tags, so a job can ask for "the black one" or "the PETG one"
and have it resolved at slice time.

Both features that need this ask the same question -- which tool holds the thing I
want -- so colour assignment and support-interface material share one resolver.

Untagged spools are unresolvable by design: the printer reports them as unknown,
and picking one anyway is how you print PLA at PETG temperatures.
"""

from __future__ import annotations

from orca_api.u1.loaded_filament import LoadedFilament

__all__ = [
    "ToolResolutionError",
    "to_hex",
    "resolve_tool_by_color",
    "resolve_tool_by_material",
]

#: Named colours accepted in a job spec, as sRGB. Deliberately small -- these are
#: filament colours, not a CSS palette.
_NAMED_COLORS = {
    "black": (0x00, 0x00, 0x00),
    "white": (0xFF, 0xFF, 0xFF),
    "grey": (0x80, 0x80, 0x80),
    "gray": (0x80, 0x80, 0x80),
    "silver": (0xC0, 0xC0, 0xC0),
    "red": (0xD0, 0x21, 0x21),
    "orange": (0xF5, 0x7C, 0x00),
    "yellow": (0xF5, 0xC8, 0x1A),
    "green": (0x2E, 0xA0, 0x44),
    "blue": (0x1E, 0x6F, 0xD9),
    "purple": (0x7B, 0x2F, 0xBE),
    "pink": (0xE8, 0x5A, 0xA8),
    "brown": (0x6B, 0x45, 0x26),
    "natural": (0xEC, 0xE3, 0xD2),
}

#: Max squared RGB distance still considered "that colour". Roughly a 100-unit
#: euclidean radius -- generous enough for "yellow" to match a warm amber, tight
#: enough that "blue" does not match black.
_MATCH_CEILING = 100 ** 2

#: Two loaded spools closer than this to each other are indistinguishable, so a
#: request matching both is refused rather than resolved to whichever sorts first.
_AMBIGUITY_FLOOR = 12 ** 2


class ToolResolutionError(Exception):
    """Raised when a job asks for a tool that cannot be identified."""


def _rgb(value: str) -> tuple[int, int, int] | None:
    text = value.strip().lstrip("#").lower()
    if text in _NAMED_COLORS:
        return _NAMED_COLORS[text]
    if len(text) == 6:
        try:
            n = int(text, 16)
        except ValueError:
            return None
        return ((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF)
    return None


def to_hex(value: str) -> str | None:
    """Normalise a hex value or a known colour name to '#RRGGBB'. None if neither."""
    rgb = _rgb(value)
    return None if rgb is None else "#%02X%02X%02X" % rgb


def _distance_sq(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _tagged(loaded: list[LoadedFilament | None]) -> list[LoadedFilament]:
    return [s for s in loaded if s is not None]


def resolve_tool_by_color(color: str, loaded: list[LoadedFilament | None]) -> int:
    """Return the tool index holding `color` (a hex value or a common name).

    Raises:
        ToolResolutionError: if nothing is tagged, the colour is unparseable, no
            spool is close enough, or two spools are too alike to choose between.
    """
    spools = _tagged(loaded)
    if not spools:
        raise ToolResolutionError(
            f"cannot resolve colour {color!r}: no tagged spools are loaded. "
            "Untagged spools can't be identified by the printer -- give an "
            "explicit tool_index instead."
        )

    want = _rgb(color)
    if want is None:
        raise ToolResolutionError(
            f"{color!r} is not a hex colour or a known name "
            f"({', '.join(sorted(_NAMED_COLORS))})"
        )

    scored = sorted(
        ((_distance_sq(want, _rgb(s.color) or (0, 0, 0)), s) for s in spools),
        key=lambda pair: (pair[0], pair[1].tool_index),
    )
    best_distance, best = scored[0]

    if best_distance > _MATCH_CEILING:
        loaded_desc = ", ".join(f"tool {s.tool_index + 1} {s.color}" for s in spools)
        raise ToolResolutionError(
            f"no loaded spool matches colour {color!r}. Loaded: {loaded_desc}"
        )

    for distance, other in scored[1:]:
        if _distance_sq(_rgb(best.color) or (0, 0, 0), _rgb(other.color) or (0, 0, 0)) <= _AMBIGUITY_FLOOR:
            raise ToolResolutionError(
                f"ambiguous colour {color!r}: tool {best.tool_index + 1} ({best.color}) "
                f"and tool {other.tool_index + 1} ({other.color}) are too alike to "
                "choose between. Use an explicit tool_index."
            )
        break

    return best.tool_index


def resolve_tool_by_material(material: str, loaded: list[LoadedFilament | None]) -> int:
    """Return the lowest tool index holding `material` (e.g. "PETG").

    Duplicate materials are not ambiguous -- either spool will do -- so this is
    deterministic rather than an error.

    Raises:
        ToolResolutionError: if no tagged spool holds that material.
    """
    spools = _tagged(loaded)
    for spool in sorted(spools, key=lambda s: s.tool_index):
        if spool.material.upper() == material.strip().upper():
            return spool.tool_index

    loaded_desc = (
        ", ".join(f"tool {s.tool_index + 1} {s.label}" for s in spools) or "(none tagged)"
    )
    raise ToolResolutionError(
        f"no loaded spool is {material.upper()}. Loaded: {loaded_desc}"
    )
