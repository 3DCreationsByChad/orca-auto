"""OrcaSlicer CLI wrapper service for slicing STL files."""

import asyncio
import json
import logging
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from orca_api.config import get_settings

logger = logging.getLogger(__name__)

# Profile paths for Bambu Lab A1 Mini with 0.6mm nozzle (built-in defaults)
MACHINE_PROFILE = "/opt/orcaslicer/orca-slicer-extracted/resources/profiles/BBL/machine/Bambu Lab A1 mini 0.6 nozzle.json"
FILAMENT_PROFILE = "/opt/orcaslicer/orca-slicer-extracted/resources/profiles/BBL/filament/Generic PETG @BBL A1M.json"

# Built-in machine profile name (for matching)
BUILTIN_MACHINE_NAME = "Bambu Lab A1 mini 0.6 nozzle"
BUILTIN_FILAMENT_NAME = "Generic PETG @BBL A1M"

# Slice timeout in seconds (5 minutes)
SLICE_TIMEOUT = 300

# PETG filament weight: approximately 3.53g per meter (1.75mm diameter, 1.27g/cm3 density)
PETG_GRAMS_PER_METER = 3.53

# Imported profiles directory
IMPORTED_PROFILES_PATH = Path("/opt/orcaslicer/profiles/imported")


def get_machine_profiles() -> list[dict]:
    """Get list of available machine profiles with source info.

    Scans built-in machine profile and imported machine profiles.

    Returns:
        List of {name: str, source: "built-in"|"imported", path: str, type: "machine"}
    """
    profiles = []

    # Add built-in A1 Mini machine profile
    profiles.append({
        "name": BUILTIN_MACHINE_NAME,
        "source": "built-in",
        "path": MACHINE_PROFILE,
        "type": "machine"
    })

    # Get imported machine profiles
    if IMPORTED_PROFILES_PATH.exists():
        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    with open(f, "r") as pf:
                        data = json.load(pf)
                    profile_type = data.get("type", "")
                    if profile_type != "machine":
                        continue
                    profiles.append({
                        "name": f.stem,
                        "source": "imported",
                        "path": str(f),
                        "type": "machine"
                    })
                except (json.JSONDecodeError, IOError):
                    continue

    return sorted(profiles, key=lambda p: (p["source"] != "built-in", p["name"]))


def get_filament_profiles() -> list[dict]:
    """Get list of available filament profiles with source info.

    Scans built-in filament profile and imported filament profiles.

    Returns:
        List of {name: str, source: "built-in"|"imported", path: str, type: "filament"}
    """
    profiles = []

    # Add built-in PETG filament profile
    profiles.append({
        "name": BUILTIN_FILAMENT_NAME,
        "source": "built-in",
        "path": FILAMENT_PROFILE,
        "type": "filament"
    })

    # Get imported filament profiles
    if IMPORTED_PROFILES_PATH.exists():
        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    with open(f, "r") as pf:
                        data = json.load(pf)
                    profile_type = data.get("type", "")
                    if profile_type != "filament":
                        continue
                    profiles.append({
                        "name": f.stem,
                        "source": "imported",
                        "path": str(f),
                        "type": "filament"
                    })
                except (json.JSONDecodeError, IOError):
                    continue

    return sorted(profiles, key=lambda p: (p["source"] != "built-in", p["name"]))


