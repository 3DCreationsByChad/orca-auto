"""Job queue service for managing slice jobs with persistence."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from orca_api.models import Job, JobStatus
from orca_api.services.slicer import slice_file

logger = logging.getLogger(__name__)


class JobQueue:
    """Job queue with JSON file persistence and async worker.
    
    Manages slice jobs with persistence to a JSON file. Jobs are
    processed sequentially by a background worker.
    """
    
    def __init__(self, jobs_file: Path):
        """Initialize job queue with persistence file.
        
        Args:
            jobs_file: Path to JSON file for job persistence
        """
        self.jobs_file = jobs_file
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._load_jobs()
        logger.info(f"Job queue initialized with {len(self._jobs)} jobs from {jobs_file}")
    
    def _load_jobs(self) -> None:
        """Load jobs from JSON file."""
        if not self.jobs_file.exists():
            logger.info(f"Jobs file not found, starting fresh: {self.jobs_file}")
            return
        
        try:
            with open(self.jobs_file, "r") as f:
                data = json.load(f)
            
            for job_data in data:
                job = Job(**job_data)
                self._jobs[job.id] = job
            
            logger.info(f"Loaded {len(self._jobs)} jobs from {self.jobs_file}")
        except Exception as e:
            logger.error(f"Failed to load jobs: {e}")
            self._jobs = {}
    
    def _save_jobs(self) -> None:
        """Save all jobs to JSON file."""
        try:
            # Ensure directory exists
            self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Convert jobs to list of dicts for JSON serialization
            jobs_list = []
            for job in self._jobs.values():
                job_dict = job.model_dump()
                # Convert datetime objects to ISO strings
                for key in ["created_at", "started_at", "completed_at"]:
                    if job_dict[key] is not None:
                        job_dict[key] = job_dict[key].isoformat()
                jobs_list.append(job_dict)
            
            with open(self.jobs_file, "w") as f:
                json.dump(jobs_list, f, indent=2)
            
            logger.debug(f"Saved {len(jobs_list)} jobs to {self.jobs_file}")
        except Exception as e:
            logger.error(f"Failed to save jobs: {e}")
    
    def create_job(
        self,
        input_path: str,
        profile: str,
        copies: int = 1,
        machine: Optional[str] = None,
        filament: Optional[str] = None
    ) -> Job:
        """Create a new pending job.
        
        Args:
            input_path: Relative path to STL file
            profile: Slicing profile name
            copies: Number of copies to print (default 1)
            machine: Machine profile name (optional)
            filament: Filament profile name (optional)
            
        Returns:
            Newly created Job
        """
        job = Job(
            id=str(uuid.uuid4()),
            input_path=input_path,
            profile=profile,
            copies=copies,
            machine=machine,
            filament=filament,
            status=JobStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        self._jobs[job.id] = job
        self._save_jobs()
        logger.info(
            f"Created job {job.id}: {input_path} with profile {profile}, "
            f"machine {machine or 'default'}, filament {filament or 'auto'} (copies={copies})"
        )
        return job
    
    def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID.
        
        Args:
            job_id: Job UUID
            
        Returns:
            Job if found, None otherwise
        """
        return self._jobs.get(job_id)
    
    def list_jobs(self, limit: int = 20) -> list[Job]:
        """List recent jobs, most recent first.
        
        Args:
            limit: Maximum number of jobs to return
            
        Returns:
            List of jobs sorted by creation time (descending)
        """
        sorted_jobs = sorted(
            self._jobs.values(),
            key=lambda j: j.created_at,
            reverse=True
        )
        return sorted_jobs[:limit]
    
    def get_pending_jobs(self) -> list[Job]:
        """Get pending jobs in creation order.
        
        Returns:
            List of pending jobs sorted by creation time (ascending)
        """
        pending = [j for j in self._jobs.values() if j.status == JobStatus.PENDING]
        return sorted(pending, key=lambda j: j.created_at)
    
    async def process_queue(self) -> None:
        """Background worker that processes pending jobs.
        
        Runs continuously, processing one job at a time.
        """
        self._running = True
        logger.info("Job queue worker started")
        
        while self._running:
            try:
                pending = self.get_pending_jobs()
                if pending:
                    job = pending[0]
                    await self._execute_job(job)
                await asyncio.sleep(1)
            except Exception as e:
                logger.exception(f"Queue worker error: {e}")
                await asyncio.sleep(5)  # Longer delay after error
    
    def stop(self) -> None:
        """Stop the queue worker."""
        self._running = False
        logger.info("Job queue worker stopping")
    
    async def _execute_job(self, job: Job) -> None:
        """Execute a single job.
        
        Args:
            job: Job to execute
        """
        from orca_api.config import get_settings
        
        logger.info(
            f"Executing job {job.id}: {job.input_path}, "
            f"machine={job.machine}, filament={job.filament} (copies={job.copies})"
        )
        
        # Update job to running
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        self._save_jobs()
        
        try:
            # Build full input path
            settings = get_settings()
            full_input_path = str(Path(settings.models_path) / job.input_path)
            
            # Run the slicer with copies, machine, and filament parameters
            result = await slice_file(
                full_input_path,
                job.profile,
                job.copies,
                job.machine,
                job.filament
            )
            
            if result.success:
                job.status = JobStatus.COMPLETED
                job.output_path = result.output_path
                
                # Use metadata from SliceResult (extracted from 3MF file)
                job.print_time_seconds = result.print_time_seconds
                job.filament_grams = result.filament_grams
                
                logger.info(
                    f"Job {job.id} completed: {job.output_path} "
                    f"(time={job.print_time_seconds}s, filament={job.filament_grams}g)"
                )
            else:
                job.status = JobStatus.FAILED
                job.error_message = result.error_message or result.stderr or "Unknown error"
                logger.error(f"Job {job.id} failed: {job.error_message}")
                
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            logger.exception(f"Job {job.id} exception: {e}")
        
        job.completed_at = datetime.now(timezone.utc)
        self._save_jobs()


# Global job queue instance (initialized on startup)
_job_queue: Optional[JobQueue] = None


def get_job_queue() -> JobQueue:
    """Get the global job queue instance.
    
    Raises:
        RuntimeError: If job queue not initialized
    """
    if _job_queue is None:
        raise RuntimeError("Job queue not initialized")
    return _job_queue


def init_job_queue(jobs_file: Path) -> JobQueue:
    """Initialize the global job queue.
    
    Args:
        jobs_file: Path to jobs JSON file
        
    Returns:
        Initialized JobQueue
    """
    global _job_queue
    _job_queue = JobQueue(jobs_file)
    return _job_queue
