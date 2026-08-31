"""Designating a spool as support body and/or interface material.

The reason to do this: PETG barely bonds to PLA or ASA. Print the support
*interface* in PETG under a PLA part and it peels off in one piece, which means
you can close the support gap to zero and get a genuinely flat support-facing
surface instead of the usual scarred one. The material incompatibility does the
releasing that an air gap normally does.
"""

import pytest

from orca_api.u1.loaded_filament import parse_filament_detect
from orca_api.u1.support import SupportSpec, apply_support
from orca_api.u1.tool_resolution import ToolResolutionError


def _tag(material, argb=0xFF080A0D):
    return {"MAIN_TYPE": material, "SUB_TYPE": "", "ARGB_COLOR": argb,
            "FIRST_LAYER_TEMP": 230, "OTHER_LAYER_TEMP": 220, "BED_TEMP": 60,
            "HOTEND_MIN_TEMP": 190, "HOTEND_MAX_TEMP": 250, "VENDOR": "Snapmaker"}


def _loaded():
    # tools 1-3 PLA, tool 4 PETG
    return parse_filament_detect([_tag("PLA"), _tag("PLA"), _tag("PLA"), _tag("PETG")])


def _cfg():
    return {"enable_support": 0, "support_filament": 0, "support_interface_filament": 0,
            "support_top_z_distance": 0.2, "support_bottom_z_distance": 0.2,
            "support_interface_spacing": 0.5}


def test_interface_material_selects_that_tool_one_based():
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface="PETG"), _loaded(), part_materials=["PLA"])
    # OrcaSlicer numbers support filaments from 1; 0 means "any"
    assert cfg["support_interface_filament"] == "4"
    assert cfg["enable_support"] == "1"


def test_body_material_selects_that_tool_independently():
    cfg = _cfg()
    apply_support(cfg, SupportSpec(body="PETG"), _loaded(), part_materials=["PLA"])
    assert cfg["support_filament"] == "4"
    assert cfg["support_interface_filament"] == 0  # untouched
    assert cfg["enable_support"] == "1"


def test_body_and_interface_are_separately_selectable():
    cfg = _cfg()
    apply_support(cfg, SupportSpec(body="PLA", interface="PETG"), _loaded(),
                  part_materials=["PLA"])
    assert cfg["support_filament"] == "1"        # first PLA tool
    assert cfg["support_interface_filament"] == "4"


def test_release_interface_closes_the_support_gap():
    """PETG under PLA does not bond, so the interface can touch the part. That is
    the whole point -- a zero gap gives a flat surface instead of a scarred one."""
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface="PETG"), _loaded(), part_materials=["PLA"])
    assert cfg["support_top_z_distance"] == "0"
    assert cfg["support_bottom_z_distance"] == "0"


def test_same_material_interface_keeps_the_air_gap():
    """PLA interface under a PLA part WILL weld. Closing the gap there fuses the
    support to the model and ruins the part."""
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface="PLA"), _loaded(), part_materials=["PLA"])
    assert cfg["support_top_z_distance"] == 0.2
    assert cfg["support_bottom_z_distance"] == 0.2


def test_mixed_part_materials_keep_the_gap_unless_every_part_releases():
    """One PETG part in the plate means a PETG interface would weld to it."""
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface="PETG"), _loaded(),
                  part_materials=["PLA", "PETG"])
    assert cfg["support_top_z_distance"] == 0.2


def test_explicit_tool_index_bypasses_material_lookup():
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface=2), _loaded(), part_materials=["PLA"])
    assert cfg["support_interface_filament"] == "3"  # 0-based 2 -> 1-based 3


def test_a_material_that_is_not_loaded_is_refused():
    cfg = _cfg()
    with pytest.raises(ToolResolutionError, match="TPU"):
        apply_support(cfg, SupportSpec(interface="TPU"), _loaded(), part_materials=["PLA"])


