"""SCAD file management endpoints for browsing and editing OpenSCAD parameters."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from orca_api.config import get_settings
from orca_api.models import ExportResult, ScadFile
from orca_api.services.scad_parser import (
    get_scad_files,
    parse_scad_file,
    apply_params,
)
from orca_api.services.scad_exporter import export_scad_to_stl

router = APIRouter(prefix="/scad", tags=["scad"])


@router.get("/files", response_model=list[dict])
async def list_scad_files() -> list[dict]:
    """List all SCAD files in the models directory.

    Returns:
        List of {path: str, name: str} for each .scad file found.
    """
    scad_paths = get_scad_files()
    return [
        {"path": path, "name": Path(path).name}
        for path in scad_paths
    ]


@router.get("/file/{path:path}", response_model=ScadFile)
async def get_scad_file(path: str) -> ScadFile:
    """Get a SCAD file with its parsed customizable variables.

    Args:
        path: Relative path from models_path to the .scad file

    Returns:
        ScadFile with path, name, variables, and raw_content

    Raises:
        HTTPException: 404 if file not found, 400 if not a .scad file
    """
    settings = get_settings()
    full_path = Path(settings.models_path) / path

    if not full_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"SCAD file not found: {path}"
        )

    if not full_path.suffix.lower() == ".scad":
        raise HTTPException(
            status_code=400,
            detail=f"Not a SCAD file: {path}"
        )

    scad_file = parse_scad_file(full_path)
    return scad_file


@router.put("/file/{path:path}", response_model=ScadFile)
async def update_scad_file(path: str, params: dict[str, Any]) -> ScadFile:
    """Update SCAD file parameters and save changes.

    Args:
        path: Relative path from models_path to the .scad file
        params: Dict of {variable_name: new_value} to update

    Returns:
        Updated ScadFile with new values

    Raises:
        HTTPException: 404 if file not found, 400 if not a .scad file
    """
    settings = get_settings()
    full_path = Path(settings.models_path) / path

    if not full_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"SCAD file not found: {path}"
        )

    if not full_path.suffix.lower() == ".scad":
        raise HTTPException(
            status_code=400,
            detail=f"Not a SCAD file: {path}"
        )

    # Parse current file
    scad_file = parse_scad_file(full_path)

    # Apply parameter changes
    new_content = apply_params(scad_file, params)

    # Write back to file
    full_path.write_text(new_content, encoding="utf-8")

    # Re-parse to return updated state
    updated_file = parse_scad_file(full_path)
    return updated_file


@router.post("/file/{path:path}/reset", response_model=ScadFile)
async def reset_scad_file(path: str) -> ScadFile:
    """Reset all SCAD file parameters to their default values.

    Args:
        path: Relative path from models_path to the .scad file

    Returns:
        Updated ScadFile with default values restored

    Raises:
        HTTPException: 404 if file not found, 400 if not a .scad file
    """
    settings = get_settings()
    full_path = Path(settings.models_path) / path

    if not full_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"SCAD file not found: {path}"
        )

    if not full_path.suffix.lower() == ".scad":
        raise HTTPException(
            status_code=400,
            detail=f"Not a SCAD file: {path}"
        )

    # Parse current file
    scad_file = parse_scad_file(full_path)

    # Build params dict with default values
    reset_params = {
        var.name: var.default_value
        for var in scad_file.variables
    }

    # Apply default values
    new_content = apply_params(scad_file, reset_params)

    # Write back to file
    full_path.write_text(new_content, encoding="utf-8")

    # Re-parse to return updated state
    updated_file = parse_scad_file(full_path)
    return updated_file


@router.post("/file/{path:path}/export", response_model=ExportResult)
async def export_scad_file(path: str) -> ExportResult:
    """Export a SCAD file to STL using OpenSCAD.

    The SCAD file is exported with its current parameter values.
    The output STL file is placed in the same directory as the input SCAD file,
    with the .scad extension replaced by .stl.

    Args:
        path: Relative path from models_path to the .scad file

    Returns:
        ExportResult with success status, output path, and logs

    Raises:
        HTTPException: 404 if file not found, 400 if not a .scad file
    """
    settings = get_settings()
    full_path = Path(settings.models_path) / path

    if not full_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"SCAD file not found: {path}"
        )

    if not full_path.suffix.lower() == ".scad":
        raise HTTPException(
            status_code=400,
            detail=f"Not a SCAD file: {path}"
        )

    # Export the SCAD file to STL
    # Pass None for params - the file already contains current values
    result = await export_scad_to_stl(full_path, params=None)

    return result
