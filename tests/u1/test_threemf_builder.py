"""Tests for the U1 multicolor .3mf builder."""

import json
import xml.etree.ElementTree as ET
import zipfile

import pytest
import trimesh

from orca_api.u1.tool_map import ToolAssignment
from orca_api.u1.threemf_builder import (
    U1Part,
    _mesh_model_xml,
    _model3d_rels,
    _model3d_xml,
    _model_settings_xml,
    _object_filename,
    _patch_project_settings,
    build_u1_3mf,
)


def _cube_part(tmp_path, name, tool, filament, color=None):
    stl = tmp_path / f"{name}.stl"
    trimesh.creation.box(extents=(10, 10, 10)).export(stl)
    return U1Part(str(stl), ToolAssignment(tool_index=tool, filament=filament, color=color))


def test_u1part_pairs_stl_with_assignment():
    p = U1Part(stl_path="/tmp/cube.stl",
               assignment=ToolAssignment(tool_index=2, filament="PLA", color="#519F61"))
    assert p.stl_path == "/tmp/cube.stl"
    assert p.assignment.tool_index == 2


def test_mesh_model_xml_has_cube_counts(tmp_path):
    stl = tmp_path / "cube.stl"
    trimesh.creation.box(extents=(10, 10, 10)).export(stl)
    xml = _mesh_model_xml(str(stl))
    # a box has 8 unique vertices and 12 triangles
    assert xml.count("<vertex ") == 8
    assert xml.count("<triangle ") == 12
    assert '<object id="1" type="model">' in xml


def test_patch_project_settings_sets_used_slots(tmp_path):
    datadir = _u1_bundle(tmp_path)
    parts = [
        U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PETG", color="#FF0000")),
        U1Part("/b.stl", ToolAssignment(tool_index=2, filament="PLA", color="#00FF00")),
    ]
    cfg = _patch_project_settings(parts, datadir)
    assert cfg["filament_colour"][0] == "#FF0000"
    assert cfg["filament_type"][0] == "PETG"
    assert cfg["filament_colour"][2] == "#00FF00"
    assert cfg["filament_type"][2] == "PLA"
    # 4-slot arrays preserved
    assert len(cfg["filament_colour"]) == 4


def test_model_settings_maps_extruder_one_based():
    parts = [
        U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PETG")),
        U1Part("/d.stl", ToolAssignment(tool_index=3, filament="PETG")),
    ]
    xml = _model_settings_xml(parts)
    root = ET.fromstring(xml)
    objs = root.findall("object")
    # object ids 2,3 ; extruder = tool_index+1 -> 1 and 4
    extruders = {
        o.get("id"): o.find("./metadata[@key='extruder']").get("value") for o in objs
    }
    assert extruders == {"2": "1", "3": "4"}


def test_model3d_has_one_object_and_component_per_part():
    parts = [
        U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PETG")),
        U1Part("/b.stl", ToolAssignment(tool_index=1, filament="PLA")),
    ]
    xml = _model3d_xml(parts)
    assert xml.count("<object ") == 2
    assert xml.count("<component ") == 2
    assert xml.count("<item ") == 2
    assert f'p:path="/3D/Objects/{_object_filename(0)}"' in xml
    rels = _model3d_rels(parts)
    assert rels.count("<Relationship ") == 2


def test_build_u1_3mf_writes_valid_archive(tmp_path):
    datadir = _u1_bundle(tmp_path)
    parts = [
        _cube_part(tmp_path, "a", 0, "PETG", "#FF0000"),
        _cube_part(tmp_path, "b", 1, "PLA", "#00FF00"),
    ]
    out = tmp_path / "job.3mf"
    result = build_u1_3mf(parts, out, datadir)
    assert result == out and out.exists()
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert "3D/3dmodel.model" in names
        assert "Metadata/model_settings.config" in names
        assert "Metadata/project_settings.config" in names
        assert "3D/Objects/obj_1.model" in names
        assert "3D/Objects/obj_2.model" in names
        cfg = json.loads(z.read("Metadata/project_settings.config"))
        assert cfg["filament_type"][1] == "PLA"


