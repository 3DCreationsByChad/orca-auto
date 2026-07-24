"""Pipeline API endpoints for SCAD-to-printer workflow."""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from orca_api.config import get_settings
from orca_api.models import PipelineJob, PipelineJobCreate
from orca_api.services.pipeline import (
    run_pipeline,
    get_pipeline_job,
    list_pipeline_jobs,
)
from orca_api.services.slicer import get_profile_path, get_machine_profile_path

router = APIRouter(tags=["pipeline"])


@router.post("/pipeline", response_model=PipelineJob, status_code=202)
async def create_pipeline_job(request: PipelineJobCreate) -> JSONResponse:
    """Create and start a new pipeline job.

    Validates SCAD file and profile exist, then starts the pipeline:
    SCAD -> STL export -> slice -> queue

    Args:
        request: Pipeline job creation request

    Returns:
        Created pipeline job with 202 Accepted status

    Raises:
        HTTPException: 400 if SCAD file or profile invalid
    """
    settings = get_settings()

    # Validate SCAD file exists
    scad_path = Path(settings.models_path) / request.scad_path
    if not scad_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"SCAD file not found: {request.scad_path}"
        )

    if not scad_path.suffix.lower() == '.scad':
        raise HTTPException(
            status_code=400,
            detail=f"Not a SCAD file: {request.scad_path}. Must have .scad extension."
        )

    # Validate profile exists
    profile_path = get_profile_path(request.profile)
    if profile_path is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid profile: {request.profile}"
        )

    # Validate machine if provided
    if request.machine:
        machine_path = get_machine_profile_path(request.machine)
        if machine_path is None:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid machine profile: {request.machine}"
            )

    # Start the pipeline
    job = await run_pipeline(
        scad_path=request.scad_path,
        params=request.params,
        profile=request.profile,
        machine=request.machine,
        filament=request.filament,
    )

    return JSONResponse(
        status_code=202,
        content=job.model_dump(mode="json")
    )


@router.get("/pipeline/{job_id}", response_model=PipelineJob)
async def get_pipeline_job_status(job_id: str) -> PipelineJob:
    """Get pipeline job status by ID.

    Args:
        job_id: Pipeline job UUID

    Returns:
        PipelineJob with current stage and status

    Raises:
        HTTPException: 404 if job not found
    """
    job = get_pipeline_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Pipeline job not found: {job_id}"
        )
    return job


@router.get("/pipeline", response_model=list[PipelineJob])
async def list_recent_pipeline_jobs(limit: int = 10) -> list[PipelineJob]:
    """List recent pipeline jobs.

    Args:
        limit: Maximum number of jobs to return (default 10)

    Returns:
        List of PipelineJob sorted by creation time (descending)
    """
    return list_pipeline_jobs(limit=limit)
