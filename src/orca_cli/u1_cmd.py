"""Local `orca-auto u1` command group: build -> slice -> (optionally) push to U1.

Unlike the other CLI commands (thin clients over the API), the U1 flow runs the
pipeline directly on the deploy host, because slicing and the Moonraker push are
host-local operations.
"""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace  # re-exported for tests
from pathlib import Path

from orca_api.u1.pipeline import build_and_slice, build_slice_push
from orca_api.u1.threemf_builder import U1Part
from orca_api.u1.tool_map import ToolAssignment

__all__ = ["Namespace", "load_parts", "cmd_u1", "add_u1_subparser",
           "build_and_slice", "build_slice_push"]


def load_parts(job_path: Path | str) -> list[U1Part]:
    """Parse a JSON job spec into U1Part list. STL paths resolve relative to the
    job file's directory when not absolute.

    Raises:
        ValueError: if `parts` is missing/empty or an entry lacks required keys.
    """
    job_path = Path(job_path)
    spec = json.loads(job_path.read_text())
    raw = spec.get("parts") or []
    if not raw:
        raise ValueError(f"job spec {job_path} has no 'parts'")

    base = job_path.parent
    parts: list[U1Part] = []
    for i, entry in enumerate(raw):
        try:
            stl = Path(entry["stl"])
            stl = stl if stl.is_absolute() else (base / stl)
            parts.append(
                U1Part(
                    stl_path=str(stl),
                    assignment=ToolAssignment(
                        tool_index=int(entry["tool_index"]),
                        filament=str(entry["filament"]),
                        color=entry.get("color"),
                    ),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"part #{i} in {job_path} is invalid: {exc}") from exc
    return parts


def cmd_u1(args: Namespace) -> int:
    """Dispatch `orca-auto u1 {slice,print}`."""
    try:
        parts = load_parts(args.job)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}")
        return 1

    slice_kw = dict(datadir=args.datadir, orcaslicer_bin=args.bin, display=args.display)

    if args.subcommand == "slice":
        workdir = args.workdir or str(Path(args.out).parent / "u1-work")
        result = build_and_slice(parts, workdir, **slice_kw)
        # Move/copy the produced gcode to the requested --out path
        out = Path(args.out)
        if result.gcode_path != out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(Path(result.gcode_path).read_bytes())
        print(f"Sliced {len(parts)} part(s) -> {out}")
        return 0

    if args.subcommand == "print":
        workdir = args.workdir or "u1-work"
        result = asyncio.run(
            build_slice_push(
                parts, workdir,
                moonraker_url=args.moonraker, api_key=args.api_key,
                mode=args.mode, **slice_kw,
            )
        )
        print(f"Pushed {result.gcode_path} to {args.moonraker} as {result.pushed_as} (mode={args.mode})")
        return 0

    print(f"Error: unknown u1 subcommand {args.subcommand!r}")
    return 1


def add_u1_subparser(subparsers) -> None:
    """Wire the `u1` subcommand group into the main parser."""
    u1 = subparsers.add_parser("u1", help="Snapmaker U1 multicolor: build->slice->print")
    u1_sub = u1.add_subparsers(dest="subcommand", required=True)

    sl = u1_sub.add_parser("slice", help="Build the U1 .3mf and slice to G-code (no push)")
    sl.add_argument("job", help="Path to the JSON job spec")
    sl.add_argument("--out", "-o", required=True, help="Destination G-code path")
    sl.add_argument("--workdir", default=None, help="Working dir for intermediates")
    sl.add_argument("--datadir", default=str(Path.home() / ".config" / "OrcaSlicer"),
                    help="OrcaSlicer data dir (system bundle)")
    sl.add_argument("--bin", default="orcaslicer", help="OrcaSlicer executable")
    sl.add_argument("--display", default=":99", help="X DISPLAY for headless slice")
    sl.set_defaults(func=cmd_u1)

    pr = u1_sub.add_parser("print", help="Build, slice, and push to the U1 via Moonraker")
    pr.add_argument("job", help="Path to the JSON job spec")
    pr.add_argument("--moonraker", required=True, help="U1 Moonraker base URL")
    pr.add_argument("--api-key", dest="api_key", default=None, help="Moonraker API key")
    pr.add_argument("--mode", choices=["queue", "start"], default="queue",
                    help="'queue' (upload+enqueue) or 'start' (print now)")
    pr.add_argument("--workdir", default=None, help="Working dir for intermediates")
    pr.add_argument("--datadir", default=str(Path.home() / ".config" / "OrcaSlicer"),
                    help="OrcaSlicer data dir (system bundle)")
    pr.add_argument("--bin", default="orcaslicer", help="OrcaSlicer executable")
    pr.add_argument("--display", default=":99", help="X DISPLAY for headless slice")
    pr.set_defaults(func=cmd_u1)
