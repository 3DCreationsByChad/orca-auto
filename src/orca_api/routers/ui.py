"""Web UI routes for file browsing and slicing."""

from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from orca_api.services.file_browser import list_directory
from orca_api.services.slicer import (
    get_available_profiles,
    get_profile_names,
    get_machine_profiles,
    get_filament_profiles,
    get_compatible_process_profiles,
    get_compatible_filament_profiles,
)
from orca_api.services.profile_import import list_imported_profiles
from orca_api.services.job_queue import get_job_queue
from orca_api.services.config_writer import (
    validate_path,
    write_env_config,
    clear_settings_cache,
)
from orca_api.services.scad_parser import (
    get_scad_files,
    parse_scad_file,
    apply_params,
)
from orca_api.services.scad_exporter import export_scad_to_stl
from orca_api.services.pipeline import (
    run_pipeline,
    get_pipeline_job,
    list_pipeline_jobs,
)
from orca_api.config import get_settings

router = APIRouter(prefix="/ui", tags=["ui"])

# Templates relative to working directory
templates = Jinja2Templates(directory="src/orca_api/templates")


@router.get("/setup", response_class=HTMLResponse)
async def setup_wizard(request: Request):
    """Setup wizard multi-step page.

    Displays a multi-step wizard that guides users through initial configuration:
    1. Path configuration (models, output, OrcaSlicer binary)
    2. Profile import (.orca_printer bundles)
    3. Completion summary

    This route is accessible even when the app is configured, allowing users
    to re-run setup later if needed.
    """
    return templates.TemplateResponse("setup.html", {"request": request})


@router.get("/setup/step/paths", response_class=HTMLResponse)
async def setup_step_paths(request: Request):
    """HTMX endpoint for path configuration step.

    Returns the paths form partial with current settings pre-filled.
    """
    settings = get_settings()
    return templates.TemplateResponse(
        "partials/setup_step_paths.html",
        {
            "request": request,
            "models_path": settings.models_path,
            "sliced_path": settings.sliced_path,
            "orcaslicer_bin": settings.orcaslicer_bin,
            "errors": None,
        }
    )


@router.post("/setup/validate-paths", response_class=HTMLResponse)
async def setup_validate_paths(
    request: Request,
    models_path: str = Form(...),
    sliced_path: str = Form(...),
    orcaslicer_bin: str = Form(...),
):
    """HTMX endpoint to validate paths and save to .env.

    Validates all three paths:
    - models_path must exist as a directory
    - sliced_path must exist as a directory
    - orcaslicer_bin must exist as a file

    On success, writes config to .env and returns profiles step.
    On failure, returns paths step with error messages.
    """
    errors = {}

    # Validate models_path
    valid, error = validate_path(models_path, must_be_dir=True)
    if not valid:
        errors["models_path"] = error

    # Validate sliced_path
    valid, error = validate_path(sliced_path, must_be_dir=True)
    if not valid:
        errors["sliced_path"] = error

    # Validate orcaslicer_bin
    valid, error = validate_path(orcaslicer_bin, must_be_dir=False)
    if not valid:
        errors["orcaslicer_bin"] = error

    # If any errors, return paths step with errors
    if errors:
        return templates.TemplateResponse(
            "partials/setup_step_paths.html",
            {
                "request": request,
                "models_path": models_path,
                "sliced_path": sliced_path,
                "orcaslicer_bin": orcaslicer_bin,
                "errors": errors,
            }
        )

    # All valid - write to .env file
    success = write_env_config(models_path, sliced_path, orcaslicer_bin)

    if not success:
        errors["general"] = "Failed to write configuration. Check file permissions."
        return templates.TemplateResponse(
            "partials/setup_step_paths.html",
            {
                "request": request,
                "models_path": models_path,
                "sliced_path": sliced_path,
                "orcaslicer_bin": orcaslicer_bin,
                "errors": errors,
            }
        )

    # Clear settings cache so next request uses new values
    clear_settings_cache()

    # Return profiles step
    profiles = list_imported_profiles()
    machine_profiles = [p for p in profiles if p.get("type") == "machine"]
    return templates.TemplateResponse(
        "partials/setup_step_profiles.html",
        {
            "request": request,
            "profiles": profiles,
            "profile_count": len(machine_profiles),
        }
    )


