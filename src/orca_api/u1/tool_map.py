"""Models for U1 4-tool color/filament assignment."""

from collections.abc import Sequence
from dataclasses import dataclass

# The Snapmaker U1 is a 4-tool toolchanger.
U1_TOOL_COUNT = 4


@dataclass(frozen=True)
class ToolAssignment:
    """Assignment of one object/filament to one of the U1's tools."""

    tool_index: int
    filament: str
    color: str | None = None


@dataclass(frozen=True)
class ToolMap:
    """Immutable, validated set of tool assignments for one U1 job."""

    assignments: tuple[ToolAssignment, ...]


def build_tool_map(assignments: Sequence[ToolAssignment]) -> ToolMap:
    """Validate and construct a ToolMap.

    Raises:
        ValueError: if the assignment set is empty, has an out-of-range tool
            index, has a duplicate tool index, or has an empty filament.
    """
    if not assignments:
        raise ValueError("ToolMap requires at least one assignment")

    seen: set[int] = set()
    for a in assignments:
        if not (0 <= a.tool_index < U1_TOOL_COUNT):
            raise ValueError(
                f"tool_index {a.tool_index} out of range (must be 0..{U1_TOOL_COUNT - 1})"
            )
        if a.tool_index in seen:
            raise ValueError(f"duplicate tool_index {a.tool_index}")
        seen.add(a.tool_index)
        if not a.filament.strip():
            raise ValueError(f"empty filament for tool_index {a.tool_index}")

    return ToolMap(assignments=tuple(assignments))
