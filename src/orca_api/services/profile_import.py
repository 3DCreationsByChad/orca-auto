"""Profile import service for OrcaSlicer profile bundles (.orca_printer files)."""

import json
import logging
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Directory for imported profiles
IMPORTED_PROFILES_PATH = Path("/opt/orcaslicer/profiles/imported")

# Valid profile types - determined by subdirectory or "type" field
VALID_PROFILE_TYPES = {"process", "filament", "machine", "machine_model", "printer"}


@dataclass
class ProfileImportResult:
    """Result of profile bundle import operation."""
    imported_count: int = 0
    profiles: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sanitize_filename(name: str) -> str:
    """Sanitize a filename to be safe for filesystem use."""
    sanitized = re.sub(r"[<>:\"/\\|?*]", "_", name)
    sanitized = sanitized.replace("..", "_")
    sanitized = sanitized.strip(" .")
    if len(sanitized) > 200:
        sanitized = sanitized[:200]
    return sanitized


def determine_profile_type(json_file: Path, profile_data: dict) -> str | None:
    """Determine profile type from subdirectory or type field.

    OrcaSlicer exports use subdirectory structure:
    - process/ -> process profiles
    - filament/ -> filament profiles
    - printer/ or machine/ -> machine profiles

    Some profiles have explicit "type" field.
    """
    # Check explicit type field first
    if "type" in profile_data:
        return profile_data["type"]

    # Infer from path - check parent directory name
    parent_dir = json_file.parent.name.lower()

    if parent_dir == "process":
        return "process"
    elif parent_dir == "filament":
        return "filament"
    elif parent_dir in ("printer", "machine"):
        return "machine"

    return None


def extract_profile_bundle(file_content: bytes, filename: str) -> ProfileImportResult:
    """Extract and import profiles from an .orca_printer bundle."""
    result = ProfileImportResult()

    IMPORTED_PROFILES_PATH.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        temp_zip = temp_path / "bundle.zip"

        try:
            temp_zip.write_bytes(file_content)
        except Exception as e:
            result.errors.append(f"Failed to write temp file: {e}")
            return result

        try:
            with zipfile.ZipFile(temp_zip, "r") as zf:
                for name in zf.namelist():
                    if ".." in name:
                        result.errors.append(f"Path traversal detected: {name}")
                        return result
                zf.extractall(temp_path / "extracted")
        except zipfile.BadZipFile:
            result.errors.append(f"Invalid ZIP file: {filename}")
            return result
        except Exception as e:
            result.errors.append(f"Failed to extract ZIP: {e}")
            return result

        extracted_path = temp_path / "extracted"
        json_files = list(extracted_path.rglob("*.json"))
        logger.info(f"Found {len(json_files)} JSON files in {filename}")

        for json_file in json_files:
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    profile_data = json.load(f)

                # Determine profile type from path or field
                profile_type = determine_profile_type(json_file, profile_data)

                if profile_type not in VALID_PROFILE_TYPES:
                    logger.debug(f"Skipping {json_file.name} (type={profile_type})")
                    continue

                # Get profile name from data or filename
                profile_name = profile_data.get("name", json_file.stem)

                safe_name = sanitize_filename(profile_name)
                if not safe_name:
                    safe_name = sanitize_filename(json_file.stem)

                output_filename = f"{safe_name}.json"
                output_path = IMPORTED_PROFILES_PATH / output_filename

                # Add import metadata
                profile_data["source"] = "imported"
                profile_data["type"] = profile_type  # Ensure type is set
                profile_data["imported_from"] = filename
                profile_data["imported_at"] = datetime.now().isoformat()

                # Fix for machine profiles: OrcaSlicer CLI requires 'inherits' field
                # to match the printer name for compatibility checking. If not set,
                # set it to the profile name so process profiles can be matched.
                if profile_type == "machine" and not profile_data.get("inherits"):
                    profile_data["inherits"] = profile_name
                    logger.info(f"Added inherits={profile_name} to machine profile for CLI compatibility")

                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(profile_data, f, indent=2)

                result.imported_count += 1
                result.profiles.append({
                    "name": profile_name,
                    "type": profile_type,
                    "path": str(output_path),
                    "filename": output_filename
                })

                logger.info(f"Imported {profile_type} profile: {profile_name}")

            except json.JSONDecodeError as e:
                result.errors.append(f"Invalid JSON in {json_file.name}: {e}")
            except Exception as e:
                result.errors.append(f"Error processing {json_file.name}: {e}")

    logger.info(f"Import complete: {result.imported_count} profiles from {filename}")
    return result


def list_imported_profiles() -> list[dict]:
    """List all imported profiles."""
    profiles = []

    if not IMPORTED_PROFILES_PATH.exists():
        return profiles

    for f in IMPORTED_PROFILES_PATH.iterdir():
        if f.is_file() and f.suffix == ".json":
            try:
                with open(f, "r", encoding="utf-8") as file:
                    data = json.load(file)

                profiles.append({
                    "name": data.get("name", f.stem),
                    "type": data.get("type", "unknown"),
                    "path": str(f),
                    "imported_at": data.get("imported_at"),
                    "imported_from": data.get("imported_from")
                })
            except Exception as e:
                logger.warning(f"Failed to read profile {f.name}: {e}")
                profiles.append({
                    "name": f.stem,
                    "type": "unknown",
                    "path": str(f),
                    "imported_at": None,
                    "imported_from": None
                })

    return sorted(profiles, key=lambda p: p["name"])


def delete_imported_profile(name: str) -> bool:
    """Delete an imported profile by name."""
    if not IMPORTED_PROFILES_PATH.exists():
        return False

    safe_name = sanitize_filename(name)
    target_path = IMPORTED_PROFILES_PATH / f"{safe_name}.json"

    if target_path.exists():
        target_path.unlink()
        logger.info(f"Deleted imported profile: {name}")
        return True

    for f in IMPORTED_PROFILES_PATH.iterdir():
        if f.is_file() and f.suffix == ".json":
            try:
                with open(f, "r", encoding="utf-8") as file:
                    data = json.load(file)
                if data.get("name") == name:
                    f.unlink()
                    logger.info(f"Deleted imported profile: {name}")
                    return True
            except Exception:
                pass

    return False
