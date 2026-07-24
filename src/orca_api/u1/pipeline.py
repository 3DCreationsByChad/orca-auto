"""End-to-end U1 job: STLs + tool assignments -> project .3mf -> G-code -> U1.

Composition only. Each concern lives in its own module:
`threemf_builder.build_u1_3mf` (assembly), `slice.slice_3mf` (slicing),
`moonraker_client.MoonrakerClient` (transport to the printer).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import httpx

from orca_api.u1.moonraker_client import MoonrakerClient
from orca_api.u1.slice import DEFAULT_DATADIR, slice_3mf
from orca_api.u1.threemf_builder import U1Part, build_u1_3mf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class U1JobResult:
    """Outcome of a U1 job."""

    project_3mf: Path
    gcode_path: Path
    pushed_as: str | None = None


def build_and_slice(
    parts: Sequence[U1Part],
    workdir: Path | str,
    *,
    datadir: Path | str = DEFAULT_DATADIR,
    **slice_kwargs: object,
) -> U1JobResult:
    """Build the multicolor project .3mf and slice it to G-code (no push).

    Args:
        parts: STL + tool assignment pairs.
        workdir: Directory for `job.3mf` and `job.gcode` (created if missing).
        datadir: OrcaSlicer data dir (passed through to `slice_3mf`).
        **slice_kwargs: Forwarded to `slice_3mf` (orcaslicer_bin, display, etc.).
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    project = build_u1_3mf(list(parts), workdir / "job.3mf")
    result = slice_3mf(project, workdir / "job.gcode", datadir=datadir, **slice_kwargs)
    return U1JobResult(project_3mf=Path(project), gcode_path=result.gcode_path)


async def build_slice_push(
    parts: Sequence[U1Part],
    workdir: Path | str,
    *,
    moonraker_url: str,
    api_key: str | None = None,
    mode: str = "queue",
    datadir: Path | str = DEFAULT_DATADIR,
    transport: httpx.BaseTransport | None = None,
    **slice_kwargs: object,
) -> U1JobResult:
    """Build + slice, then upload the G-code to the U1 via Moonraker.

    Args:
        moonraker_url: Base URL of the U1's Moonraker instance.
        api_key: Optional Moonraker API key.
        mode: "queue" (upload + enqueue) or "start" (upload + print now).
        transport: Optional httpx transport (inject MockTransport in tests).
    """
    job = build_and_slice(parts, workdir, datadir=datadir, **slice_kwargs)
    async with MoonrakerClient(moonraker_url, api_key=api_key, transport=transport) as mc:
        pushed = await mc.push(str(job.gcode_path), mode=mode)
    logger.info("pushed %s to %s as %s (mode=%s)", job.gcode_path, moonraker_url, pushed, mode)
    return U1JobResult(project_3mf=job.project_3mf, gcode_path=job.gcode_path, pushed_as=pushed)
