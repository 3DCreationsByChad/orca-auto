"""Build a Snapmaker U1 multicolor project .3mf from STLs + tool assignments."""

from __future__ import annotations

import json
import logging
import os
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from xml.sax.saxutils import escape

import trimesh

from orca_api.u1.filament_presets import (
    DEFAULT_VENDOR,
    resolve_filament_preset,
    resolve_preset_name,
)
from orca_api.u1.loaded_filament import (
    LoadedFilament,
    check_material_match,
)
from orca_api.u1.support import SupportSpec, apply_support
from orca_api.u1.tool_resolution import to_hex
from orca_api.u1.tool_map import ToolAssignment, build_tool_map

logger = logging.getLogger(__name__)

_TOOL_COUNT = 4

#: Config keys that are 4-element arrays for reasons that have nothing to do with
#: filament -- bed corner coordinates, per-extruder hardware facts. A filament preset
#: has no business rewriting these, and `printable_area` in particular would silently
#: move one corner of the bed.
_PRINTER_SCOPED_KEYS = frozenset({
    "printable_area",
    "extruder_offset",
    "extruder_colour",
    "nozzle_diameter",
})

_MODEL_NS = (
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
)


@dataclass(frozen=True)
class U1Part:
    """One STL paired with the tool/color it prints on."""

    stl_path: str
    assignment: ToolAssignment


def _mesh_object_xml(stl_path: str, object_id: int) -> str:
    """One `<object>` element carrying an STL's vertices and triangles verbatim."""
    mesh = trimesh.load(stl_path, force="mesh")
    verts = "".join(
        f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in mesh.vertices
    )
    tris = "".join(
        f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces
    )
    return (
        f'<object id="{object_id}" type="model"><mesh>'
        f"<vertices>{verts}</vertices><triangles>{tris}</triangles>"
        "</mesh></object>"
    )


def _wrap_sub_model(objects_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" {_MODEL_NS} requiredextensions="p">'
        f"<resources>{objects_xml}</resources><build/></model>"
    )


def _mesh_model_xml(stl_path: str) -> str:
    """Convert an STL to a 3MF sub-model holding object id=1 (vertices + triangles)."""
    return _wrap_sub_model(_mesh_object_xml(stl_path, 1))


def _assembly_model_xml(parts: Sequence[U1Part]) -> str:
    """All part meshes in one sub-model, object ids 1..N in part order.

    Keeping every mesh in its own coordinate system and giving the components
    identity transforms is what preserves a shared origin -- the parts of a
    multicolour model must land on top of each other, not beside each other.
    """
    return _wrap_sub_model(
        "".join(_mesh_object_xml(p.stl_path, idx + 1) for idx, p in enumerate(parts))
    )


def _load_template() -> dict:
    text = (
        resources.files("orca_api.u1")
        .joinpath("templates", "u1_project_settings.json")
        .read_text()
    )
    return json.loads(text)


def _overlay_loaded_spool(slots: dict, loaded: LoadedFilament) -> None:
    """Apply a spool's own RFID-tagged temperatures over the resolved profile.

    The tag is written by the filament's manufacturer for that exact spool, so it
    beats the generic profile -- `Snapmaker PLA @U1` says 220C first layer while a
    PLA SnapSpeed spool tags 230C. Values outside the spool's own declared hotend
    range are treated as corrupt and ignored rather than sent to the heater.
    """
    i = loaded.tool_index
    lo, hi = loaded.hotend_min_temp, loaded.hotend_max_temp

    def sane(temp: int) -> bool:
        return temp > 0 and (not (lo and hi) or lo <= temp <= hi)

    if sane(loaded.first_layer_temp):
        slots["nozzle_temperature_initial_layer"][i] = str(loaded.first_layer_temp)
    if sane(loaded.other_layer_temp):
        slots["nozzle_temperature"][i] = str(loaded.other_layer_temp)
    if loaded.bed_temp > 0:
        for key in ("hot_plate_temp", "hot_plate_temp_initial_layer"):
            if key in slots:
                slots[key][i] = str(loaded.bed_temp)