def test_build_u1_3mf_rejects_duplicate_tool(tmp_path):
    datadir = _u1_bundle(tmp_path)
    parts = [
        _cube_part(tmp_path, "a", 0, "PETG"),
        _cube_part(tmp_path, "b", 0, "PLA"),  # duplicate tool 0
    ]
    with pytest.raises(ValueError, match="duplicate tool_index"):
        build_u1_3mf(parts, tmp_path / "job.3mf", datadir)


def test_build_u1_3mf_rejects_empty(tmp_path):
    datadir = _u1_bundle(tmp_path)
    with pytest.raises(ValueError, match="at least one"):
        build_u1_3mf([], tmp_path / "job.3mf", datadir)


def test_build_matches_golden_encoding(tmp_path):
    datadir = _u1_bundle(tmp_path)
    parts = [
        _cube_part(tmp_path, "c0", 0, "PETG", "#FFFFFF"),
        _cube_part(tmp_path, "c1", 1, "PETG", "#080A0D"),
        _cube_part(tmp_path, "c2", 2, "PLA", "#519F61"),
        _cube_part(tmp_path, "c3", 3, "PETG", "#E2DEDB"),
    ]
    out = build_u1_3mf(parts, tmp_path / "u1.3mf", datadir)
    with zipfile.ZipFile(out) as z:
        ms = ET.fromstring(z.read("Metadata/model_settings.config"))
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    extruders = sorted(
        int(o.find("./metadata[@key='extruder']").get("value"))
        for o in ms.findall("object")
    )
    assert extruders == [1, 2, 3, 4]
    assert cfg["filament_type"] == ["PETG", "PETG", "PLA", "PETG"]
    assert cfg["filament_colour"][2] == "#519F61"


# --- filament preset resolution (regression: slots inherited the golden sample's
# --- PETG thermals no matter what the job spec asked for) ---------------------

def _u1_bundle(tmp_path):
    """Minimal Snapmaker bundle with PLA + PETG, mirroring the real @U1 layout."""
    fdir = tmp_path / "datadir" / "system" / "Snapmaker" / "filament"
    fdir.mkdir(parents=True)
    (fdir / "Snapmaker PLA @U1.json").write_text(json.dumps({
        "name": "Snapmaker PLA @U1",
        "filament_type": ["PLA"],
        "nozzle_temperature": ["220"],
        "nozzle_temperature_initial_layer": ["220"],
        "hot_plate_temp": ["55"],
        "textured_plate_temp": ["55"],
        "temperature_vitrification": ["45"],
        "filament_density": ["1.24"],
    }))
    (fdir / "Snapmaker PETG @U1.json").write_text(json.dumps({
        "name": "Snapmaker PETG @U1",
        "filament_type": ["PETG"],
        "nozzle_temperature": ["255"],
        "hot_plate_temp": ["80"],
    }))
    return tmp_path / "datadir"


def test_requesting_pla_on_a_petg_slot_rewrites_the_thermals(tmp_path):
    """The bug: template slot 0 ships as PETG (255C/80C bed). Asking for PLA used
    to relabel the slot while leaving PETG temps in place -- silently wrong."""
    datadir = _u1_bundle(tmp_path)
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA"))]

    cfg = _patch_project_settings(parts, datadir)

    assert cfg["filament_type"][0] == "PLA"
    assert cfg["nozzle_temperature"][0] == "220"
    assert cfg["hot_plate_temp"][0] == "55"
    assert cfg["temperature_vitrification"][0] == "45"


def test_untouched_slots_keep_their_template_values(tmp_path):
    datadir = _u1_bundle(tmp_path)
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA"))]

    cfg = _patch_project_settings(parts, datadir)

    # slot 3 was never assigned -- must not be disturbed
    assert cfg["nozzle_temperature"][3] == "255"
    assert len(cfg["nozzle_temperature"]) == 4


def test_explicit_full_preset_name_is_honoured(tmp_path):
    datadir = _u1_bundle(tmp_path)
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=1, filament="Snapmaker PETG @U1"))]

    cfg = _patch_project_settings(parts, datadir)

    assert cfg["filament_type"][1] == "PETG"
    assert cfg["nozzle_temperature"][1] == "255"


def test_color_still_overrides_the_preset(tmp_path):
    datadir = _u1_bundle(tmp_path)
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA", color="#519F61"))]

    cfg = _patch_project_settings(parts, datadir)

    assert cfg["filament_colour"][0] == "#519F61"


