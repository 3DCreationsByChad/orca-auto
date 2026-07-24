"""Build a Snapmaker U1 multicolor project .3mf from STLs + tool assignments."""

from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from xml.sax.saxutils import escape

import trimesh

from orca_api.u1.tool_map import ToolAssignment, build_tool_map

_FILAMENT_SETTINGS_BY_TYPE = {
    "PETG": "Generic PETG @System",
    "PLA": "Generic PLA @System",
    "ABS": "Generic ABS @System",
    "TPU": "Generic TPU @System",
}

_MODEL_NS = (
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
)


@dataclass(frozen=True)
class U1Part:
    """One STL paired with the tool/color it prints on."""

    stl_path: str
    assignment: ToolAssignment


def _mesh_model_xml(stl_path: str) -> str:
    """Convert an STL to a 3MF sub-model holding object id=1 (vertices + triangles)."""
    mesh = trimesh.load(stl_path, force="mesh")
    verts = "".join(
        f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in mesh.vertices
    )
    tris = "".join(
        f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" {_MODEL_NS} requiredextensions="p">'
        '<resources><object id="1" type="model"><mesh>'
        f"<vertices>{verts}</vertices><triangles>{tris}</triangles>"
        "</mesh></object></resources><build/></model>"
    )


def _load_template() -> dict:
    text = (
        resources.files("orca_api.u1")
        .joinpath("templates", "u1_project_settings.json")
        .read_text()
    )
    return json.loads(text)


def _patch_project_settings(parts: Sequence[U1Part]) -> dict:
    """Return a copy of the U1 template config with filament slots set per part."""
    cfg = _load_template()
    colour = list(cfg["filament_colour"])
    ftype = list(cfg["filament_type"])
    fsid = list(cfg["filament_settings_id"])
    for part in parts:
        a = part.assignment
        i = a.tool_index
        ftype[i] = a.filament
        if a.color:
            colour[i] = a.color
        fsid[i] = _FILAMENT_SETTINGS_BY_TYPE.get(a.filament, fsid[i])
    cfg["filament_colour"] = colour
    cfg["filament_type"] = ftype
    cfg["filament_settings_id"] = fsid
    return cfg


def _model_settings_xml(parts: Sequence[U1Part]) -> str:
    objects = []
    instances = []
    for idx, part in enumerate(parts):
        oid = idx + 2  # ids start at 2 (id 1 is the mesh sub-object)
        extruder = part.assignment.tool_index + 1
        name = escape(os.path.basename(part.stl_path))
        objects.append(
            f'<object id="{oid}">'
            f'<metadata key="name" value="{name}"/>'
            f'<metadata key="extruder" value="{extruder}"/>'
            f'<part id="1" subtype="normal_part"><metadata key="name" value="{name}"/>'
            '<mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" '
            'facets_reversed="0" backwards_edges="0"/></part></object>'
        )
        instances.append(
            f'<model_instance><metadata key="object_id" value="{oid}"/>'
            '<metadata key="instance_id" value="0"/></model_instance>'
        )
    maps = " ".join("1" for _ in parts)
    plate = (
        '<plate><metadata key="plater_id" value="1"/>'
        '<metadata key="filament_map_mode" value="Auto For Flush"/>'
        f'<metadata key="filament_maps" value="{maps}"/>'
        f'{"".join(instances)}</plate>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>'
        f'{"".join(objects)}{plate}</config>'
    )


_BED_MM = 220  # U1 bed is ~220mm; grid-center objects, slicer re-arranges later
_GRID_PITCH = 40.0


def _object_filename(idx: int) -> str:
    return f"obj_{idx + 1}.model"


def _grid_xy(idx: int) -> tuple[float, float]:
    cols = max(1, int(_BED_MM // _GRID_PITCH))
    row, col = divmod(idx, cols)
    return (_GRID_PITCH * (col + 1), _GRID_PITCH * (row + 1))


def _model3d_xml(parts: Sequence[U1Part]) -> str:
    objects = []
    items = []
    for idx, _ in enumerate(parts):
        oid = idx + 2
        path = f"/3D/Objects/{_object_filename(idx)}"
        objects.append(
            f'<object id="{oid}" type="model"><components>'
            f'<component p:path="{path}" objectid="1" '
            'transform="1 0 0 0 1 0 0 0 1 0 0 0"/></components></object>'
        )
        x, y = _grid_xy(idx)
        items.append(
            f'<item objectid="{oid}" '
            f'transform="1 0 0 0 1 0 0 0 1 {x:.4f} {y:.4f} 0" printable="1"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" {_MODEL_NS} requiredextensions="p">'
        '<metadata name="Application">BambuStudio-2.3.1</metadata>'
        '<metadata name="BambuStudio:3mfVersion">1</metadata>'
        f'<resources>{"".join(objects)}</resources>'
        f'<build>{"".join(items)}</build></model>'
    )


def _model3d_rels(parts: Sequence[U1Part]) -> str:
    rels = "".join(
        f'<Relationship Target="/3D/Objects/{_object_filename(i)}" Id="rel-{i + 1}" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        for i in range(len(parts))
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel-1" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
)


def build_u1_3mf(parts: Sequence[U1Part], out_path: str | Path) -> Path:
    """Build a Snapmaker U1 multicolor project .3mf.

    Raises:
        ValueError: if the assignments are empty, have an out-of-range or
            duplicate tool index, or an empty filament (via build_tool_map).
    """
    build_tool_map([p.assignment for p in parts])  # validate; raises on bad input
    out = Path(out_path)
    cfg = _patch_project_settings(parts)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("3D/3dmodel.model", _model3d_xml(parts))
        z.writestr("3D/_rels/3dmodel.model.rels", _model3d_rels(parts))
        for idx, part in enumerate(parts):
            z.writestr(
                f"3D/Objects/{_object_filename(idx)}", _mesh_model_xml(part.stl_path)
            )
        z.writestr("Metadata/model_settings.config", _model_settings_xml(parts))
        z.writestr("Metadata/project_settings.config", json.dumps(cfg))
    return out