def get_compatible_filament_profiles(machine_name: str) -> list[dict]:
    """Get filament profiles compatible with given machine.

    For built-in A1 Mini, returns built-in PETG filament.
    For imported machines, returns filaments that match the machine.

    Args:
        machine_name: Name of the machine profile to filter by

    Returns:
        List of {name: str, source: "built-in"|"imported", path: str}
    """
    profiles = []
    is_builtin_machine = machine_name == BUILTIN_MACHINE_NAME

    # Built-in filament only for built-in machine
    if is_builtin_machine:
        profiles.append({
            "name": BUILTIN_FILAMENT_NAME,
            "source": "built-in",
            "path": FILAMENT_PROFILE
        })

    # Get imported filament profiles
    if IMPORTED_PROFILES_PATH.exists():
        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    with open(f, "r") as pf:
                        data = json.load(pf)
                    if data.get("type") != "filament":
                        continue

                    # Check compatible_printers array (if present)
                    compatible_printers = data.get("compatible_printers", [])

                    # Filament is compatible if:
                    # 1. compatible_printers is empty (compatible with all), or
                    # 2. machine_name is in compatible_printers list
                    if not compatible_printers or machine_name in compatible_printers:
                        profiles.append({
                            "name": f.stem,
                            "source": "imported",
                            "path": str(f)
                        })
                except (json.JSONDecodeError, IOError):
                    continue

    return sorted(profiles, key=lambda p: p["name"])


def get_compatible_filament_profile(machine_name: str) -> str:
    """Get a compatible filament profile path for the given machine (auto-select).

    For built-in A1 Mini, returns the built-in PETG profile.
    For imported machines, looks for an imported filament profile that matches
    the machine name pattern (e.g., contains machine name or is generic PETG).

    Args:
        machine_name: Machine profile name

    Returns:
        Path to a compatible filament profile
    """
    # Built-in machine uses built-in filament
    if machine_name == BUILTIN_MACHINE_NAME:
        return FILAMENT_PROFILE

    # For imported machines, look for a matching filament profile
    if IMPORTED_PROFILES_PATH.exists():
        # First, try to find a PETG filament that matches the machine
        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    with open(f, "r") as pf:
                        data = json.load(pf)
                    if data.get("type") != "filament":
                        continue

                    # Check if filament name contains "PETG" and machine name
                    name = f.stem.lower()
                    if "petg" in name and machine_name.lower().split()[0] in name:
                        logger.info(f"Using filament profile {f.stem} for machine {machine_name}")
                        return str(f)
                except (json.JSONDecodeError, IOError):
                    continue

        # Second pass: find any filament that mentions the machine
        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    with open(f, "r") as pf:
                        data = json.load(pf)
                    if data.get("type") != "filament":
                        continue

                    # Check if filament name contains part of the machine name
                    name = f.stem
                    # Extract machine identifier (first few words)
                    machine_id = " ".join(machine_name.split()[:3])
                    if machine_id in name:
                        logger.info(f"Using filament profile {name} for machine {machine_name}")
                        return str(f)
                except (json.JSONDecodeError, IOError):
                    continue

    # Fallback to built-in PETG (may cause compatibility issues)
    logger.warning(f"No compatible filament found for {machine_name}, using default PETG")
    return FILAMENT_PROFILE


def get_compatible_process_profiles(machine_name: str) -> list[dict]:
    """Get process profiles compatible with given machine.

    Args:
        machine_name: Name of the machine profile to filter by

    Returns:
        List of {name: str, source: "built-in"|"imported", path: str} for compatible process profiles
    """
    settings = get_settings()
    profiles = []

    # For built-in A1 Mini, include all built-in profiles (they're all for A1 Mini)
    is_builtin_machine = machine_name == BUILTIN_MACHINE_NAME

    # Get built-in profiles (always compatible with built-in machine)
    profiles_path = Path(settings.profiles_path)
    if profiles_path.exists() and is_builtin_machine:
        for f in profiles_path.iterdir():
            if f.is_file() and f.suffix == ".json":
                # Skip non-profile JSON files
                if f.name in ("base_export.json", "result.json", "test_config.json"):
                    continue
                profiles.append({
                    "name": f.stem,
                    "source": "built-in",
                    "path": str(f)
                })

    # Get imported process profiles that are compatible with the selected machine
    if IMPORTED_PROFILES_PATH.exists():
        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    with open(f, "r") as pf:
                        data = json.load(pf)
                    profile_type = data.get("type", "")
                    if profile_type != "process":
                        continue

                    # Check compatible_printers array
                    compatible_printers = data.get("compatible_printers", [])

                    # Profile is compatible if:
                    # 1. compatible_printers is empty (compatible with all), or
                    # 2. machine_name is in compatible_printers list
                    if not compatible_printers or machine_name in compatible_printers:
                        profiles.append({
                            "name": f.stem,
                            "source": "imported",
                            "path": str(f)
                        })
                except (json.JSONDecodeError, IOError):
                    continue

    return sorted(profiles, key=lambda p: p["name"])