def _patch_project_settings(
    parts: Sequence[U1Part],
    datadir: str | Path,
    vendor: str = DEFAULT_VENDOR,
    loaded: Sequence[LoadedFilament | None] | None = None,
    extra_filaments: dict[int, str] | None = None,
) -> dict:
    """Return a copy of the U1 template config with each slot's filament resolved.

    The project `.3mf` embeds a fully resolved config, so naming a preset is not
    enough -- every per-filament value (temps, bed, flow, cooling, retraction) is
    spliced into the slot from the vendor bundle's real profile.

    Raises:
        FilamentPresetError: if a requested filament has no preset. We refuse
            rather than emit a slot labelled one material and heated like another.
    """
    cfg = _load_template()
    # every per-filament setting in the template is a 4-slot array, one per tool
    slots = {
        key: list(value)
        for key, value in cfg.items()
        if isinstance(value, list)
        and len(value) == _TOOL_COUNT
        and key not in _PRINTER_SCOPED_KEYS
    }

    for part in parts:
        a = part.assignment
        i = a.tool_index
        name = resolve_preset_name(a.filament, datadir, vendor)
        preset = resolve_filament_preset(a.filament, datadir, vendor)

        for key, value in preset.items():
            if key not in slots:
                continue  # not a per-filament setting in this template
            if isinstance(value, list) and len(value) == 1:
                slots[key][i] = value[0]

        slots["filament_settings_id"][i] = name

        spool = loaded[i] if loaded and i < len(loaded) else None
        if a.color:
            # a job may name a colour ("black") to *select* the tool, but this field
            # has to be #RRGGBB. Prefer the spool's real colour over an approximation.
            explicit = to_hex(a.color)
            if spool is not None and to_hex(a.color) is not None and not a.color.strip().startswith("#"):
                slots["filament_colour"][i] = spool.color
            elif explicit is not None:
                slots["filament_colour"][i] = explicit

        if spool is not None:
            check_material_match(tool_index=i, requested=a.filament, loaded=spool)
            _overlay_loaded_spool(slots, spool)

    # Tools that print no geometry still need their slot resolved -- a support
    # interface is the case that matters. Designating tool 4 as the support
    # material does NOT make slot 4 heat like that material; without this the
    # slot keeps the template default and PETG is extruded at PLA temperature.
    for tool_index, filament in (extra_filaments or {}).items():
        preset = resolve_filament_preset(filament, datadir, vendor)
        for key, value in preset.items():
            if key in slots and isinstance(value, list) and len(value) == 1:
                slots[key][tool_index] = value[0]
        slots["filament_settings_id"][tool_index] = resolve_preset_name(
            filament, datadir, vendor
        )
        spool = loaded[tool_index] if loaded and tool_index < len(loaded) else None
        if spool is not None:
            _overlay_loaded_spool(slots, spool)

    cfg.update(slots)
    return cfg


def _assembly_settings_xml(parts: Sequence[U1Part]) -> str:
    """`model_settings.config` for one object whose parts each print in a tool.

    Mirrors what OrcaSlicer itself writes for a merged assembly: a single
    `<object>` holding one `<part id="k">` per component, in component order,
    each carrying its own `extruder`.
    """
    oid = len(parts) + 1
    body = "".join(
        f'<part id="{idx + 1}" subtype="normal_part">'
        f'<metadata key="name" value="{escape(os.path.basename(part.stl_path))}"/>'
        f'<metadata key="matrix" value="{_IDENTITY_MATRIX}"/>'
        f'<metadata key="extruder" value="{part.assignment.tool_index + 1}"/>'
        '<mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" '
        'facets_reversed="0" backwards_edges="0"/></part>'
        for idx, part in enumerate(parts)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>'
        f'<object id="{oid}">'
        f'<metadata key="name" value="{_ASSEMBLY_NAME}"/>'
        f"{body}</object>"
        '<plate><metadata key="plater_id" value="1"/>'
        '<metadata key="filament_map_mode" value="Auto For Flush"/>'
        '<metadata key="filament_maps" value="1"/>'
        f'<model_instance><metadata key="object_id" value="{oid}"/>'
        '<metadata key="instance_id" value="0"/></model_instance>'
        "</plate></config>"
    )


def check_process_overrides(cfg: dict, process: dict) -> list[str]:
    """Warnings for `process` overrides that OrcaSlicer will quietly ignore.

    OrcaSlicer's config is stringly typed -- `small_area_infill_flow_compensation`
    is `"0"`, not `0`. Hand it a JSON integer and it does not complain, it just
    keeps the old value, and the only way to notice is to read the setting back
    out of the G-code. A type mismatch is therefore reported loudly.

    Returns:
        Human-readable warnings, empty when every override will land.
    """
    warnings: list[str] = []
    for key, value in process.items():
        if key not in cfg:
            warnings.append(
                f"process override {key!r} is not a key this template carries -- "
                f"it will be passed through, but check the spelling"
            )
            continue
        current = cfg[key]
        if isinstance(current, list) != isinstance(value, list):
            warnings.append(
                f"process override {key!r} should be "
                f"{'a list of 4, one per tool' if isinstance(current, list) else 'a single value'}"
                f" -- got {type(value).__name__}"
            )
        elif isinstance(current, str) and not isinstance(value, str):
            warnings.append(
                f"process override {key!r} must be a string like {current!r}, "
                f"not {type(value).__name__} -- OrcaSlicer drops it silently"
            )
        elif isinstance(current, list) and current and isinstance(current[0], str):
            if any(not isinstance(v, str) for v in value):
                warnings.append(
                    f"process override {key!r} must be strings like {current!r} -- "
                    f"OrcaSlicer drops non-strings silently"
                )
    return warnings


