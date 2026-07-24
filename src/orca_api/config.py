"""Application configuration using Pydantic Settings."""

import json
import logging
from functools import cached_property, lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# Path where imported profiles are stored (must match profile_import.py)
IMPORTED_PROFILES_PATH = Path("/opt/orcaslicer/profiles/imported")


class Settings(BaseSettings):
    """Application settings loaded from environment with defaults.
    
    All settings can be overridden via environment variables with ORCA_ prefix.
    Example: ORCA_MODELS_PATH=/path/to/stl/files
    """
    
    # Paths - Override these with environment variables or .env file
    models_path: str = "/data/models"  # Root directory for STL file browser
    sliced_path: str = "/data/output"  # Where sliced files are saved
    profiles_path: str = "/opt/orcaslicer/profiles"  # Built-in profiles
    orcaslicer_bin: str = "/usr/local/bin/orcaslicer"
    openscad_bin: str = "/usr/bin/openscad"  # OpenSCAD CLI for SCAD-to-STL export
    jobs_file: str = "/opt/orca-api/data/jobs.json"
    
    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    debug: bool = False
    
    model_config = {
        "env_prefix": "ORCA_",
        "env_file": ".env",
        "extra": "ignore"
    }

    def _has_imported_machine_profiles(self) -> bool:
        """Check if at least one machine profile has been imported.

        Scans the imported profiles directory for .json files with type="machine".

        Returns:
            True if at least one machine profile exists, False otherwise.
        """
        if not IMPORTED_PROFILES_PATH.exists():
            return False

        for f in IMPORTED_PROFILES_PATH.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    with open(f, "r", encoding="utf-8") as file:
                        data = json.load(file)
                    if data.get("type") == "machine":
                        return True
                except Exception as e:
                    logger.debug(f"Failed to read profile {f.name}: {e}")
                    continue

        return False

    @cached_property
    def is_configured(self) -> bool:
        """Check if the application is properly configured for use.

        The application is considered configured when:
        1. models_path exists as a directory
        2. sliced_path exists as a directory
        3. orcaslicer_bin exists as a file
        4. At least one machine profile has been imported

        Returns:
            True if all configuration requirements are met, False otherwise.
        """
        models_path = Path(self.models_path)
        sliced_path = Path(self.sliced_path)
        orcaslicer_bin = Path(self.orcaslicer_bin)

        # Check paths exist
        if not models_path.is_dir():
            logger.debug(f"Not configured: models_path does not exist: {models_path}")
            return False

        if not sliced_path.is_dir():
            logger.debug(f"Not configured: sliced_path does not exist: {sliced_path}")
            return False

        if not orcaslicer_bin.is_file():
            logger.debug(f"Not configured: orcaslicer_bin does not exist: {orcaslicer_bin}")
            return False

        # Check for at least one imported machine profile
        if not self._has_imported_machine_profiles():
            logger.debug("Not configured: no imported machine profiles found")
            return False

        return True


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
