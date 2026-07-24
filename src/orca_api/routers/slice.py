"""Slice job creation endpoint."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from orca_api.config import get_settings
from orca_api.models import JobCreate, JobResponse
from orca_api.services.job_queue import get_job_queue
from orca_api.services.slicer import get_available_profiles

router = APIRouter(tags=["slice"])


@router.post("/slice", response_model=JobResponse, status_code=202)
async def create_slice_job(request: JobCreate) -> JSONResponse:
    """Create a new slice job.
    
    Validates file and profile exist, creates a pending job,
    and returns immediately with 202 Accepted.
    
    Args:
        request: Job creation request with file_path, profile, and optional copies
        
    Returns:
        Created job with 202 status
        
    Raises:
        HTTPException: 400 if file or profile invalid
    """
    settings = get_settings()
    
    # Validate file exists
    full_path = Path(settings.models_path) / request.file_path
    if not full_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"File not found: {request.file_path}"
        )
    
    if not full_path.suffix.lower() in (".stl", ".3mf", ".step", ".stp"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type: {full_path.suffix}. Supported: .stl, .3mf, .step, .stp"
        )
    
    # Validate profile exists
    available_profiles = get_available_profiles()
    if request.profile not in available_profiles:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid profile: {request.profile}. Available: {available_profiles}"
        )
    
    # Create job with copies parameter
    queue = get_job_queue()
    job = queue.create_job(request.file_path, request.profile, request.copies)
    
    return JSONResponse(
        status_code=202,
        content=job.model_dump(mode="json")
    )


@router.get("/profiles", response_model=list[str])
async def list_profiles() -> list[str]:
    """Get available slicing profiles.
    
    Returns:
        List of profile names
    """
    return get_available_profiles()