def get_available_profiles() -> list[dict]:
    """Get list of available slicing profiles with source info.

    Scans both built-in profiles directory and imported profiles directory.

    Returns:
        List of {name: str, source: "built-in"|"imported", path: str}
    """
    settings = get_settings()
    profiles = []

    # Get built-in profiles
    profiles_path = Path(settings.profiles_path)
    if profiles_path.exists():
        for f in profiles_path.iterdir():
            if f.is_file() and f.suffix == ".json":
                # Skip non-profile JSON files
                if f.name in ("base_export.json", "result.json", "test_config.json"):
                    continue
                profiles.append({
                    "name": f.stem,
                    "source": "built-in",
                    "path": str(f)
                })
    else:
        logger.warning(f"Profiles path does not exist: {profiles_path}")

    # Get imported profiles (only process type for slicing)
    if IMPORTED_PROFILES_PATH.exists():
        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json":
                # Check profile type - only include process profiles
                try:
                    with open(f, "r") as pf:
                        data = json.load(pf)
                    profile_type = data.get("type", "")
                    if profile_type != "process":
                        continue
                except (json.JSONDecodeError, IOError):
                    continue
                profiles.append({
                    "name": f.stem,
                    "source": "imported",
                    "path": str(f)
                })

    return sorted(profiles, key=lambda p: p["name"])


def get_profile_names() -> list[str]:
    """Get list of profile names only (for backward compatibility).

    Returns:
        List of profile names (without .json extension)
    """
    return [p["name"] for p in get_available_profiles()]


def get_profile_path(profile_name: str) -> Optional[Path]:
    """Get the full path for a profile by name.

    Checks built-in profiles first, then imported profiles.

    Args:
        profile_name: Profile name (without .json extension)

    Returns:
        Path to profile JSON file, or None if not found
    """
    settings = get_settings()

    # Check built-in profiles first
    builtin_path = Path(settings.profiles_path) / f"{profile_name}.json"
    if builtin_path.exists():
        return builtin_path

    # Check imported profiles
    imported_path = IMPORTED_PROFILES_PATH / f"{profile_name}.json"
    if imported_path.exists():
        return imported_path

    return None


def get_machine_profile_path(machine_name: str) -> Optional[str]:
    """Get the full path for a machine profile by name.

    Args:
        machine_name: Machine profile name

    Returns:
        Path to machine profile JSON file, or None if not found
    """
    # Check if it's the built-in A1 Mini
    if machine_name == BUILTIN_MACHINE_NAME:
        return MACHINE_PROFILE

    # Check imported machine profiles
    if IMPORTED_PROFILES_PATH.exists():
        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json" and f.stem == machine_name:
                try:
                    with open(f, "r") as pf:
                        data = json.load(pf)
                    if data.get("type") == "machine":
                        return str(f)
                except (json.JSONDecodeError, IOError):
                    continue

    return None


def get_filament_profile_path(filament_name: str) -> Optional[str]:
    """Get the full path for a filament profile by name.

    Args:
        filament_name: Filament profile name

    Returns:
        Path to filament profile JSON file, or None if not found
    """
    # Check if it's the built-in PETG
    if filament_name == BUILTIN_FILAMENT_NAME:
        return FILAMENT_PROFILE

    # Check imported filament profiles
    if IMPORTED_PROFILES_PATH.exists():
        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json" and f.stem == filament_name:
                try:
                    with open(f, "r") as pf:
                        data = json.load(pf)
                    if data.get("type") == "filament":
                        return str(f)
                except (json.JSONDecodeError, IOError):
                    continue

    return None


