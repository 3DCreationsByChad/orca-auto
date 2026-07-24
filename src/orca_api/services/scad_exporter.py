"""OpenSCAD CLI wrapper service for exporting SCAD files to STL."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from orca_api.config import get_settings
from orca_api.models import ExportResult, ScadVariableType

logger = logging.getLogger(__name__)

# Export timeout in seconds (2 minutes - SCAD exports are typically faster than slicing)
EXPORT_TIMEOUT = 120


def _format_param_value(value: Any, var_type: ScadVariableType | None = None) -> str:
    """Format a parameter value for OpenSCAD -D argument.

    Args:
        value: The Python value to format
        var_type: Optional type hint for formatting

    Returns:
        Formatted string for OpenSCAD -D argument
    """
    # String values need to be wrapped in double quotes
    if isinstance(value, str):
        # Escape any quotes in the string
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'

    # Boolean
    if isinstance(value, bool):
        return 'true' if value else 'false'

    # List/vector
    if isinstance(value, (list, tuple)):
        elements = ', '.join(str(v) for v in value)
        return f'[{elements}]'

    # Number or other
    return str(value)


async def export_scad_to_stl(
    scad_path: Path,
    params: dict[str, Any] | None = None
) -> ExportResult:
    """Export an OpenSCAD file to STL using OpenSCAD CLI.

    The output STL file is placed in the same directory as the input SCAD file,
    with the .scad extension replaced by .stl.

    Args:
        scad_path: Path to input .scad file
        params: Optional dict of parameter overrides {var_name: value}

    Returns:
        ExportResult with success status, output path, and logs
    """
    settings = get_settings()
    start_time = time.time()

    # Validate input file exists
    if not scad_path.exists():
        return ExportResult(
            success=False,
            output_path=None,
            stdout="",
            stderr="",
            duration_seconds=0,
            error_message=f"SCAD file not found: {scad_path}"
        )

    # Compute output path (same directory, .stl extension)
    output_path = scad_path.with_suffix('.stl')

    # Build command
    cmd = [
        settings.openscad_bin,
        "-o", str(output_path),
        "--export-format", "binstl",
        "-q",  # Quiet mode
    ]

    # Add parameter overrides via -D flags
    if params:
        for var_name, value in params.items():
            formatted_value = _format_param_value(value)
            # Format: -D 'varname=value'
            cmd.extend(["-D", f"{var_name}={formatted_value}"])

    # Add input file
    cmd.append(str(scad_path))

    logger.info(f"Exporting SCAD to STL: {scad_path} -> {output_path}")
    logger.debug(f"Command: {' '.join(cmd)}")

    try:
        # Run with DISPLAY for headless X11 (some OpenSCAD builds need this)
        env = {"DISPLAY": ":99"}

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**env}
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=EXPORT_TIMEOUT
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            duration = time.time() - start_time
            return ExportResult(
                success=False,
                output_path=None,
                stdout="",
                stderr="",
                duration_seconds=duration,
                error_message=f"Export timed out after {EXPORT_TIMEOUT} seconds"
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        duration = time.time() - start_time

        # Check if output was created
        if output_path.exists():
            logger.info(f"Export successful: {output_path} ({duration:.1f}s)")
            return ExportResult(
                success=True,
                output_path=str(output_path),
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                error_message=None
            )
        else:
            logger.error(f"Export failed: output not created")
            # Include stderr in error message for debugging
            error_msg = "Export completed but output file was not created"
            if stderr.strip():
                error_msg += f": {stderr.strip()}"
            return ExportResult(
                success=False,
                output_path=None,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                error_message=error_msg
            )

    except FileNotFoundError:
        duration = time.time() - start_time
        logger.error(f"OpenSCAD not found at: {settings.openscad_bin}")
        return ExportResult(
            success=False,
            output_path=None,
            stdout="",
            stderr="",
            duration_seconds=duration,
            error_message=f"OpenSCAD not found at: {settings.openscad_bin}. Install OpenSCAD or set ORCA_OPENSCAD_BIN."
        )

    except Exception as e:
        duration = time.time() - start_time
        logger.exception(f"Export error: {e}")
        return ExportResult(
            success=False,
            output_path=None,
            stdout="",
            stderr="",
            duration_seconds=duration,
            error_message=str(e)
        )