def test_unresolvable_filament_refuses_rather_than_mislabelling(tmp_path):
    """Silent wrongness is the failure mode we are eliminating -- fail loud."""
    from orca_api.u1.filament_presets import FilamentPresetError
    datadir = _u1_bundle(tmp_path)
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="UNOBTANIUM"))]

    with pytest.raises(FilamentPresetError, match="UNOBTANIUM"):
        _patch_project_settings(parts, datadir)


def test_filament_settings_id_records_the_resolved_preset(tmp_path):
    """The slot must name the preset its numbers actually came from."""
    datadir = _u1_bundle(tmp_path)
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA"))]

    cfg = _patch_project_settings(parts, datadir)

    assert cfg["filament_settings_id"][0] == "Snapmaker PLA @U1"


def test_printer_scoped_keys_are_never_spliced_from_a_filament_preset(tmp_path):
    """`printable_area` is bed *corners*, not a per-filament array -- it just happens
    to have 4 entries. A preset carrying one must not rewrite the bed geometry."""
    fdir = tmp_path / "datadir" / "system" / "Snapmaker" / "filament"
    fdir.mkdir(parents=True)
    (fdir / "Snapmaker PLA @U1.json").write_text(json.dumps({
        "name": "Snapmaker PLA @U1",
        "filament_type": ["PLA"],
        "nozzle_temperature": ["220"],
        "printable_area": ["999x999"],   # hostile/atypical profile
        "extruder_offset": ["9x9"],
        "nozzle_diameter": ["0.8"],
    }))
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA"))]

    cfg = _patch_project_settings(parts, tmp_path / "datadir")

    assert cfg["nozzle_temperature"][0] == "220"        # filament settings still applied
    assert cfg["printable_area"][0] == "-0.5x-1"        # bed geometry untouched
    assert cfg["extruder_offset"][0] == "0x0"
    assert cfg["nozzle_diameter"][0] == "0.4"


# --- RFID overlay: the spool's own vendor temps beat the generic profile -------

def _pla_tag(**over):
    tag = {"MAIN_TYPE": "PLA", "SUB_TYPE": "SnapSpeed", "ARGB_COLOR": 0xFF080A0D,
           "FIRST_LAYER_TEMP": 230, "OTHER_LAYER_TEMP": 220, "BED_TEMP": 60,
           "HOTEND_MIN_TEMP": 190, "HOTEND_MAX_TEMP": 230, "VENDOR": "Snapmaker"}
    tag.update(over)
    return tag


def test_loaded_spool_temps_override_the_profile(tmp_path):
    """The profile says 220C first layer; the spool's own tag says 230C. The
    spool wins -- it knows what it is."""
    from orca_api.u1.loaded_filament import parse_filament_detect
    datadir = _u1_bundle(tmp_path)
    loaded = parse_filament_detect([_pla_tag()])
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA"))]

    cfg = _patch_project_settings(parts, datadir, loaded=loaded)

    assert cfg["nozzle_temperature_initial_layer"][0] == "230"  # from the tag
    assert cfg["nozzle_temperature"][0] == "220"
    assert cfg["hot_plate_temp"][0] == "60"
    assert cfg["hot_plate_temp_initial_layer"][0] == "60"


def test_untagged_slot_keeps_the_profile_values(tmp_path):
    from orca_api.u1.loaded_filament import parse_filament_detect
    datadir = _u1_bundle(tmp_path)
    loaded = parse_filament_detect([])  # nothing tagged
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA"))]

    cfg = _patch_project_settings(parts, datadir, loaded=loaded)

    assert cfg["nozzle_temperature_initial_layer"][0] == "220"  # profile
    assert cfg["hot_plate_temp"][0] == "55"


def test_tag_temps_outside_the_spools_own_hotend_range_are_ignored(tmp_path):
    """A corrupt or absurd tag must not drive the hotend. Fall back to the profile."""
    from orca_api.u1.loaded_filament import parse_filament_detect
    datadir = _u1_bundle(tmp_path)
    loaded = parse_filament_detect([_pla_tag(FIRST_LAYER_TEMP=400, OTHER_LAYER_TEMP=0)])
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA"))]

    cfg = _patch_project_settings(parts, datadir, loaded=loaded)

    assert cfg["nozzle_temperature_initial_layer"][0] == "220"  # profile, not 400
    assert cfg["nozzle_temperature"][0] == "220"                # profile, not 0


