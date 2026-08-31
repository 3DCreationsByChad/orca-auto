"""Preview thumbnails for the U1's screen, rendered here and spliced in.

Why this exists: the U1 reads its on-screen preview from a thumbnail embedded in
the G-code. OrcaSlicer's *CLI* never emits one -- measured, not assumed: the
project config carries `thumbnails = 48x48/PNG, 300x300/PNG` all the way into
the sliced output, the exported `.3mf` contains no image entry of any kind, and
Moonraker reports `thumbnails: None` for every file we have ever pushed. There
is no GUI renderer in the headless path to draw it.

So we draw it. OpenSCAD is already a hard dependency of the job pipeline (it
renders `.scad` parts to STL under the same headless display), which makes it
the cheapest renderer available -- no new dependency for a cosmetic feature.

The byte-level shape below was read off a Snapmaker-Orca-sliced file pulled from
the printer, i.e. one whose preview the U1 is known to display:

    ; THUMBNAIL_BLOCK_START
    <blank>
    ;
    ; thumbnail begin 48x48 1132
    ; <base64, 78 chars per line>
    ; thumbnail end
    ; THUMBNAIL_BLOCK_END

⚠️ The declared number is the length of the **base64**, not the PNG's byte
count (1132 for an 847-byte image). Declaring the raw size yields a blank
screen and no error anywhere.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "PreviewError",
    "DEFAULT_SIZES",
    "thumbnail_block",
    "inject_thumbnails",
    "render_preview",
    "render_thumbnails",
]

#: Matches what Snapmaker Orca embeds. The small one is what the file list
#: draws; the large one is the job-detail screen.
DEFAULT_SIZES: tuple[int, ...] = (48, 300)

DEFAULT_DISPLAY = ":99"
DEFAULT_TIMEOUT = 120.0
DEFAULT_BIN = "openscad"

_WRAP = 78
_BEGIN = "; thumbnail begin"
_BLOCK_START = "; THUMBNAIL_BLOCK_START"
_BLOCK_END = "; THUMBNAIL_BLOCK_END"
_HEADER_END = "; HEADER_BLOCK_END"


class PreviewError(RuntimeError):
    """Raised when a preview image cannot be rendered."""


def thumbnail_block(png: bytes, width: int, height: int) -> str:
    """One `THUMBNAIL_BLOCK` in the exact shape the U1 accepts."""
    b64 = base64.b64encode(png).decode("ascii")
    lines = [_BLOCK_START, "", ";", f"{_BEGIN} {width}x{height} {len(b64)}"]
    lines += [f"; {b64[i:i + _WRAP]}" for i in range(0, len(b64), _WRAP)]
    lines += ["; thumbnail end", _BLOCK_END]
    return "\n".join(lines)


def inject_thumbnails(
    gcode_path: Path | str, images: Sequence[tuple[bytes, int, int]]
) -> Path:
    """Splice thumbnail blocks into a sliced G-code file, in place.

    Placed directly after `; HEADER_BLOCK_END` where the slicer would have put
    them, or at the top of the file when there is no header block. Re-running a
    job replaces any blocks already present rather than stacking them.
    """
    gcode_path = Path(gcode_path)
    if not images:
        return gcode_path

    lines = gcode_path.read_text(errors="replace").split("\n")
    lines = _strip_existing(lines)

    blocks = "\n".join(thumbnail_block(png, w, h) for png, w, h in images)
    try:
        at = lines.index(_HEADER_END) + 1
    except ValueError:
        at = 0

    merged = lines[:at] + ["", *blocks.split("\n"), ""] + lines[at:]
    gcode_path.write_text("\n".join(merged))
    return gcode_path


def _strip_existing(lines: list[str]) -> list[str]:
    """Drop any thumbnail blocks already in the file."""
    out: list[str] = []
    inside = False
    for line in lines:
        if line == _BLOCK_START:
            inside = True
            continue
        if inside:
            if line == _BLOCK_END:
                inside = False
            continue
        out.append(line)
    return out


def render_preview(
    stls: Iterable[Path | str],
    out_png: Path | str,
    *,
    size: int = 300,
    openscad_bin: str = DEFAULT_BIN,
    display: str | None = DEFAULT_DISPLAY,
    timeout: float = DEFAULT_TIMEOUT,
) -> Path:
    """Render the job's geometry to a square PNG, headlessly.

    Every STL is imported into one scene at its own coordinates, so an assembly
    previews as the assembled model. Preview shading is used rather than a CGAL
    render -- this is a 300px picture, not geometry, and a full render of a
    plate of text can take minutes.

    Raises:
        PreviewError: if OpenSCAD fails, or exits 0 having written nothing
            usable (it does this when the GL context cannot be created).
    """
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    paths = [str(Path(s).resolve()) for s in stls]
    if not paths:
        raise PreviewError("nothing to preview: no STLs given")

    source = "\n".join(f'import("{p}");' for p in paths) + "\n"

    env = dict(os.environ)
    if display:
        env["DISPLAY"] = display

    with tempfile.TemporaryDirectory() as tmp:
        scad = Path(tmp) / "preview.scad"
        scad.write_text(source)
        cmd = [
            openscad_bin,
            "--imgsize", f"{size},{size}",
            "--autocenter",
            "--viewall",
            "--projection", "p",
            "--colorscheme", "Tomorrow Night",
            "--camera", "0,0,0,55,0,25,0",
            "-o", str(out_png),
            str(scad),
        ]
        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PreviewError(f"openscad could not render the preview: {exc}") from exc

    if proc.returncode != 0:
        raise PreviewError(
            f"openscad exited {proc.returncode}: "
            f"{proc.stderr.decode(errors='replace').strip()[:400]}"
        )
    if not out_png.is_file() or out_png.stat().st_size == 0:
        raise PreviewError(
            f"openscad wrote no usable image to {out_png} "
            f"(stderr: {proc.stderr.decode(errors='replace').strip()[:400]})"
        )
    return out_png


def render_thumbnails(
    stls: Iterable[Path | str],
    workdir: Path | str,
    *,
    sizes: Sequence[int] = DEFAULT_SIZES,
    **render_kwargs: object,
) -> list[tuple[bytes, int, int]]:
    """Render one PNG per requested size, ready for `inject_thumbnails`."""
    stls = list(stls)
    workdir = Path(workdir)
    images: list[tuple[bytes, int, int]] = []
    for size in sizes:
        png = render_preview(stls, workdir / f"preview-{size}.png",
                             size=size, **render_kwargs)  # type: ignore[arg-type]
        images.append((png.read_bytes(), size, size))
    return images
