"""File browser API endpoints."""

import logging
from typing import Union

from fastapi import APIRouter, HTTPException

from orca_api.models import FileInfo, FileMetadata
from orca_api.services.file_browser import list_directory, get_file_metadata, _validate_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/", response_model=list[FileInfo])
async def list_root_directory() -> list[FileInfo]:
    """
    List contents of the root models directory.
    
    Returns:
        List of files and directories at the root level
    """
    try:
        return list_directory("")
    except FileNotFoundError as e:
        logger.error(f"Root directory not found: {e}")
        raise HTTPException(status_code=500, detail="Models directory not accessible")
    except Exception as e:
        logger.error(f"Error listing root directory: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{path:path}", response_model=Union[list[FileInfo], FileMetadata])
async def get_path(path: str) -> Union[list[FileInfo], FileMetadata]:
    """
    Get directory contents or file metadata.
    
    Args:
        path: Relative path from models root
        
    Returns:
        List of FileInfo if path is a directory,
        FileMetadata if path is a file
    """
    try:
        # Validate path first (security check)
        full_path = _validate_path(path)
        
        if not full_path.exists():
            raise HTTPException(status_code=404, detail=f"Path not found: {path}")
        
        if full_path.is_dir():
            return list_directory(path)
        else:
            return get_file_metadata(path)
            
    except ValueError as e:
        # Path traversal attempt
        logger.warning(f"Path traversal blocked: {path}")
        raise HTTPException(status_code=400, detail="Invalid path")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    except Exception as e:
        logger.error(f"Error accessing path {path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
