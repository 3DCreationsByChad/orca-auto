"""Render a job spec's `.scad` parts to STL, so a job runs from source.

Why this exists: `u1 slice` used to start at STL, which left one manual
`openscad -o` in front of every job. That step is slow, easy to forget, and when
forgotten it prints the *previous* design from a stale STL without complaining.

Two things here are not incidental:

- **Staleness is judged against the whole include graph, not the named file.**
  A card's entry file is often eight lines; all the geometry lives in the files
  it `include`s. Comparing mtimes against only the named `.scad` happily reuses
  an STL that predates the edit you just made.
- **An empty render is treated as a failure.** OpenSCAD exits 0 when a top-level
  object turns out not to be 3D (a bad `include` path does this), leaving a
  valid STL with no facets — which slices, prints, and produces nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from orca_api.services.scad_exporter import _format_param_value

logger = logging.getLogger(__name__)

__all__ = [
    "ScadRenderError",
    "scad_dependencies",
    "output_name",
    "is_stale",
    "render_scad",
]

DEFAULT_TIMEOUT = 900.0  # CGAL on a full plate of text/QR geometry takes minutes
DEFAULT_DISPLAY = ":99"

#: `include <foo.scad>` / `use <foo.scad>` — OpenSCAD resolves both relative to
#: the file doing the including.
_INCLUDE_RE = re.compile(r"^\s*(?:include|use)\s*<([^>]+)>", re.MULTILINE)

_MAX_DEPTH = 32


class ScadRenderError(RuntimeError):
    """Raised when a `.scad` part cannot be rendered to usable geometry."""


def scad_dependencies(scad_path: Path | str) -> list[Path]:
    """Every file the render depends on: the entry file plus its include graph.

    Missing includes are skipped rather than raised on — OpenSCAD resolves some
    paths from its own library directories, and a wrong guess here should not
    stop a render that would otherwise work. The render itself fails loudly if
    the include really was broken.
    """
    entry = Path(scad_path).resolve()
    seen: list[Path] = []
    queue = [(entry, 0)]
    while queue:
        current, depth = queue.pop(0)
        if current in seen or depth > _MAX_DEPTH:
            continue
        seen.append(current)
        try:
            text = current.read_text()
        except OSError:
            continue
        for ref in _INCLUDE_RE.findall(text):
            candidate = (current.parent / ref).resolve()
            if candidate.is_file():
                queue.append((candidate, depth + 1))
    return seen


def output_name(scad_path: Path | str, params: dict[str, Any] | None) -> str:
    """STL filename for a `.scad` + params pair.

    Params are part of the name so two variants of the same source (face-up and
    face-down, say) never overwrite each other's cache.
    """
    stem = Path(scad_path).stem
    if not params:
        return f"{stem}.stl"
    digest = hashlib.sha1(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:8]
    return f"{stem}-{digest}.stl"


def is_stale(out_path: Path | str, deps: list[Path]) -> bool:
    """True if `out_path` is missing or older than anything it was built from."""
    out = Path(out_path)
    if not out.is_file() or out.stat().st_size == 0:
        return True
    out_mtime = out.stat().st_mtime
    return any(d.is_file() and d.stat().st_mtime > out_mtime for d in deps)


def _facet_count(stl_path: Path) -> int:
    """Facets in an STL, binary or ASCII. 0 means OpenSCAD produced nothing."""
    data = stl_path.read_bytes()
    if data[:5].lower().lstrip() == b"solid" and b"facet" in data[:4096]:
        return data.count(b"facet normal")
    if len(data) < 84:
        return 0
    return int.from_bytes(data[80:84], "little")


def render_scad(
    scad_path: Path | str,
    out_path: Path | str,
    *,
    params: dict[str, Any] | None = None,
    openscad_bin: str = "openscad",
    display: str | None = DEFAULT_DISPLAY,
    timeout: float = DEFAULT_TIMEOUT,
    force: bool = False,
) -> Path:
    """Render `scad_path` to `out_path`, skipping the work if it is up to date.

    Args:
        params: OpenSCAD variable overrides, passed as `-D name=value`.
        force: Re-render even when the cached STL is newer than its sources.

    Raises:
        ScadRenderError: OpenSCAD is missing, fails, times out, or emits an STL
            with no facets.
    """
    scad = Path(scad_path)
    out = Path(out_path)
    if not scad.is_file():
        raise ScadRenderError(f"SCAD file not found: {scad}")

    deps = scad_dependencies(scad)
    if not force and not is_stale(out, deps):
        logger.info("scad up to date, reusing %s", out)
        return out

    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [openscad_bin, "-o", str(out), "--export-format", "binstl"]
    for name, value in (params or {}).items():
        cmd.extend(["-D", f"{name}={_format_param_value(value)}"])
    cmd.append(str(scad))

    # Inherit the environment rather than replacing it: OpenSCAD needs fontconfig
    # (and therefore HOME/XDG_*) to resolve a font by name. Strip that and text()
    # silently renders as nothing.
    env = dict(os.environ)
    if display:
        env["DISPLAY"] = display

    logger.info("rendering %s -> %s", scad, out)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError as exc:
        raise ScadRenderError(f"OpenSCAD binary not found: {openscad_bin!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScadRenderError(
            f"OpenSCAD timed out after {timeout}s rendering {scad}"
        ) from exc

    if proc.returncode != 0 or not out.is_file():
        raise ScadRenderError(
            f"OpenSCAD failed (rc={proc.returncode}) for {scad}\n"
            f"--- stderr ---\n{proc.stderr[-2000:]}"
        )

    facets = _facet_count(out)
    if facets == 0:
        out.unlink(missing_ok=True)
        raise ScadRenderError(
            f"OpenSCAD produced empty geometry for {scad} — nothing would print. "
            f"A broken include or a 2D top-level object does this while still "
            f"exiting 0.\n--- stderr ---\n{proc.stderr[-2000:]}"
        )
    logger.info("rendered %s (%d facets)", out, facets)
    return out
