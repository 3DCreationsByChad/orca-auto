"""Pipeline service for SCAD-to-printer workflow orchestration.

Coordinates the full workflow: SCAD editing -> STL export -> slicing -> queue.
Reuses existing services for each stage.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from orca_api.config import get_settings
from orca_api.models import PipelineJob, PipelineStatus
from orca_api.services.scad_parser import parse_scad_file, apply_params
from orca_api.services.scad_exporter import export_scad_to_stl
from orca_api.services.job_queue import get_job_queue

logger = logging.getLogger(__name__)

# In-memory storage for pipeline jobs (for simplicity, could be persisted later)
_pipeline_jobs: dict[str, PipelineJob] = {}


def get_pipeline_job(job_id: str) -> Optional[PipelineJob]:
    """Get a pipeline job by ID.

    Args:
        job_id: Pipeline job UUID

    Returns:
        PipelineJob if found, None otherwise
    """
    return _pipeline_jobs.get(job_id)


def list_pipeline_jobs(limit: int = 10) -> list[PipelineJob]:
    """List recent pipeline jobs, most recent first.

    Args:
        limit: Maximum number of jobs to return

    Returns:
        List of PipelineJob sorted by creation time (descending)
    """
    sorted_jobs = sorted(
        _pipeline_jobs.values(),
        key=lambda j: j.created_at,
        reverse=True
    )
    return sorted_jobs[:limit]


async def run_pipeline(
    scad_path: str,
    params: dict[str, Any],
    profile: str,
    machine: Optional[str] = None,
    filament: Optional[str] = None
) -> PipelineJob:
    """Run the full SCAD-to-printer pipeline.

    Orchestrates the workflow:
    1. Validate SCAD file exists and is parseable
    2. Apply parameter overrides to SCAD
    3. Export modified SCAD to STL
    4. Create slice job with exported STL
    5. Track status through all stages

    Args:
        scad_path: Relative path to SCAD file from models_path
        params: Dict of parameter overrides {var_name: value}
        profile: Slicing profile name
        machine: Machine profile name (optional)
        filament: Filament profile name (optional)

    Returns:
        PipelineJob with current status (may still be in progress)
    """
    settings = get_settings()

    # Create pipeline job
    job = PipelineJob(
        id=str(uuid.uuid4()),
        scad_path=scad_path,
        scad_params=params,
        profile=profile,
        machine=machine,
        filament=filament,
        pipeline_status=PipelineStatus.EDITING,
        created_at=datetime.now(timezone.utc),
    )
    _pipeline_jobs[job.id] = job

    logger.info(f"Created pipeline job {job.id}: {scad_path} with profile {profile}")

    # Start async execution
    asyncio.create_task(_execute_pipeline(job))

    return job


async def _execute_pipeline(job: PipelineJob) -> None:
    """Execute the pipeline stages asynchronously.

    This function updates the job in-place as it progresses through stages.

    Args:
        job: The pipeline job to execute
    """
    settings = get_settings()

    try:
        # Stage 1: EDITING - Validate and parse SCAD file
        logger.info(f"Pipeline {job.id}: Stage EDITING - validating SCAD file")
        job.pipeline_status = PipelineStatus.EDITING

        full_scad_path = Path(settings.models_path) / job.scad_path

        if not full_scad_path.exists():
            job.pipeline_status = PipelineStatus.FAILED
            job.error_message = f"SCAD file not found: {job.scad_path}"
            job.completed_at = datetime.now(timezone.utc)
            logger.error(f"Pipeline {job.id} failed: {job.error_message}")
            return

        if not full_scad_path.suffix.lower() == '.scad':
            job.pipeline_status = PipelineStatus.FAILED
            job.error_message = f"Not a SCAD file: {job.scad_path}"
            job.completed_at = datetime.now(timezone.utc)
            logger.error(f"Pipeline {job.id} failed: {job.error_message}")
            return

        # Parse SCAD file to validate it
        try:
            scad_file = parse_scad_file(full_scad_path)
        except Exception as e:
            job.pipeline_status = PipelineStatus.FAILED
            job.error_message = f"Failed to parse SCAD file: {e}"
            job.completed_at = datetime.now(timezone.utc)
            logger.error(f"Pipeline {job.id} failed: {job.error_message}")
            return

        # Apply parameter overrides if provided
        if job.scad_params:
            logger.info(f"Pipeline {job.id}: Applying {len(job.scad_params)} parameter overrides")
            modified_content = apply_params(scad_file, job.scad_params)
            # Write modified content back to file for export
            full_scad_path.write_text(modified_content, encoding='utf-8')

        # Stage 2: EXPORTING - Export SCAD to STL
        logger.info(f"Pipeline {job.id}: Stage EXPORTING - converting SCAD to STL")
        job.pipeline_status = PipelineStatus.EXPORTING

        export_result = await export_scad_to_stl(full_scad_path, params=None)

        if not export_result.success:
            job.pipeline_status = PipelineStatus.FAILED
            job.error_message = f"STL export failed: {export_result.error_message}"
            job.completed_at = datetime.now(timezone.utc)
            logger.error(f"Pipeline {job.id} failed: {job.error_message}")
            return

        job.exported_stl_path = export_result.output_path
        logger.info(f"Pipeline {job.id}: Exported STL to {job.exported_stl_path}")

        # Stage 3: SLICING - Create slice job
        logger.info(f"Pipeline {job.id}: Stage SLICING - creating slice job")
        job.pipeline_status = PipelineStatus.SLICING

        # Get relative path for slice job
        stl_path = Path(job.exported_stl_path)
        try:
            stl_relative_path = str(stl_path.relative_to(settings.models_path))
        except ValueError:
            stl_relative_path = stl_path.name

        # Create slice job
        queue = get_job_queue()
        slice_job = queue.create_job(
            input_path=stl_relative_path,
            profile=job.profile,
            copies=1,
            machine=job.machine,
            filament=job.filament
        )
        job.slice_job_id = slice_job.id

        logger.info(f"Pipeline {job.id}: Created slice job {slice_job.id}")

        # Stage 4: QUEUED - Wait for slice job to complete
        job.pipeline_status = PipelineStatus.QUEUED

        # Poll for slice job completion
        while True:
            await asyncio.sleep(1)  # Poll every second

            updated_slice_job = queue.get_job(slice_job.id)
            if updated_slice_job is None:
                job.pipeline_status = PipelineStatus.FAILED
                job.error_message = "Slice job disappeared from queue"
                job.completed_at = datetime.now(timezone.utc)
                logger.error(f"Pipeline {job.id} failed: {job.error_message}")
                return

            if updated_slice_job.status.value == "completed":
                # Stage 5: COMPLETED
                job.pipeline_status = PipelineStatus.COMPLETED
                job.output_path = updated_slice_job.output_path
                job.print_time_seconds = updated_slice_job.print_time_seconds
                job.filament_grams = updated_slice_job.filament_grams
                job.completed_at = datetime.now(timezone.utc)
                logger.info(
                    f"Pipeline {job.id}: COMPLETED - GCode at {job.output_path}"
                )
                return

            elif updated_slice_job.status.value == "failed":
                job.pipeline_status = PipelineStatus.FAILED
                job.error_message = f"Slicing failed: {updated_slice_job.error_message}"
                job.completed_at = datetime.now(timezone.utc)
                logger.error(f"Pipeline {job.id} failed: {job.error_message}")
                return

            # Still pending or running - continue polling

    except Exception as e:
        job.pipeline_status = PipelineStatus.FAILED
        job.error_message = f"Pipeline error: {str(e)}"
        job.completed_at = datetime.now(timezone.utc)
        logger.exception(f"Pipeline {job.id} exception: {e}")
