"""File browser service for navigating the models directory."""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import trimesh

from orca_api.config import get_settings
from orca_api.models import FileInfo, FileMetadata


# Allowed extensions for sliceable files
SLICEABLE_EXTENSIONS = {".stl", ".obj", ".3mf", ".step", ".stp"}


def is_sliceable(path: Path) -> bool:
    """Check if file has a sliceable extension."""
    return path.suffix.lower() in SLICEABLE_EXTENSIONS


def _validate_path(path: str) -> Path:
    """
    Validate path stays within models directory.
    
    Raises:
        ValueError: If path traversal attempted
    """
    settings = get_settings()
    base_path = Path(settings.models_path).resolve()
    
    # Handle empty path as root
    if not path or path == ".":
        return base_path
    
    # Resolve the full path
    full_path = (base_path / path).resolve()
    
    # Security check: ensure path stays within base
    try:
        full_path.relative_to(base_path)
    except ValueError:
        raise ValueError(f"Path traversal not allowed: {path}")
    
    return full_path


def _count_sliceable_files(dir_path: Path) -> int:
    """Count sliceable files in directory (non-recursive for performance)."""
    count = 0
    try:
        for item in dir_path.iterdir():
            if item.is_file() and is_sliceable(item):
                count += 1
    except PermissionError:
        pass
    return count


def list_directory(path: str = "") -> list[FileInfo]:
    """
    List files and directories at the given path.
    
    Args:
        path: Relative path from models root (empty string for root)
        
    Returns:
        List of FileInfo objects, directories first then files alphabetically
        
    Raises:
        FileNotFoundError: If path does not exist
        ValueError: If path traversal attempted
    """
    settings = get_settings()
    base_path = Path(settings.models_path)
    full_path = _validate_path(path)
    
    if not full_path.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    
    if not full_path.is_dir():
        raise ValueError(f"Not a directory: {path}")
    
    items: list[FileInfo] = []
    
    for item in full_path.iterdir():
        # Skip hidden files
        if item.name.startswith("."):
            continue
            
        # Filter to directories and sliceable files only
        if item.is_file() and not is_sliceable(item):
            continue
        
        stat = item.stat()
        relative_path = str(item.relative_to(base_path))
        
        file_info = FileInfo(
            name=item.name,
            path=relative_path,
            is_dir=item.is_dir(),
            size=stat.st_size if item.is_file() else 0,
            modified=datetime.fromtimestamp(stat.st_mtime),
            file_count=_count_sliceable_files(item) if item.is_dir() else None,
        )
        items.append(file_info)
    
    # Sort: directories first, then alphabetically by name
    items.sort(key=lambda x: (not x.is_dir, x.name.lower()))
    
    return items


def get_file_metadata(path: str) -> FileMetadata:
    """
    Get detailed metadata for a file, including STL mesh info.
    
    Args:
        path: Relative path from models root
        
    Returns:
        FileMetadata with mesh dimensions and vertex count
        
    Raises:
        FileNotFoundError: If file does not exist
        ValueError: If path traversal attempted or not a file
    """
    settings = get_settings()
    base_path = Path(settings.models_path)
    full_path = _validate_path(path)
    
    if not full_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    
    if not full_path.is_file():
        raise ValueError(f"Not a file: {path}")
    
    stat = full_path.stat()
    relative_path = str(full_path.relative_to(base_path))
    
    # Base file info
    metadata = FileMetadata(
        name=full_path.name,
        path=relative_path,
        is_dir=False,
        size=stat.st_size,
        modified=datetime.fromtimestamp(stat.st_mtime),
    )
    
    # Try to extract mesh metadata
    if is_sliceable(full_path):
        try:
            mesh = trimesh.load_mesh(str(full_path))
            
            if hasattr(mesh, "vertices"):
                metadata.vertices = len(mesh.vertices)
            
            if hasattr(mesh, "bounds") and mesh.bounds is not None:
                bounds = mesh.bounds
                metadata.dimensions = {
                    "x": round(float(bounds[1][0] - bounds[0][0]), 2),
                    "y": round(float(bounds[1][1] - bounds[0][1]), 2),
                    "z": round(float(bounds[1][2] - bounds[0][2]), 2),
                }
            
            if hasattr(mesh, "volume"):
                # Convert mm^3 to cm^3
                metadata.volume = round(float(mesh.volume) / 1000, 3)
                
        except Exception:
            # Failed to parse mesh - return basic metadata
            pass
    
    return metadata