def _model_settings_xml(parts: Sequence[U1Part], *, assembly: bool = False) -> str:
    if assembly:
        return _assembly_settings_xml(parts)
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

#: 4x4 row-major identity, the shape OrcaSlicer writes for an untransformed part.
_IDENTITY_MATRIX = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
_ASSEMBLY_FILE = "assembly.model"
_ASSEMBLY_NAME = "assembly"


def _object_filename(idx: int) -> str:
    return f"obj_{idx + 1}.model"


def _grid_xy(idx: int) -> tuple[float, float]:
    cols = max(1, int(_BED_MM // _GRID_PITCH))
    row, col = divmod(idx, cols)
    return (_GRID_PITCH * (col + 1), _GRID_PITCH * (row + 1))


def _assembly3d_xml(parts: Sequence[U1Part]) -> str:
    """One object composed of every part, all at the origin they were authored in."""
    oid = len(parts) + 1
    components = "".join(
        f'<component p:path="/3D/Objects/{_ASSEMBLY_FILE}" objectid="{idx + 1}" '
        'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
        for idx in range(len(parts))
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" {_MODEL_NS} requiredextensions="p">'
        '<metadata name="Application">BambuStudio-2.3.1</metadata>'
        '<metadata name="BambuStudio:3mfVersion">1</metadata>'
        f'<resources><object id="{oid}" type="model">'
        f"<components>{components}</components></object></resources>"
        f'<build><item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 0 0 0" '
        'printable="1"/></build></model>'
    )


def _model3d_xml(parts: Sequence[U1Part], *, assembly: bool = False) -> str:
    if assembly:
        return _assembly3d_xml(parts)
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


def _model3d_rels(parts: Sequence[U1Part], *, assembly: bool = False) -> str:
    targets = (
        [_ASSEMBLY_FILE] if assembly else [_object_filename(i) for i in range(len(parts))]
    )
    rels = "".join(
        f'<Relationship Target="/3D/Objects/{target}" Id="rel-{i + 1}" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        for i, target in enumerate(targets)
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


def build_u1_3mf(
    parts: Sequence[U1Part],
    out_path: str | Path,
    datadir: str | Path,
    vendor: str = DEFAULT_VENDOR,
    loaded: Sequence[LoadedFilament | None] | None = None,
    support: SupportSpec | None = None,
    assembly: bool = False,
    process: dict | None = None,
    extra_filaments: dict[int, str] | None = None,
) -> Path:
    """Build a Snapmaker U1 multicolor project .3mf.

    Args:
        datadir: OrcaSlicer data dir holding the vendor bundle. Required, because
            each slot's real filament settings are resolved out of it.
        loaded: What the printer reports is physically loaded, one entry per tool.
            Each tagged spool's own temperatures override the profile.
        assembly: Treat the STLs as parts of ONE model that share an origin, so
            they print on top of each other in different colours. The default
            keeps each STL a separate object, which is what a plate of unrelated
            parts wants -- and which would print a two-colour model as two
            half-models side by side.
        process: Raw project-config overrides applied last (e.g.
            `{"wall_generator": "arachne"}`).

    Raises:
        ValueError: if the assignments are empty, have an out-of-range or
            duplicate tool index, or an empty filament (via build_tool_map).
        FilamentPresetError: if a requested filament has no preset in the bundle.
    """
    build_tool_map([p.assignment for p in parts])  # validate; raises on bad input
    out = Path(out_path)
    cfg = _patch_project_settings(parts, datadir, vendor, loaded=loaded,
                                 extra_filaments=extra_filaments)
    if support is not None:
        apply_support(
            cfg, support, list(loaded or [None] * _TOOL_COUNT),
            part_materials=[p.assignment.filament for p in parts],
            declared=extra_filaments,
        )
    if process:
        for warning in check_process_overrides(cfg, process):
            logger.warning("%s", warning)
            print(f"Warning: {warning}")
        cfg.update(process)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("3D/3dmodel.model", _model3d_xml(parts, assembly=assembly))
        z.writestr("3D/_rels/3dmodel.model.rels", _model3d_rels(parts, assembly=assembly))
        if assembly:
            z.writestr(f"3D/Objects/{_ASSEMBLY_FILE}", _assembly_model_xml(parts))
        else:
            for idx, part in enumerate(parts):
                z.writestr(
                    f"3D/Objects/{_object_filename(idx)}", _mesh_model_xml(part.stl_path)
                )
        z.writestr(
            "Metadata/model_settings.config",
            _model_settings_xml(parts, assembly=assembly),
        )
        z.writestr("Metadata/project_settings.config", json.dumps(cfg))
    return out
