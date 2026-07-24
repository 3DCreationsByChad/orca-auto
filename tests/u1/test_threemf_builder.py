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


def test_patch_project_settings_sets_used_slots():
    parts = [
        U1Part("/a.stl", ToolAssignment(tool_index=0, filament="PETG", color="#FF0000")),
        U1Part("/b.stl", ToolAssignment(tool_index=2, filament="PLA", color="#00FF00")),
    ]
    cfg = _patch_project_settings(parts)
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
    parts = [
        _cube_part(tmp_path, "a", 0, "PETG", "#FF0000"),
        _cube_part(tmp_path, "b", 1, "PLA", "#00FF00"),
    ]
    out = tmp_path / "job.3mf"
    result = build_u1_3mf(parts, out)
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
    parts = [
        _cube_part(tmp_path, "a", 0, "PETG"),
        _cube_part(tmp_path, "b", 0, "PLA"),  # duplicate tool 0
    ]
    with pytest.raises(ValueError, match="duplicate tool_index"):
        build_u1_3mf(parts, tmp_path / "job.3mf")


def test_build_u1_3mf_rejects_empty(tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        build_u1_3mf([], tmp_path / "job.3mf")


def test_build_matches_golden_encoding(tmp_path):
    parts = [
        _cube_part(tmp_path, "c0", 0, "PETG", "#FFFFFF"),
        _cube_part(tmp_path, "c1", 1, "PETG", "#080A0D"),
        _cube_part(tmp_path, "c2", 2, "PLA", "#519F61"),
        _cube_part(tmp_path, "c3", 3, "PETG", "#E2DEDB"),
    ]
    out = build_u1_3mf(parts, tmp_path / "u1.3mf")
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
