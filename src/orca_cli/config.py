"""Configuration management for orca-auto CLI."""

import json
from pathlib import Path
from typing import Optional

# Default configuration
DEFAULT_API_URL = "http://localhost:8000"
CONFIG_DIR = Path.home() / ".config" / "orca-auto"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _ensure_config_dir() -> None:
    """Create config directory if it doesn't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load configuration from file.
    
    Returns:
        dict with keys: api_url, default_profile (optional)
    """
    if not CONFIG_FILE.exists():
        return {"api_url": DEFAULT_API_URL}
    
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            # Ensure api_url exists
            if "api_url" not in config:
                config["api_url"] = DEFAULT_API_URL
            return config
    except (json.JSONDecodeError, IOError):
        return {"api_url": DEFAULT_API_URL}


def save_config(config: dict) -> None:
    """Save configuration to file.
    
    Args:
        config: dict with api_url and optionally default_profile
    """
    _ensure_config_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_api_url() -> str:
    """Get the configured API URL.
    
    Returns:
        API URL string
    """
    return load_config().get("api_url", DEFAULT_API_URL)


def get_default_profile() -> Optional[str]:
    """Get the default profile if configured.
    
    Returns:
        Profile name or None
    """
    return load_config().get("default_profile")


def set_api_url(url: str) -> None:
    """Set the API URL.
    
    Args:
        url: API URL string
    """
    config = load_config()
    config["api_url"] = url
    save_config(config)


def set_default_profile(profile: str) -> None:
    """Set the default profile.
    
    Args:
        profile: Profile name
    """
    config = load_config()
    config["default_profile"] = profile
    save_config(config)