def test_material_mismatch_between_job_and_loaded_spool_is_refused(tmp_path):
    from orca_api.u1.loaded_filament import MaterialMismatch, parse_filament_detect
    datadir = _u1_bundle(tmp_path)
    loaded = parse_filament_detect([_pla_tag(MAIN_TYPE="ABS")])
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA"))]

    with pytest.raises(MaterialMismatch, match="tool 1"):
        _patch_project_settings(parts, datadir, loaded=loaded)


def test_build_u1_3mf_threads_loaded_spools_into_the_archive(tmp_path):
    """Seam test: the pipeline passes `loaded` to build_u1_3mf, and the mocked
    builder in the pipeline tests cannot catch a signature mismatch here."""
    from orca_api.u1.loaded_filament import parse_filament_detect
    datadir = _u1_bundle(tmp_path)
    loaded = parse_filament_detect([_pla_tag()])
    parts = [_cube_part(tmp_path, "a", 0, "PLA")]

    out = build_u1_3mf(parts, tmp_path / "job.3mf", datadir, loaded=loaded)

    with zipfile.ZipFile(out) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    assert cfg["nozzle_temperature_initial_layer"][0] == "230"
    assert cfg["hot_plate_temp"][0] == "60"


def test_support_spec_reaches_the_built_archive(tmp_path):
    """Seam test: a `support` block in the job must survive into project_settings."""
    from orca_api.u1.loaded_filament import parse_filament_detect
    from orca_api.u1.support import SupportSpec
    fdir = tmp_path / "datadir" / "system" / "Snapmaker" / "filament"
    fdir.mkdir(parents=True)
    for name, mat, temp in [("Snapmaker PLA @U1", "PLA", "220"), ("Snapmaker PETG @U1", "PETG", "255")]:
        (fdir / f"{name}.json").write_text(json.dumps(
            {"name": name, "filament_type": [mat], "nozzle_temperature": [temp]}))
    loaded = parse_filament_detect([
        {"MAIN_TYPE": "PLA", "SUB_TYPE": "", "ARGB_COLOR": 0xFF080A0D, "FIRST_LAYER_TEMP": 220,
         "OTHER_LAYER_TEMP": 220, "BED_TEMP": 55, "HOTEND_MIN_TEMP": 190, "HOTEND_MAX_TEMP": 240},
        {"MAIN_TYPE": "PETG", "SUB_TYPE": "", "ARGB_COLOR": 0xFF1E6FD9, "FIRST_LAYER_TEMP": 255,
         "OTHER_LAYER_TEMP": 255, "BED_TEMP": 80, "HOTEND_MIN_TEMP": 220, "HOTEND_MAX_TEMP": 270},
    ])
    parts = [_cube_part(tmp_path, "a", 0, "PLA")]

    out = build_u1_3mf(parts, tmp_path / "job.3mf", tmp_path / "datadir",
                       loaded=loaded, support=SupportSpec(interface="PETG"))

    with zipfile.ZipFile(out) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    assert cfg["support_interface_filament"] == 2   # PETG is tool 2, 1-based
    assert cfg["enable_support"] == 1
    assert cfg["support_top_z_distance"] == 0        # PETG releases from PLA


def test_named_colour_is_stored_as_hex_not_the_word(tmp_path):
    """`filament_colour` must hold a hex value. A job saying "black" selects the
    tool by name, but the config needs #RRGGBB -- preferably the spool's real one."""
    from orca_api.u1.loaded_filament import parse_filament_detect
    datadir = _u1_bundle(tmp_path)
    loaded = parse_filament_detect([_pla_tag(ARGB_COLOR=0xFF080A0D)])
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA", color="black"))]

    cfg = _patch_project_settings(parts, datadir, loaded=loaded)

    assert cfg["filament_colour"][0] == "#080A0D"   # the spool's actual colour


def test_named_colour_without_a_tagged_spool_falls_back_to_the_named_hex(tmp_path):
    datadir = _u1_bundle(tmp_path)
    parts = [U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PLA", color="red"))]

    cfg = _patch_project_settings(parts, datadir)

    assert cfg["filament_colour"][0].startswith("#")
    assert cfg["filament_colour"][0] != "red"
