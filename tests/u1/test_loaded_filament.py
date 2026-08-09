"""Tests for reading what is physically loaded in the U1's four tools.

The U1 reads an RFID tag on each Snapmaker spool and exposes it over Moonraker as
`filament_detect`. Those tags carry the vendor's own recommended temperatures,
which can differ from the generic OrcaSlicer profile -- e.g. PLA SnapSpeed tags
230C for the first layer where the profile says 220C.
"""

import pytest

from orca_api.u1.loaded_filament import (
    LoadedFilament,
    MaterialMismatch,
    check_material_match,
    parse_filament_detect,
)


def _tag(**over):
    tag = {
        "MAIN_TYPE": "PLA",
        "SUB_TYPE": "SnapSpeed",
        "ARGB_COLOR": 4278716941,  # 0xFF080A0D
        "FIRST_LAYER_TEMP": 230,
        "OTHER_LAYER_TEMP": 220,
        "HOTEND_MIN_TEMP": 190,
        "HOTEND_MAX_TEMP": 230,
        "BED_TEMP": 60,
        "VENDOR": "Snapmaker",
        "OFFICIAL": True,
    }
    tag.update(over)
    return tag


EMPTY = {"MAIN_TYPE": "NONE", "SUB_TYPE": "NONE", "ARGB_COLOR": 4294967295}


def test_parses_a_tagged_spool_into_its_tool_slot():
    loaded = parse_filament_detect([_tag(), EMPTY, EMPTY, EMPTY])

    assert loaded[0] == LoadedFilament(
        tool_index=0,
        material="PLA",
        sub_type="SnapSpeed",
        color="#080A0D",
        first_layer_temp=230,
        other_layer_temp=220,
        bed_temp=60,
        hotend_min_temp=190,
        hotend_max_temp=230,
        vendor="Snapmaker",
    )


def test_untagged_slots_are_none_not_guessed():
    """An untagged third-party spool is unknown, not empty and not assumed PLA."""
    loaded = parse_filament_detect([_tag(), EMPTY, EMPTY, EMPTY])

    assert loaded[1] is None and loaded[2] is None and loaded[3] is None
    assert len(loaded) == 4


def test_tool_index_follows_array_position():
    loaded = parse_filament_detect([EMPTY, EMPTY, _tag(MAIN_TYPE="ABS"), EMPTY])

    assert loaded[2].tool_index == 2
    assert loaded[2].material == "ABS"


def test_short_payload_still_yields_four_slots():
    loaded = parse_filament_detect([_tag()])

    assert len(loaded) == 4
    assert loaded[3] is None


def test_color_decodes_from_argb():
    loaded = parse_filament_detect([_tag(ARGB_COLOR=0xFFF4C032)])

    assert loaded[0].color == "#F4C032"


def test_material_match_passes_when_the_spool_agrees_with_the_request():
    check_material_match(tool_index=0, requested="PLA", loaded=parse_filament_detect([_tag()])[0])
    check_material_match(
        tool_index=0,
        requested="Snapmaker PLA SnapSpeed @U1",
        loaded=parse_filament_detect([_tag()])[0],
    )


def test_material_mismatch_is_refused_loudly():
    """Asking for PLA on a tool holding ABS would print at the wrong temperature
    into a material that needs an enclosure. Refuse rather than guess."""
    loaded = parse_filament_detect([_tag(MAIN_TYPE="ABS")])[0]

    with pytest.raises(MaterialMismatch) as exc:
        check_material_match(tool_index=0, requested="PLA", loaded=loaded)

    msg = str(exc.value)
    assert "tool 1" in msg  # human-facing numbering
    assert "PLA" in msg and "ABS" in msg


def test_no_tag_means_no_mismatch_check():
    """Untagged spools can't be verified -- that is not an error."""
    check_material_match(tool_index=3, requested="PLA", loaded=None)
