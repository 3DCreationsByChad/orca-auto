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

from orca_api.u1.loaded_filament import LoadedFilament
from orca_api.u1.support import SupportSpec
from orca_api.u1.filament_presets import FilamentPresetError
from orca_api.u1.loaded_filament import MaterialMismatch
from orca_api.u1.tool_resolution import ToolResolutionError, resolve_tool_by_color
from orca_api.u1.moonraker_client import MoonrakerClient
from orca_api.u1.pipeline import build_and_slice, build_slice_push
from orca_api.u1.threemf_builder import U1Part
from orca_api.u1.tool_map import ToolAssignment

__all__ = ["Namespace", "load_parts", "cmd_u1", "add_u1_subparser",
           "build_and_slice", "build_slice_push", "read_loaded_filaments",
           "load_support"]


async def read_loaded_filaments(
    moonraker_url: str, api_key: str | None = None
) -> list[LoadedFilament | None]:
    """Ask the printer which spools are loaded, one entry per tool."""
    async with MoonrakerClient(moonraker_url, api_key=api_key) as mc:
        return await mc.get_loaded_filaments()


def _print_loaded(loaded: list[LoadedFilament | None]) -> None:
    for index, spool in enumerate(loaded):
        if spool is None:
            print(f"  tool {index + 1}: not tagged - the printer cannot identify it")
            continue
        print(
            f"  tool {index + 1}: {spool.label:<18} {spool.color}  "
            f"first layer {spool.first_layer_temp}C / then {spool.other_layer_temp}C / "
            f"bed {spool.bed_temp}C  ({spool.vendor})"
        )


def load_parts(
    job_path: Path | str,
    loaded: list[LoadedFilament | None] | None = None,
) -> list[U1Part]:
    """Parse a JSON job spec into U1Part list. STL paths resolve relative to the
    job file's directory when not absolute.

    A part names its tool either explicitly (`tool_index`) or by the colour loaded
    in it (`color`), which needs `loaded` -- what the printer reports is in each
    tool. An explicit index always wins.

    Raises:
        ValueError: if `parts` is missing/empty, an entry lacks required keys, or a
            colour-selected part was given without the printer's loaded filaments.
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
            color = entry.get("color")

            if entry.get("tool_index") is not None:
                tool_index = int(entry["tool_index"])
            elif color is not None:
                if loaded is None:
                    raise ValueError(
                        "selecting a tool by colour needs to know what is loaded -- "
                        "pass --moonraker so the printer can be asked, or give an "
                        "explicit tool_index"
                    )
                tool_index = resolve_tool_by_color(color, loaded)
            else:
                raise ValueError("needs either 'tool_index' or 'color'")

            parts.append(
                U1Part(
                    stl_path=str(stl),
                    assignment=ToolAssignment(
                        tool_index=tool_index,
                        filament=str(entry["filament"]),
                        color=color,
                    ),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"part #{i} in {job_path} is invalid: {exc}") from exc
    return parts


def load_support(job_path: Path | str) -> SupportSpec | None:
    """Parse the optional `support` block of a job spec."""
    spec = json.loads(Path(job_path).read_text())
    return SupportSpec.from_dict(spec.get("support"))


def cmd_u1(args: Namespace) -> int:
    """Dispatch `orca-auto u1 {slice,print,filaments}`."""
    if args.subcommand == "filaments":
        loaded = asyncio.run(read_loaded_filaments(args.moonraker, args.api_key))
        print(f"Loaded filament reported by {args.moonraker}:")
        _print_loaded(loaded)
        return 0

    moonraker = getattr(args, "moonraker", None)
    loaded = None
    if moonraker and getattr(args, "read_filament", True):
        try:
            loaded = asyncio.run(read_loaded_filaments(moonraker, getattr(args, "api_key", None)))
        except Exception as exc:  # printer unreachable is not fatal for slicing
            print(f"Warning: could not read loaded filament from {moonraker}: {exc}")

    try:
        parts = load_parts(args.job, loaded=loaded)
        support = load_support(args.job)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}")
        return 1

    slice_kw = dict(datadir=args.datadir, orcaslicer_bin=args.bin, display=args.display)

    if args.subcommand == "slice":
        workdir = args.workdir or str(Path(args.out).parent / "u1-work")
        try:
            result = build_and_slice(parts, workdir, loaded=loaded, support=support, **slice_kw)
        except (ToolResolutionError, FilamentPresetError, MaterialMismatch) as exc:
            print(f"Error: {exc}")
            return 1
        # Move/copy the produced gcode to the requested --out path
        out = Path(args.out)
        if result.gcode_path != out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(Path(result.gcode_path).read_bytes())
        print(f"Sliced {len(parts)} part(s) -> {out}")
        return 0

    if args.subcommand == "print":
        workdir = args.workdir or "u1-work"
        try:
            result = asyncio.run(
                build_slice_push(
                parts, workdir,
                moonraker_url=args.moonraker, api_key=args.api_key,
                mode=args.mode, loaded=loaded, support=support,
                    read_filament=getattr(args, "read_filament", True), **slice_kw,
                )
            )
        except (ToolResolutionError, FilamentPresetError, MaterialMismatch) as exc:
            print(f"Error: {exc}")
            return 1
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
    sl.add_argument("--moonraker", default=None,
                    help="U1 Moonraker URL, to resolve colours and read spool temps")
    sl.add_argument("--api-key", dest="api_key", default=None, help="Moonraker API key")
    sl.set_defaults(func=cmd_u1, read_filament=True)

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
    pr.add_argument("--no-read-filament", dest="read_filament", action="store_false",
                    help="Don't read the loaded spools' RFID tags; use the profile as-is")
    pr.set_defaults(func=cmd_u1, read_filament=True)

    fl = u1_sub.add_parser("filaments", help="Show which spools the U1 currently holds")
    fl.add_argument("--moonraker", required=True, help="U1 Moonraker base URL")
    fl.add_argument("--api-key", dest="api_key", default=None, help="Moonraker API key")
    fl.set_defaults(func=cmd_u1)
