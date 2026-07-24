"""Profile management API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File

from orca_api.services.profile_import import (
    extract_profile_bundle,
    list_imported_profiles,
    delete_imported_profile,
    ProfileImportResult,
    IMPORTED_PROFILES_PATH,
)
from orca_api.services.slicer import get_available_profiles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profiles", tags=["profiles"])

# Max file size for profile bundles (10MB)
MAX_PROFILE_BUNDLE_SIZE = 10 * 1024 * 1024


@router.post("/import")
async def import_profile_bundle(file: UploadFile = File(...)) -> dict:
    """Import an OrcaSlicer profile bundle (.orca_printer file).

    Accepts a multipart file upload of an .orca_printer file (ZIP format).
    Extracts process, filament, and machine profiles from the bundle and
    stores them in the imported profiles directory.

    Args:
        file: Uploaded .orca_printer file

    Returns:
        JSON with success status, imported count, profiles list, and any errors
    """
    # Validate file extension
    if not file.filename or not file.filename.endswith(".orca_printer"):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only .orca_printer files are accepted."
        )

    # Read file content with size limit
    content = await file.read()
    if len(content) > MAX_PROFILE_BUNDLE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is {MAX_PROFILE_BUNDLE_SIZE // (1024*1024)}MB."
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    # Extract profiles
    try:
        result = extract_profile_bundle(content, file.filename)
    except Exception as e:
        logger.exception(f"Profile import failed: {e}")
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")

    return {
        "success": result.imported_count > 0 or len(result.errors) == 0,
        "imported_count": result.imported_count,
        "profiles": result.profiles,
        "errors": result.errors,
    }


@router.get("")
async def list_profiles() -> list[dict]:
    """Get combined list of built-in and imported profiles.

    Returns:
        List of profiles with name, source (built-in/imported), and path
    """
    return get_available_profiles()


@router.get("/imported")
async def list_imported() -> list[dict]:
    """List only imported profiles with full metadata.

    Returns:
        List of imported profiles with name, type, path, imported_at, imported_from
    """
    return list_imported_profiles()


@router.delete("/imported/{name}")
async def delete_profile(name: str) -> dict:
    """Delete an imported profile by name.

    Args:
        name: Profile name to delete

    Returns:
        JSON with success status and message
    """
    if not name or name.strip() == "":
        raise HTTPException(status_code=400, detail="Profile name is required.")

    success = delete_imported_profile(name)

    if success:
        return {"success": True, "message": f"Profile '{name}' deleted."}
    else:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found.")
