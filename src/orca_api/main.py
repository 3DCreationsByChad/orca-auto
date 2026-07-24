"""FastAPI application for OrcaSlicer batch slicing."""

import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from orca_api import __version__
from orca_api.config import get_settings
from orca_api.models import HealthResponse
from orca_api.services.job_queue import init_job_queue, get_job_queue

logger = logging.getLogger(__name__)


class SetupRedirectMiddleware(BaseHTTPMiddleware):
    """Middleware to redirect unconfigured app to setup wizard.

    When the application is not configured (is_configured returns False),
    this middleware redirects UI requests to the setup wizard page.

    Allowed paths when unconfigured:
    - /ui/setup (the wizard itself)
    - /api/* (API needs to work for CLI setup)
    - /health (health checks)
    - /docs, /redoc, /openapi.json (API documentation)
    """

    # Paths that should be accessible even when unconfigured
    ALLOWED_PATHS = (
        "/ui/setup",
        "/api/",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    )

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path

        # Check if redirect is needed
        if not settings.is_configured:
            # Only redirect UI paths (except setup)
            if path.startswith("/ui") and not path.startswith("/ui/setup"):
                logger.debug(f"Redirecting unconfigured request from {path} to /ui/setup")
                return RedirectResponse(url="/ui/setup", status_code=307)

            # Also redirect root path
            if path == "/":
                return RedirectResponse(url="/ui/setup", status_code=307)

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    settings = get_settings()
    logger.info(f"Starting Orca Slicer API v{__version__}")
    logger.info(f"Models path: {settings.models_path}")
    logger.info(f"Profiles path: {settings.profiles_path}")

    # Initialize job queue
    jobs_file = Path(settings.jobs_file)
    job_queue = init_job_queue(jobs_file)

    # Start queue worker as background task
    worker_task = asyncio.create_task(job_queue.process_queue())
    logger.info("Job queue worker started")

    yield

    # Shutdown: stop worker and wait for cleanup
    logger.info("Shutting down Orca Slicer API")
    job_queue.stop()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Orca Slicer API",
    description="Batch STL slicing service using OrcaSlicer",
    version=__version__,
    lifespan=lifespan,
)

# CORS middleware - allow all origins (internal tool)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup redirect middleware - redirect unconfigured app to setup wizard
app.add_middleware(SetupRedirectMiddleware)


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root to web UI."""
    return RedirectResponse(url="/ui")


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=__version__,
        models_path_accessible=Path(settings.models_path).exists(),
        profiles_path_accessible=Path(settings.profiles_path).exists(),
    )


# Import and include routers after app creation to avoid circular imports
from orca_api.routers import files, jobs, pipeline, profiles, scad, slice, ui

app.include_router(files.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(profiles.router, prefix="/api")
app.include_router(scad.router, prefix="/api")
app.include_router(slice.router, prefix="/api")
app.include_router(ui.router)
