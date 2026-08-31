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

from orca_api.u1.loaded_filament import LoadedFilament
from orca_api.u1.moonraker_client import MoonrakerClient
from orca_api.u1.naming import FALLBACK_STEM, safe_stem
from orca_api.u1.slice import DEFAULT_DATADIR, slice_3mf
from orca_api.u1.support import SupportSpec
from orca_api.u1.thumbnail import PreviewError, inject_thumbnails, render_thumbnails
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
    name: str = FALLBACK_STEM,
    datadir: Path | str = DEFAULT_DATADIR,
    loaded: Sequence[LoadedFilament | None] | None = None,
    support: SupportSpec | None = None,
    assembly: bool = False,
    process: dict | None = None,
    extra_filaments: dict[int, str] | None = None,
    thumbnails: bool = True,
    **slice_kwargs: object,
) -> U1JobResult:
    """Build the multicolor project .3mf and slice it to G-code (no push).

    Args:
        parts: STL + tool assignment pairs.
        workdir: Directory for `<name>.3mf` and `<name>.gcode` (created if
            missing).
        name: Stem for the produced files, and therefore the name the job shows
            under on the printer's screen. Sanitised; unusable names fall back
            to "job".
        datadir: OrcaSlicer data dir (passed through to `slice_3mf`).
        assembly: STLs are parts of one model sharing an origin (see
            `build_u1_3mf`).
        process: Project-config overrides, e.g. `{"wall_generator": "arachne"}`.
        thumbnails: Render a preview and embed it, so the U1 shows the model
            rather than a blank tile. Never fatal -- a job that slices should
            print.
        **slice_kwargs: Forwarded to `slice_3mf` (orcaslicer_bin, display, etc.).
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(name)
    project = build_u1_3mf(list(parts), workdir / f"{stem}.3mf", datadir, loaded=loaded,
                           support=support, assembly=assembly, process=process,
                           extra_filaments=extra_filaments)
    result = slice_3mf(project, workdir / f"{stem}.gcode", datadir=datadir, **slice_kwargs)
    if thumbnails:
        _embed_preview(parts, workdir, result.gcode_path, slice_kwargs.get("display"))
    return U1JobResult(project_3mf=Path(project), gcode_path=result.gcode_path)


def _embed_preview(
    parts: Sequence[U1Part], workdir: Path, gcode: Path, display: object
) -> None:
    """Render and inject the on-screen preview, downgrading failure to a warning.

    A missing thumbnail costs the user a picture on a screen. A raised exception
    costs them the print, after the slice has already been paid for.
    """
    kwargs: dict[str, object] = {}
    if isinstance(display, str):
        kwargs["display"] = display
    try:
        images = render_thumbnails([p.stl_path for p in parts], workdir, **kwargs)
        inject_thumbnails(gcode, images)
    except (PreviewError, OSError) as exc:
        logger.warning("no preview embedded in %s: %s", gcode, exc)


async def build_slice_push(
    parts: Sequence[U1Part],
    workdir: Path | str,
    *,
    moonraker_url: str,
    api_key: str | None = None,
    mode: str = "queue",
    name: str = FALLBACK_STEM,
    datadir: Path | str = DEFAULT_DATADIR,
    read_filament: bool = True,
    loaded: Sequence[LoadedFilament | None] | None = None,
    support: SupportSpec | None = None,
    assembly: bool = False,
    process: dict | None = None,
    extra_filaments: dict[int, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    **slice_kwargs: object,
) -> U1JobResult:
    """Build + slice, then upload the G-code to the U1 via Moonraker.

    Args:
        moonraker_url: Base URL of the U1's Moonraker instance.
        api_key: Optional Moonraker API key.
        mode: "queue" (upload + enqueue) or "start" (upload + print now).
        name: Stem the job uploads under, i.e. what the U1's file list shows.
        transport: Optional httpx transport (inject MockTransport in tests).
    """
    async with MoonrakerClient(moonraker_url, api_key=api_key, transport=transport) as mc:
        if loaded is None and read_filament:
            # ask the printer what is actually loaded *before* slicing -- afterwards
            # the temperatures are already baked into the G-code
            loaded = await mc.get_loaded_filaments()
            for spool in loaded:
                if spool is not None:
                    logger.info("tool %d holds %s (%s)", spool.tool_index + 1,
                                spool.label, spool.color)
        job = build_and_slice(parts, workdir, name=name, datadir=datadir, loaded=loaded,
                              support=support, assembly=assembly, process=process,
                              extra_filaments=extra_filaments, **slice_kwargs)
        pushed = await mc.push(str(job.gcode_path), mode=mode)
    logger.info("pushed %s to %s as %s (mode=%s)", job.gcode_path, moonraker_url, pushed, mode)
    return U1JobResult(project_3mf=job.project_3mf, gcode_path=job.gcode_path, pushed_as=pushed)