def compute_output_path(input_path: str, profile: str) -> Path:
    """Compute output path in parallel sliced folder structure.

    Args:
        input_path: Full path to input STL file
        profile: Profile name used for slicing

    Returns:
        Path for output .gcode.3mf file

    Example:
        Input: /data/models/folder/file.stl
        Output: /data/models-sliced/folder/file_profile.gcode.3mf
    """
    settings = get_settings()
    models_path = Path(settings.models_path)
    sliced_path = Path(settings.sliced_path)

    input_p = Path(input_path)

    # Get relative path from models root
    try:
        relative = input_p.relative_to(models_path)
    except ValueError:
        # Input is not under models_path, use filename only
        relative = Path(input_p.name)

    # Build output path
    output_dir = sliced_path / relative.parent
    output_name = f"{input_p.stem}_{profile}.gcode.3mf"
    output_path = output_dir / output_name

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    return output_path


def parse_3mf_metadata(output_path: str) -> tuple[int | None, float | None]:
    """Parse print time and filament usage from 3MF output file.

    Extracts metadata from Metadata/slice_info.config inside the 3MF archive.

    Args:
        output_path: Path to the output .gcode.3mf file

    Returns:
        Tuple of (print_time_seconds, filament_grams). Either or both may be None
        if extraction fails or data is not present.
    """
    print_time_seconds = None
    filament_grams = None

    try:
        with zipfile.ZipFile(output_path, 'r') as zf:
            # Read slice_info.config from the archive
            with zf.open('Metadata/slice_info.config') as f:
                xml_content = f.read()

        # Parse XML
        root = ET.fromstring(xml_content)

        # Find print time prediction
        for metadata in root.findall('.//plate/metadata'):
            if metadata.get('key') == 'prediction':
                try:
                    print_time_seconds = int(metadata.get('value', 0))
                except (ValueError, TypeError):
                    pass

        # Find filament usage - sum across all filaments
        total_meters = 0.0
        total_grams = 0.0

        for filament in root.findall('.//plate/filament'):
            try:
                used_m = float(filament.get('used_m', 0))
                used_g = float(filament.get('used_g', 0))
                total_meters += used_m
                total_grams += used_g
            except (ValueError, TypeError):
                pass

        # If grams is 0 but we have meters, compute grams from meters
        # OrcaSlicer often outputs 0.00 for used_g
        if total_grams == 0 and total_meters > 0:
            total_grams = total_meters * PETG_GRAMS_PER_METER

        if total_grams > 0:
            filament_grams = round(total_grams, 2)

        logger.info(f"Parsed 3MF metadata: time={print_time_seconds}s, filament={filament_grams}g")

    except zipfile.BadZipFile:
        logger.warning(f"Failed to parse 3MF metadata: not a valid zip file: {output_path}")
    except KeyError:
        logger.warning(f"Failed to parse 3MF metadata: slice_info.config not found in: {output_path}")
    except ET.ParseError as e:
        logger.warning(f"Failed to parse 3MF metadata: XML parse error: {e}")
    except Exception as e:
        logger.warning(f"Failed to parse 3MF metadata: {e}")

    return (print_time_seconds, filament_grams)