@router.get("/setup/step/profiles", response_class=HTMLResponse)
async def setup_step_profiles(request: Request):
    """HTMX endpoint for profile import step.

    Returns the profiles import partial with current profile list.
    """
    profiles = list_imported_profiles()
    machine_profiles = [p for p in profiles if p.get("type") == "machine"]
    return templates.TemplateResponse(
        "partials/setup_step_profiles.html",
        {
            "request": request,
            "profiles": profiles,
            "profile_count": len(machine_profiles),
        }
    )


@router.get("/setup/step/complete", response_class=HTMLResponse)
async def setup_step_complete(request: Request):
    """HTMX endpoint for completion step.

    Returns the completion summary with configured values.
    """
    settings = get_settings()
    profiles = list_imported_profiles()
    machine_profiles = [p for p in profiles if p.get("type") == "machine"]

    return templates.TemplateResponse(
        "partials/setup_complete.html",
        {
            "request": request,
            "models_path": settings.models_path,
            "sliced_path": settings.sliced_path,
            "orcaslicer_bin": settings.orcaslicer_bin,
            "profile_count": len(machine_profiles),
        }
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main file browser page."""
    files = list_directory("")
    machines = get_machine_profiles()
    # Get profiles compatible with first machine (default selection)
    default_machine = machines[0]["name"] if machines else None
    profiles = get_compatible_process_profiles(default_machine) if default_machine else []
    filaments = get_compatible_filament_profiles(default_machine) if default_machine else []
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "files": files,
            "current_path": "",
            "profiles": profiles,
            "machines": machines,
            "filaments": filaments,
        }
    )


@router.get("/browse/{path:path}", response_class=HTMLResponse)
async def browse_path(request: Request, path: str):
    """
    HTMX endpoint for folder navigation.
    Returns partial file_list.html for in-place updates.
    """
    files = list_directory(path)
    machines = get_machine_profiles()
    # Get profiles compatible with first machine (default selection)
    default_machine = machines[0]["name"] if machines else None
    profiles = get_compatible_process_profiles(default_machine) if default_machine else []
    filaments = get_compatible_filament_profiles(default_machine) if default_machine else []
    return templates.TemplateResponse(
        "partials/file_list.html",
        {
            "request": request,
            "files": files,
            "current_path": path,
            "profiles": profiles,
            "machines": machines,
            "filaments": filaments,
        }
    )


@router.get("/profiles/process/{machine_name:path}", response_class=HTMLResponse)
async def get_process_profiles_for_machine(request: Request, machine_name: str):
    """HTMX endpoint for process profiles filtered by machine.
    
    Returns option elements for a select dropdown.
    """
    profiles = get_compatible_process_profiles(machine_name)
    options = []
    for p in profiles:
        suffix = " *" if p.get("source") == "imported" else ""
        display_name = p["name"][:25] + "..." if len(p["name"]) > 25 else p["name"]
        options.append(f'<option value="{p["name"]}">{display_name}{suffix}</option>')
    return HTMLResponse("\n".join(options))


@router.get("/profiles/filament/{machine_name:path}", response_class=HTMLResponse)
async def get_filament_profiles_for_machine(request: Request, machine_name: str):
    """HTMX endpoint for filament profiles filtered by machine.
    
    Returns option elements for a select dropdown.
    """
    filaments = get_compatible_filament_profiles(machine_name)
    options = []
    for f in filaments:
        suffix = " *" if f.get("source") == "imported" else ""
        display_name = f["name"][:25] + "..." if len(f["name"]) > 25 else f["name"]
        options.append(f'<option value="{f["name"]}">{display_name}{suffix}</option>')
    return HTMLResponse("\n".join(options))


@router.get("/jobs", response_class=HTMLResponse)
async def get_jobs_html(request: Request):
    """HTMX endpoint for job list polling."""
    queue = get_job_queue()
    jobs = queue.list_jobs(limit=10)
    return templates.TemplateResponse(
        "partials/job_list.html",
        {
            "request": request,
            "jobs": jobs,
        }
    )


@router.get("/profiles", response_class=HTMLResponse)
async def get_imported_profiles_html(request: Request):
    """HTMX endpoint for imported profiles list."""
    profiles = list_imported_profiles()
    return templates.TemplateResponse(
        "partials/imported_profiles.html",
        {
            "request": request,
            "profiles": profiles,
        }
    )


@router.post("/slice", response_class=HTMLResponse)
async def slice_single_html(
    request: Request,
    file_path: str = Form(...),
    profile: str = Form(...),
    copies: int = Form(default=1),
    machine: Optional[str] = Form(default=None),
    filament: Optional[str] = Form(default=None)
):
    """Handle single file slice request and return updated job list HTML."""
    settings = get_settings()
    queue = get_job_queue()

    # Validate profile - use get_profile_names for simple name lookup
    available = get_profile_names()
    if profile not in available:
        jobs = queue.list_jobs(limit=10)
        return templates.TemplateResponse(
            "partials/job_list.html",
            {"request": request, "jobs": jobs, "error": f"Invalid profile: {profile}"}
        )

    # Validate copies range
    if copies < 1:
        copies = 1
    elif copies > 99:
        copies = 99

    # Create job with copies, machine, and filament
    full_path = Path(settings.models_path) / file_path
    if full_path.exists():
        queue.create_job(file_path, profile, copies, machine, filament)

    # Return updated job list
    jobs = queue.list_jobs(limit=10)
    return templates.TemplateResponse(
        "partials/job_list.html",
        {"request": request, "jobs": jobs}
    )


@router.post("/slice-batch", response_class=HTMLResponse)
async def slice_batch_html(
    request: Request,
    profile: str = Form(...),
    files: List[str] = Form(...),
    copies: int = Form(default=1),
    machine: Optional[str] = Form(default=None),
    filament: Optional[str] = Form(default=None)
):
    """Handle batch slice request and return updated job list HTML."""
    settings = get_settings()
    queue = get_job_queue()

    # Validate profile - use get_profile_names for simple name lookup
    available = get_profile_names()
    if profile not in available:
        jobs = queue.list_jobs(limit=10)
        return templates.TemplateResponse(
            "partials/job_list.html",
            {"request": request, "jobs": jobs, "error": f"Invalid profile: {profile}"}
        )

    # Validate copies range
    if copies < 1:
        copies = 1
    elif copies > 99:
        copies = 99

    # Create job for each file with copies, machine, and filament
    created_count = 0
    for file_path in files:
        full_path = Path(settings.models_path) / file_path
        if full_path.exists():
            queue.create_job(file_path, profile, copies, machine, filament)
            created_count += 1

    # Return updated job list
    jobs = queue.list_jobs(limit=10)
    return templates.TemplateResponse(
        "partials/job_list.html",
        {"request": request, "jobs": jobs, "message": f"Queued {created_count} jobs"}
    )


# ============================================================================
# SCAD Editor Routes
# ============================================================================


@router.get("/scad", response_class=HTMLResponse)
async def scad_editor(request: Request):
    """SCAD parameter editor page.

    Displays a two-column layout:
    - Left: List of .scad files in models directory
    - Right: Parameter editor for selected file
    """
    return templates.TemplateResponse("scad_editor.html", {"request": request})


@router.get("/scad/files", response_class=HTMLResponse)
async def scad_file_list(request: Request):
    """HTMX partial: list of SCAD files.

    Returns the file list partial with all .scad files found in models_path.
    """
    scad_paths = get_scad_files()
    scad_files = [
        {"path": path, "name": Path(path).name}
        for path in scad_paths
    ]
    return templates.TemplateResponse(
        "partials/scad_file_list.html",
        {"request": request, "scad_files": scad_files}
    )


@router.get("/scad/params/{path:path}", response_class=HTMLResponse)
async def scad_params(request: Request, path: str):
    """HTMX partial: parameter editor for a SCAD file.

    Parses the specified SCAD file and returns an editable form
    with inputs appropriate to each variable's type.

    Args:
        path: Relative path from models_path to the .scad file
    """
    settings = get_settings()
    full_path = Path(settings.models_path) / path

    if not full_path.exists():
        return templates.TemplateResponse(
            "partials/scad_params.html",
            {
                "request": request,
                "scad_file": None,
                "error_message": f"File not found: {path}",
            }
        )

    scad_file = parse_scad_file(full_path)
    return templates.TemplateResponse(
        "partials/scad_params.html",
        {
            "request": request,
            "scad_file": scad_file,
        }
    )


@router.post("/scad/params/{path:path}", response_class=HTMLResponse)
async def save_scad_params(request: Request, path: str):
    """HTMX endpoint: save SCAD file parameters.

    Parses form data, applies parameter changes, and saves the file.
    Returns the updated parameter editor partial with a success message.

    Args:
        path: Relative path from models_path to the .scad file
    """
    settings = get_settings()
    full_path = Path(settings.models_path) / path

    if not full_path.exists():
        return templates.TemplateResponse(
            "partials/scad_params.html",
            {
                "request": request,
                "scad_file": None,
                "error_message": f"File not found: {path}",
            }
        )

    # Parse current file to get variable types
    scad_file = parse_scad_file(full_path)

    # Get form data
    form_data = await request.form()

    # Build params dict with proper type conversion
    params = {}
    for var in scad_file.variables:
        if var.name in form_data:
            raw_value = form_data[var.name]
            params[var.name] = _convert_form_value(raw_value, var.type.value)
        elif var.type.value == "boolean":
            # Unchecked checkbox won't be in form data
            params[var.name] = False

    # Apply changes
    new_content = apply_params(scad_file, params)

    # Write back to file
    full_path.write_text(new_content, encoding="utf-8")

    # Re-parse to get updated state
    updated_file = parse_scad_file(full_path)

    return templates.TemplateResponse(
        "partials/scad_params.html",
        {
            "request": request,
            "scad_file": updated_file,
            "success_message": "Parameters saved successfully!",
        }
    )


@router.post("/scad/reset/{path:path}", response_class=HTMLResponse)
async def reset_scad_params(request: Request, path: str):
    """HTMX endpoint: reset SCAD file parameters to defaults.

    Restores all variables to their original default values and saves the file.
    Returns the updated parameter editor partial with a success message.

    Args:
        path: Relative path from models_path to the .scad file
    """
    settings = get_settings()
    full_path = Path(settings.models_path) / path

    if not full_path.exists():
        return templates.TemplateResponse(
            "partials/scad_params.html",
            {
                "request": request,
                "scad_file": None,
                "error_message": f"File not found: {path}",
            }
        )

    # Parse current file
    scad_file = parse_scad_file(full_path)

    # Build params dict with default values
    reset_params = {
        var.name: var.default_value
        for var in scad_file.variables
    }

    # Apply default values
    new_content = apply_params(scad_file, reset_params)

    # Write back to file
    full_path.write_text(new_content, encoding="utf-8")

    # Re-parse to get updated state
    updated_file = parse_scad_file(full_path)

    return templates.TemplateResponse(
        "partials/scad_params.html",
        {
            "request": request,
            "scad_file": updated_file,
            "success_message": "Parameters reset to defaults!",
        }
    )


@router.post("/scad/export/{path:path}", response_class=HTMLResponse)
async def export_scad_to_stl_ui(request: Request, path: str):
    """HTMX endpoint: export SCAD file to STL.

    Calls the OpenSCAD CLI to convert the SCAD file to STL format.
    Returns the parameter editor partial with success/error message.

    Args:
        path: Relative path from models_path to the .scad file
    """
    settings = get_settings()
    full_path = Path(settings.models_path) / path

    if not full_path.exists():
        return templates.TemplateResponse(
            "partials/scad_params.html",
            {
                "request": request,
                "scad_file": None,
                "error_message": f"File not found: {path}",
            }
        )

    # Export the SCAD file to STL
    result = await export_scad_to_stl(full_path, params=None)

    # Re-parse the file to show current state
    scad_file = parse_scad_file(full_path)

    if result.success:
        return templates.TemplateResponse(
            "partials/scad_params.html",
            {
                "request": request,
                "scad_file": scad_file,
                "success_message": f"Exported to: {result.output_path}",
            }
        )
    else:
        return templates.TemplateResponse(
            "partials/scad_params.html",
            {
                "request": request,
                "scad_file": scad_file,
                "error_message": f"Export failed: {result.error_message}",
            }
        )


def _convert_form_value(raw_value: str, var_type: str):
    """Convert form string value to appropriate Python type.

    Args:
        raw_value: The raw string value from the form
        var_type: The variable type (string, number, boolean, vector)

    Returns:
        Converted value in appropriate type
    """
    if var_type == "boolean":
        return raw_value.lower() in ("true", "1", "on", "yes")

    elif var_type == "number":
        try:
            if "." in raw_value:
                return float(raw_value)
            return int(raw_value)
        except ValueError:
            return 0

    elif var_type == "vector":
        try:
            # Parse comma-separated values
            parts = [p.strip() for p in raw_value.split(",")]
            return [float(p) for p in parts if p]
        except ValueError:
            return []

    # Default: string
    return raw_value


# ============================================================================
# Pipeline Routes (SCAD -> STL -> GCode workflow)
# ============================================================================


@router.get("/pipeline", response_class=HTMLResponse)
async def pipeline_page(request: Request):
    """Pipeline page - unified SCAD-to-printer workflow.

    Displays the pipeline interface:
    - SCAD file selector
    - Parameter editor
    - Profile/machine/filament selectors
    - Pipeline status display
    """
    # Get available SCAD files
    scad_paths = get_scad_files()
    scad_files = [
        {"path": path, "name": Path(path).name}
        for path in scad_paths
    ]

    # Get available machines, profiles, and filaments
    machines = get_machine_profiles()
    default_machine = machines[0]["name"] if machines else None
    profiles = get_compatible_process_profiles(default_machine) if default_machine else []
    filaments = get_compatible_filament_profiles(default_machine) if default_machine else []

    # Get recent pipeline jobs
    jobs = list_pipeline_jobs(limit=5)

    return templates.TemplateResponse(
        "pipeline.html",
        {
            "request": request,
            "scad_files": scad_files,
            "profiles": profiles,
            "machines": machines,
            "filaments": filaments,
            "jobs": jobs,
        }
    )


@router.get("/pipeline/form", response_class=HTMLResponse)
async def pipeline_form(request: Request, scad_path: Optional[str] = None):
    """HTMX partial: pipeline form with optional SCAD file pre-selected.

    Returns the pipeline form with SCAD file selector, parameter editor,
    and slice settings.

    Args:
        scad_path: Optional SCAD file path to pre-select
    """
    settings = get_settings()

    # Get available SCAD files
    scad_paths = get_scad_files()
    scad_files = [
        {"path": path, "name": Path(path).name}
        for path in scad_paths
    ]

    # Get available machines, profiles, and filaments
    machines = get_machine_profiles()
    default_machine = machines[0]["name"] if machines else None
    profiles = get_compatible_process_profiles(default_machine) if default_machine else []
    filaments = get_compatible_filament_profiles(default_machine) if default_machine else []

    # Parse SCAD file if selected
    scad_file = None
    if scad_path:
        full_path = Path(settings.models_path) / scad_path
        if full_path.exists():
            scad_file = parse_scad_file(full_path)

    return templates.TemplateResponse(
        "partials/pipeline_form.html",
        {
            "request": request,
            "scad_files": scad_files,
            "scad_file": scad_file,
            "selected_scad": scad_path,
            "profiles": profiles,
            "machines": machines,
            "filaments": filaments,
        }
    )


@router.get("/pipeline/params/{path:path}", response_class=HTMLResponse)
async def pipeline_params(request: Request, path: str):
    """HTMX partial: parameter editor for a SCAD file in pipeline context.

    Args:
        path: Relative path from models_path to the .scad file
    """
    settings = get_settings()
    full_path = Path(settings.models_path) / path

    if not full_path.exists():
        return HTMLResponse(
            '<div class="text-red-400 text-sm">File not found</div>'
        )

    scad_file = parse_scad_file(full_path)
    return templates.TemplateResponse(
        "partials/pipeline_params.html",
        {
            "request": request,
            "scad_file": scad_file,
        }
    )


@router.post("/pipeline/start", response_class=HTMLResponse)
async def start_pipeline(
    request: Request,
    scad_path: str = Form(...),
    profile: str = Form(...),
    machine: Optional[str] = Form(default=None),
    filament: Optional[str] = Form(default=None),
):
    """HTMX endpoint: start a pipeline job.

    Parses form data including SCAD parameters, starts the pipeline,
    and returns the status partial.
    """
    settings = get_settings()

    # Get form data for SCAD parameters
    form_data = await request.form()

    # Extract SCAD parameters (all form fields except control fields)
    control_fields = {"scad_path", "profile", "machine", "filament"}
    params = {}

    # Parse the SCAD file to get variable types
    full_path = Path(settings.models_path) / scad_path
    if full_path.exists():
        scad_file = parse_scad_file(full_path)
        var_types = {v.name: v.type.value for v in scad_file.variables}

        for key, value in form_data.items():
            if key not in control_fields and key in var_types:
                params[key] = _convert_form_value(value, var_types[key])

        # Handle unchecked checkboxes (booleans)
        for var in scad_file.variables:
            if var.type.value == "boolean" and var.name not in form_data:
                params[var.name] = False

    # Start the pipeline
    job = await run_pipeline(
        scad_path=scad_path,
        params=params,
        profile=profile,
        machine=machine,
        filament=filament,
    )

    return templates.TemplateResponse(
        "partials/pipeline_status.html",
        {
            "request": request,
            "job": job,
        }
    )


@router.get("/pipeline/status/{job_id}", response_class=HTMLResponse)
async def pipeline_status(request: Request, job_id: str):
    """HTMX partial: pipeline job status.

    Shows current stage with visual indicator and polls until terminal state.

    Args:
        job_id: Pipeline job UUID
    """
    job = get_pipeline_job(job_id)

    if job is None:
        return HTMLResponse(
            '<div class="text-red-400 text-sm">Job not found</div>'
        )

    return templates.TemplateResponse(
        "partials/pipeline_status.html",
        {
            "request": request,
            "job": job,
        }
    )


@router.get("/pipeline/jobs", response_class=HTMLResponse)
async def pipeline_jobs_list(request: Request):
    """HTMX partial: list of recent pipeline jobs."""
    jobs = list_pipeline_jobs(limit=5)
    return templates.TemplateResponse(
        "partials/pipeline_jobs.html",
        {
            "request": request,
            "jobs": jobs,
        }
    )
