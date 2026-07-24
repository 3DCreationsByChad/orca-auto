"""Job management API endpoints."""

from fastapi import APIRouter, HTTPException

from orca_api.models import Job, JobResponse
from orca_api.services.job_queue import get_job_queue

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/", response_model=list[JobResponse])
async def list_jobs(limit: int = 20) -> list[Job]:
    """List recent jobs, most recent first.
    
    Args:
        limit: Maximum number of jobs to return (default 20)
        
    Returns:
        List of jobs sorted by creation time (descending)
    """
    queue = get_job_queue()
    return queue.list_jobs(limit=limit)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> Job:
    """Get job by ID.
    
    Args:
        job_id: Job UUID
        
    Returns:
        Job details
        
    Raises:
        HTTPException: 404 if job not found
    """
    queue = get_job_queue()
    job = queue.get_job(job_id)
    
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    
    return job