async def slice_file(
    input_path: str,
    profile: str,
    copies: int = 1,
    machine: str | None = None,
    filament: str | None = None
) -> "SliceResult":
    """Slice an STL file using OrcaSlicer with specified profile.

    For multiple copies, the input file is passed multiple times with --arrange 1
    to auto-arrange copies on the build plate. Note: OrcaSlicer's --repetitions
    flag does not work with --slice, so we use multiple file inputs instead.

    Args:
        input_path: Path to input STL file
        profile: Profile name (e.g., "draft", "standard", "quality", "functional")
        copies: Number of copies to arrange on build plate (1-99, default 1)
        machine: Machine profile name (optional, defaults to built-in A1 Mini)
        filament: Filament profile name (optional, auto-selects based on machine if not provided)

    Returns:
        SliceResult with success status, output path, and logs
    """
    from orca_api.models import SliceResult

    settings = get_settings()
    start_time = time.time()

    # Validate profile exists - use get_profile_path for both built-in and imported
    profile_path = get_profile_path(profile)
    if profile_path is None:
        available = get_profile_names()
        return SliceResult(
            success=False,
            output_path=None,
            stdout="",
            stderr="",
            duration_seconds=0,
            error_message=f"Invalid profile \"{profile}\". Available: {available}"
        )

    # Validate input file exists
    input_p = Path(input_path)
    if not input_p.exists():
        return SliceResult(
            success=False,
            output_path=None,
            stdout="",
            stderr="",
            duration_seconds=0,
            error_message=f"Input file not found: {input_path}"
        )

    # Determine machine profile path and name
    if machine is None:
        machine_profile_path = MACHINE_PROFILE
        machine_name = BUILTIN_MACHINE_NAME
    else:
        machine_profile_path = get_machine_profile_path(machine)
        if machine_profile_path is None:
            # Fallback to built-in if machine not found
            logger.warning(f"Machine profile '{machine}' not found, using default A1 Mini")
            machine_profile_path = MACHINE_PROFILE
            machine_name = BUILTIN_MACHINE_NAME
        else:
            machine_name = machine

    # Determine filament profile path
    if filament is None:
        # Auto-select based on machine
        filament_profile_path = get_compatible_filament_profile(machine_name)
        logger.info(f"Auto-selected filament profile: {filament_profile_path}")
    else:
        # Use explicitly selected filament
        filament_profile_path = get_filament_profile_path(filament)
        if filament_profile_path is None:
            # Fallback to auto-select if filament not found
            logger.warning(f"Filament profile '{filament}' not found, auto-selecting")
            filament_profile_path = get_compatible_filament_profile(machine_name)
        else:
            logger.info(f"Using selected filament profile: {filament}")

    # Compute output path
    output_path = compute_output_path(input_path, profile)

    # Build settings load path with machine profile
    load_settings = f"{profile_path};{machine_profile_path}"

    # Build command
    cmd = [
        settings.orcaslicer_bin,
        "--load-settings", load_settings,
        "--load-filaments", filament_profile_path,
    ]

    # For multiple copies, use --arrange 1 and pass file multiple times
    # Use --slice 1 for plate 1 (required for arrange to work properly)
    if copies > 1:
        cmd.extend(["--arrange", "1"])
        cmd.extend(["--slice", "1"])
    else:
        cmd.extend(["--slice", "0"])

    cmd.extend(["--export-3mf", str(output_path)])

    # Add input file(s) - repeat for number of copies
    for _ in range(copies):
        cmd.append(str(input_p))

    logger.info(f"Slicing {input_path} with profile {profile}, machine {machine_name} (copies={copies})")
    cmd_str = " ".join(cmd)
    logger.debug(f"Command: {cmd_str}")

    try:
        # Run with DISPLAY for headless X11
        env = {"DISPLAY": ":99"}

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**env}
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=SLICE_TIMEOUT
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            duration = time.time() - start_time
            return SliceResult(
                success=False,
                output_path=None,
                stdout="",
                stderr="",
                duration_seconds=duration,
                error_message=f"Slice timed out after {SLICE_TIMEOUT} seconds"
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        duration = time.time() - start_time

        # Check if output was created
        if output_path.exists():
            # Parse metadata from the output 3MF file
            print_time_seconds, filament_grams = parse_3mf_metadata(str(output_path))

            logger.info(f"Slice successful: {output_path} ({duration:.1f}s)")
            return SliceResult(
                success=True,
                output_path=str(output_path),
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                error_message=None,
                print_time_seconds=print_time_seconds,
                filament_grams=filament_grams
            )
        else:
            logger.error(f"Slice failed: output not created")
            return SliceResult(
                success=False,
                output_path=None,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                error_message="Slice completed but output file was not created"
            )

    except Exception as e:
        duration = time.time() - start_time
        logger.exception(f"Slice error: {e}")
        return SliceResult(
            success=False,
            output_path=None,
            stdout="",
            stderr="",
            duration_seconds=duration,
            error_message=str(e)
        )
