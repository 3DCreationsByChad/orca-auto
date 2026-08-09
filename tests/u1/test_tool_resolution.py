"""Resolve a tool by what is loaded in it, rather than by slot number.

Job specs that hard-code `tool_index` break the moment spools are reloaded in a
different order. The printer already knows each tool's material and colour from
the RFID tags, so "the black one" and "the PETG one" can be resolved at slice time.
"""

import pytest

from orca_api.u1.loaded_filament import parse_filament_detect
from orca_api.u1.tool_resolution import (
    ToolResolutionError,
    resolve_tool_by_color,
    resolve_tool_by_material,
)


def _tag(material="PLA", sub="SnapSpeed", argb=0xFF080A0D):
    return {"MAIN_TYPE": material, "SUB_TYPE": sub, "ARGB_COLOR": argb,
            "FIRST_LAYER_TEMP": 230, "OTHER_LAYER_TEMP": 220, "BED_TEMP": 60,
            "HOTEND_MIN_TEMP": 190, "HOTEND_MAX_TEMP": 230, "VENDOR": "Snapmaker"}


EMPTY = {"MAIN_TYPE": "NONE", "SUB_TYPE": "NONE", "ARGB_COLOR": 0xFFFFFFFF}


def _loaded(*tags):
    return parse_filament_detect(list(tags))


# --- colour -----------------------------------------------------------------

def test_exact_hex_match_finds_the_tool():
    loaded = _loaded(_tag(argb=0xFF080A0D), _tag(argb=0xFFF4C032))

    assert resolve_tool_by_color("#F4C032", loaded) == 1


def test_hex_match_is_case_and_hash_insensitive():
    loaded = _loaded(_tag(argb=0xFF080A0D))

    assert resolve_tool_by_color("080a0d", loaded) == 0


def test_common_colour_names_resolve_to_the_nearest_spool():
    loaded = _loaded(_tag(argb=0xFF080A0D), _tag(argb=0xFFF4C032))

    assert resolve_tool_by_color("black", loaded) == 0
    assert resolve_tool_by_color("yellow", loaded) == 1


def test_a_colour_nothing_is_close_to_is_refused():
    """No blue is loaded -- say so rather than returning the least-wrong tool."""
    loaded = _loaded(_tag(argb=0xFF080A0D), _tag(argb=0xFFF4C032))

    with pytest.raises(ToolResolutionError, match="blue"):
        resolve_tool_by_color("blue", loaded)


def test_two_indistinguishable_spools_are_refused_not_guessed():
    loaded = _loaded(_tag(argb=0xFFD62828), _tag(argb=0xFFD62A29))

    with pytest.raises(ToolResolutionError, match="[Aa]mbiguous"):
        resolve_tool_by_color("#D62828", loaded)


def test_untagged_spools_cannot_be_resolved_by_colour():
    """The printer does not know what an untagged spool is -- say that plainly."""
    loaded = _loaded(EMPTY, EMPTY, EMPTY, EMPTY)

    with pytest.raises(ToolResolutionError, match="not tagged|no tagged"):
        resolve_tool_by_color("black", loaded)


# --- material ---------------------------------------------------------------

def test_material_match_finds_the_tool():
    loaded = _loaded(_tag("PLA"), _tag("PETG", sub=""), _tag("PLA"))

    assert resolve_tool_by_material("PETG", loaded) == 1


def test_material_match_is_case_insensitive():
    loaded = _loaded(_tag("PLA"), _tag("PETG", sub=""))

    assert resolve_tool_by_material("petg", loaded) == 1


def test_a_material_that_is_not_loaded_is_refused_and_lists_what_is():
    loaded = _loaded(_tag("PLA"), _tag("PLA"))

    with pytest.raises(ToolResolutionError) as exc:
        resolve_tool_by_material("PETG", loaded)

    assert "PETG" in str(exc.value) and "PLA" in str(exc.value)


def test_two_spools_of_the_same_material_pick_the_lowest_tool():
    """Unlike colour, duplicate material is not ambiguous -- either will do, so be
    deterministic rather than refusing."""
    loaded = _loaded(_tag("PLA"), _tag("PETG", sub=""), _tag("PETG", sub=""))

    assert resolve_tool_by_material("PETG", loaded) == 1
