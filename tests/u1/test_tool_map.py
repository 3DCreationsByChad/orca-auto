import pytest

from orca_api.u1.tool_map import ToolAssignment, U1_TOOL_COUNT, build_tool_map


def test_tool_assignment_holds_fields():
    a = ToolAssignment(tool_index=0, filament="Generic PLA", color="#FF0000")
    assert a.tool_index == 0
    assert a.filament == "Generic PLA"
    assert a.color == "#FF0000"


def test_color_is_optional():
    a = ToolAssignment(tool_index=1, filament="Generic PETG")
    assert a.color is None


def test_u1_has_four_tools():
    assert U1_TOOL_COUNT == 4


def test_build_tool_map_happy_path():
    tm = build_tool_map([
        ToolAssignment(tool_index=0, filament="PLA Red", color="#FF0000"),
        ToolAssignment(tool_index=1, filament="PLA Blue", color="#0000FF"),
    ])
    assert len(tm.assignments) == 2
    assert tm.assignments[0].tool_index == 0


def test_build_tool_map_is_immutable():
    tm = build_tool_map([ToolAssignment(tool_index=0, filament="PLA")])
    assert isinstance(tm.assignments, tuple)


def test_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        build_tool_map([])


def test_rejects_out_of_range_index():
    with pytest.raises(ValueError, match="0..3"):
        build_tool_map([ToolAssignment(tool_index=4, filament="PLA")])


def test_rejects_negative_index():
    with pytest.raises(ValueError, match="0..3"):
        build_tool_map([ToolAssignment(tool_index=-1, filament="PLA")])


def test_rejects_duplicate_index():
    with pytest.raises(ValueError, match="duplicate"):
        build_tool_map([
            ToolAssignment(tool_index=0, filament="PLA"),
            ToolAssignment(tool_index=0, filament="PETG"),
        ])


def test_rejects_empty_filament():
    with pytest.raises(ValueError, match="filament"):
        build_tool_map([ToolAssignment(tool_index=0, filament="")])
