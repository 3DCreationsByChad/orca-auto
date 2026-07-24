"""Headless OrcaSlicer coordinator: slice a project .3mf into G-code for the U1.

Implements the proven headless recipe from
`docs/headless-slicing-findings-2026-06-11.md`: slice a *project* `.3mf` (whose
embedded `project_settings.config` bypasses OrcaSlicer's vendor-bundle
compatibility check) with `--datadir`, then extract the embedded G-code from the
exported `.3mf`. This is the fix for orca-auto's systemically broken
standalone-profile slicing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DATADIR = Path.home() / ".config" / "OrcaSlicer"
DEFAULT_BIN = "orcaslicer"
DEFAULT_DISPLAY = ":99"
DEFAULT_TIMEOUT = 300.0


class SliceError(RuntimeError):
    """Raised when OrcaSlicer fails to produce a sliced output."""


@dataclass(frozen=True)
class SliceResult:
    """Result of a successful slice."""

    gcode_path: Path
    output_3mf: Path  # the full sliced project .3mf, kept alongside the gcode


def _build_command(
    in_3mf: Path,
    out_3mf: Path,
    datadir: Path,
    orcaslicer_bin: str,
    allow_newer: bool,
) -> list[str]:
    """Assemble the OrcaSlicer CLI argv for a project-.3mf slice.

    The input project .3mf MUST be the positional final argument.
    """
    cmd = [
        orcaslicer_bin,
        "--datadir",
        str(datadir),
        "--arrange",
        "1",
        "--slice",
        "0",
        "--export-3mf",
        str(out_3mf),
    ]
    if allow_newer:
        cmd.append("--allow-newer-file")
    cmd.append(str(in_3mf))
    return cmd


def _extract_gcode(sliced_3mf: Path, out_gcode: Path) -> Path:
    """Extract the first embedded `.gcode` entry from a sliced project .3mf."""
    with zipfile.ZipFile(sliced_3mf) as z:
        gcode_names = [n for n in z.namelist() if n.endswith(".gcode")]
        if not gcode_names:
            raise SliceError(
                f"no .gcode entry found in sliced output {sliced_3mf} "
                f"(entries: {z.namelist()})"
            )
        data = z.read(gcode_names[0])
    out_gcode.parent.mkdir(parents=True, exist_ok=True)
    out_gcode.write_bytes(data)
    return out_gcode


def slice_3mf(
    in_3mf: Path | str,
    out_gcode: Path | str,
    *,
    datadir: Path | str = DEFAULT_DATADIR,
    orcaslicer_bin: str = DEFAULT_BIN,
    display: str | None = DEFAULT_DISPLAY,
    allow_newer: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> SliceResult:
    """Slice a project `.3mf` into G-code headlessly.

    Args:
        in_3mf: Path to a project .3mf with a complete embedded config
            (e.g. produced by `threemf_builder.build_u1_3mf`).
        out_gcode: Destination path for the extracted G-code.
        datadir: OrcaSlicer data dir holding the system bundle (required so the
            CLI can resolve `inherits`). On VM 111 this is ~/.config/OrcaSlicer.
        orcaslicer_bin: OrcaSlicer executable name or path.
        display: X display for the headless slice (Xvfb, e.g. ":99"); set as
            DISPLAY in the child env. Pass None to inherit the current DISPLAY.
        allow_newer: Pass `--allow-newer-file` (tolerate a newer 3mf stamp).
        timeout: Seconds before the slice is killed.

    Returns:
        SliceResult with the extracted gcode path and the kept sliced .3mf.

    Raises:
        FileNotFoundError: if `in_3mf` does not exist.
        SliceError: if OrcaSlicer exits non-zero, writes no output, or the
            output contains no .gcode entry.
    """
    in_3mf = Path(in_3mf)
    out_gcode = Path(out_gcode)
    datadir = Path(datadir)
    if not in_3mf.is_file():
        raise FileNotFoundError(f"input .3mf not found: {in_3mf}")

    run_env = dict(os.environ)
    if display:
        run_env["DISPLAY"] = display

    with tempfile.TemporaryDirectory(prefix="u1-slice-") as td:
        out_3mf = Path(td) / "sliced.3mf"
        cmd = _build_command(in_3mf, out_3mf, datadir, orcaslicer_bin, allow_newer)
        logger.info("slicing: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
            )
        except FileNotFoundError as exc:  # orcaslicer_bin not on PATH
            raise SliceError(f"OrcaSlicer binary not found: {orcaslicer_bin!r}") from exc
        except subprocess.TimeoutExpired as exc:  # OrcaSlicer hung (e.g. degenerate mesh)
            raise SliceError(
                f"OrcaSlicer timed out after {timeout}s for {in_3mf}"
            ) from exc

        if proc.returncode != 0 or not out_3mf.is_file():
            raise SliceError(
                f"OrcaSlicer failed (rc={proc.returncode}) for {in_3mf}\n"
                f"--- stdout ---\n{proc.stdout[-2000:]}\n"
                f"--- stderr ---\n{proc.stderr[-2000:]}"
            )

        _extract_gcode(out_3mf, out_gcode)
        kept_3mf = out_gcode.with_suffix(".sliced.3mf")
        shutil.copy2(out_3mf, kept_3mf)

    return SliceResult(gcode_path=out_gcode, output_3mf=kept_3mf)
