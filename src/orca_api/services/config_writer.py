"""Configuration writer service for setup wizard.

Provides functions to write configuration to .env file during initial setup.
"""

import logging
from pathlib import Path

from orca_api.config import get_settings

logger = logging.getLogger(__name__)


def validate_path(path: str, must_be_dir: bool = True) -> tuple[bool, str]:
    """Validate that a path exists and is the correct type.

    Args:
        path: The path to validate
        must_be_dir: If True, path must be a directory. If False, path must be a file.

    Returns:
        Tuple of (is_valid, error_message). Returns (True, "") if valid,
        (False, "error message") if invalid.
    """
    if not path or not path.strip():
        return False, "Path cannot be empty"

    try:
        p = Path(path)

        if not p.exists():
            return False, f"Path does not exist: {path}"

        if must_be_dir:
            if not p.is_dir():
                return False, f"Path is not a directory: {path}"
        else:
            if not p.is_file():
                return False, f"Path is not a file: {path}"

        return True, ""

    except Exception as e:
        logger.exception(f"Error validating path {path}: {e}")
        return False, f"Invalid path: {str(e)}"


def write_env_config(models_path: str, sliced_path: str, orcaslicer_bin: str) -> bool:
    """Write configuration to .env file in the project root.

    Creates or updates the .env file with ORCA_* prefixed variables.
    Preserves other existing variables that don't have the ORCA_ prefix.

    Args:
        models_path: Path to the STL models directory
        sliced_path: Path to the sliced output directory
        orcaslicer_bin: Path to the OrcaSlicer binary

    Returns:
        True on success, False on failure.
    """
    env_file = Path(".env")

    # Variables to write
    new_vars = {
        "ORCA_MODELS_PATH": models_path,
        "ORCA_SLICED_PATH": sliced_path,
        "ORCA_ORCASLICER_BIN": orcaslicer_bin,
    }

    try:
        # Read existing .env content if it exists
        existing_lines = []
        if env_file.exists():
            with open(env_file, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()

        # Parse existing variables and filter out ORCA_* ones we'll replace
        preserved_lines = []
        for line in existing_lines:
            stripped = line.strip()
            # Keep empty lines, comments, and non-ORCA variables
            if not stripped or stripped.startswith("#"):
                preserved_lines.append(line)
            elif "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                # Keep variables that aren't the ones we're setting
                if key not in new_vars:
                    preserved_lines.append(line)

        # Build new content
        output_lines = []

        # Add preserved lines first
        for line in preserved_lines:
            output_lines.append(line if line.endswith("\n") else line + "\n")

        # Add a blank line before our variables if there's existing content
        if output_lines and output_lines[-1].strip():
            output_lines.append("\n")

        # Add our ORCA_ variables
        for key, value in new_vars.items():
            output_lines.append(f"{key}={value}\n")

        # Write the file
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(output_lines)

        logger.info(f"Configuration written to {env_file.absolute()}")
        return True

    except Exception as e:
        logger.exception(f"Failed to write .env file: {e}")
        return False


def clear_settings_cache() -> None:
    """Clear the cached Settings instance to reload configuration.

    After writing new configuration to .env, call this function to ensure
    the next call to get_settings() returns fresh values.
    """
    get_settings.cache_clear()
    logger.info("Settings cache cleared")