def test_no_support_spec_leaves_the_config_untouched():
    cfg = _cfg()
    apply_support(cfg, None, _loaded(), part_materials=["PLA"])
    assert cfg == _cfg()


def test_support_spec_from_job_dict():
    assert SupportSpec.from_dict({"interface": "PETG", "body": "PLA"}) == SupportSpec(
        body="PLA", interface="PETG")
    assert SupportSpec.from_dict(None) is None
    assert SupportSpec.from_dict({}) is None


# --- part materials arrive as full preset names, not bare materials -------------
#
# Every test above passes part_materials=["PLA"], a shape no real job produces.
# build_u1_3mf passes `p.assignment.filament`, which is a preset name like
# "Snapmaker PLA SnapSpeed @U1". Exact set membership never matched one, so the
# zero-gap branch could not fire from any real job spec.


def test_a_preset_name_still_reads_as_its_material():
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface="PETG"), _loaded(),
                  part_materials=["Snapmaker PLA SnapSpeed @U1"])
    assert cfg["support_top_z_distance"] == "0"


def test_a_preset_name_of_a_welding_material_keeps_the_gap():
    """PETG interface under a PETG part welds, however the preset is spelled."""
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface="PETG"), _loaded(),
                  part_materials=["Snapmaker PETG @U1"])
    assert cfg["support_top_z_distance"] == 0.2


def test_a_material_is_matched_as_a_whole_token_not_a_substring():
    """PETG-CF is not PETG's safe list, and must not match via 'PA-CF' or 'PLA'."""
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface="PETG"), _loaded(),
                  part_materials=["Generic PETG-CF @U1"])
    assert cfg["support_top_z_distance"] == 0.2


# --- an untagged spool's material comes from the job's own declaration ----------
#
# Third-party and re-spooled filament carries no RFID tag, so the printer reports
# the slot as unknown. The job's `filaments` block names the preset for exactly
# that case, and the builder already trusts it to set the slot's hotend and bed
# temperature -- a hotter mistake than a support gap.


def _loaded_untagged_support():
    # tools 1-3 PLA, tool 4 loaded but untagged -- the real plate
    return parse_filament_detect([_tag("PLA"), _tag("PLA"), _tag("PLA"), _tag("NONE")])


def test_declared_filament_resolves_an_untagged_interface_spool():
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface=3), _loaded_untagged_support(),
                  part_materials=["Snapmaker PLA SnapSpeed @U1"],
                  declared={3: "Snapmaker PETG @U1"})
    assert cfg["support_top_z_distance"] == "0"
    assert cfg["support_bottom_z_distance"] == "0"


def test_an_undeclared_untagged_interface_spool_keeps_the_gap():
    """Nothing knows what is in that slot, so nothing may close the gap."""
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface=3), _loaded_untagged_support(),
                  part_materials=["Snapmaker PLA SnapSpeed @U1"])
    assert cfg["support_top_z_distance"] == 0.2


def test_the_rfid_tag_outranks_the_declaration():
    """The tag is measured; the declaration is typed. A PLA tag keeps the gap even
    if the job claims PETG."""
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface=0), _loaded(),
                  part_materials=["Snapmaker PLA SnapSpeed @U1"],
                  declared={0: "Snapmaker PETG @U1"})
    assert cfg["support_top_z_distance"] == 0.2


def test_a_declaration_for_another_tool_does_not_leak():
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface=3), _loaded_untagged_support(),
                  part_materials=["Snapmaker PLA SnapSpeed @U1"],
                  declared={1: "Snapmaker PETG @U1"})
    assert cfg["support_top_z_distance"] == 0.2


def test_a_declaration_naming_no_known_material_keeps_the_gap():
    cfg = _cfg()
    apply_support(cfg, SupportSpec(interface=3), _loaded_untagged_support(),
                  part_materials=["Snapmaker PLA SnapSpeed @U1"],
                  declared={3: "Some House Brand @U1"})
    assert cfg["support_top_z_distance"] == 0.2
