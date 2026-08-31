"""Local `orca-auto u1` command group: build -> slice -> (optionally) push to U1.

Unlike the other CLI commands (thin clients over the API), the U1 flow runs the
pipeline directly on the deploy host, because slicing and the Moonraker push are
host-local operations.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from argparse import Namespace  # re-exported for tests
from pathlib import Path

from orca_api.u1.loaded_filament import LoadedFilament
from orca_api.u1.naming import safe_stem, with_date_suffix
from orca_api.u1.support import SupportSpec
from orca_api.u1.filament_presets import FilamentPresetError
from orca_api.u1.loaded_filament import MaterialMismatch
from orca_api.u1.tool_resolution import ToolResolutionError, resolve_tool_by_color
from orca_api.u1.moonraker_client import MoonrakerClient
from orca_api.u1.scad_parts import ScadRenderError, output_name, render_scad
from orca_api.u1.pipeline import build_and_slice, build_slice_push
from orca_api.u1.threemf_builder import U1Part
from orca_api.u1.tool_map import ToolAssignment
from orca_api.u1.timelapse import TimelapseError, record_timelapse

__all__ = ["Namespace", "load_parts", "cmd_u1", "add_u1_subparser",
           "build_and_slice", "build_slice_push", "read_loaded_filaments",
           "load_support", "load_assembly", "load_process", "load_filaments",
           "load_name", "load_date_suffix", "spawn_timelapse_recorder"]


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


def _resolve_geometry(entry: dict, base: Path, scad_workdir: Path | None,
                      scad_opts: dict | None) -> Path:
    """The STL for a part, rendering it from `.scad` first if that is what it names.

    Raises:
        ValueError: if the entry names neither `stl` nor `scad`, or names `scad`
            with nowhere to put the render.
    """
    if entry.get("stl") is not None:
        stl = Path(entry["stl"])
        return stl if stl.is_absolute() else (base / stl)

    if entry.get("scad") is None:
        raise ValueError("needs either 'stl' or 'scad'")

    scad = Path(entry["scad"])
    scad = scad if scad.is_absolute() else (base / scad)
    if scad_workdir is None:
        raise ValueError(
            "a 'scad' part needs somewhere to render to -- pass a workdir"
        )
    params = entry.get("params") or None
    if params is not None and not isinstance(params, dict):
        raise ValueError("'params' must be an object of OpenSCAD variable overrides")
    out = Path(scad_workdir) / "scad" / output_name(scad, params)
    return render_scad(scad, out, params=params, **(scad_opts or {}))


def load_parts(
    job_path: Path | str,
    loaded: list[LoadedFilament | None] | None = None,
    *,
    scad_workdir: Path | str | None = None,
    scad_opts: dict | None = None,
) -> list[U1Part]:
    """Parse a JSON job spec into U1Part list. Paths resolve relative to the
    job file's directory when not absolute.

    A part names its geometry either as a ready `stl` or as a `scad` (plus
    optional `params`), which is rendered into `scad_workdir` and reused while
    it stays newer than every file in its include graph.

    A part names its tool either explicitly (`tool_index`) or by the colour loaded
    in it (`color`), which needs `loaded` -- what the printer reports is in each
    tool. An explicit index always wins.

    Raises:
        ValueError: if `parts` is missing/empty, an entry lacks required keys, or a
            colour-selected part was given without the printer's loaded filaments.
        ScadRenderError: if a `scad` part cannot be rendered to usable geometry.
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
            stl = _resolve_geometry(entry, base, scad_workdir, scad_opts)
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


def load_filaments(job_path: Path | str) -> dict[int, str] | None:
    """Parse the optional `filaments` block: tool number (1-based) -> preset.

    For tools that carry no geometry. Designating a tool as the support material
    does not resolve its slot, so without this a PETG support interface is
    extruded at whatever the template slot says -- PLA temperature.

    Raises:
        ValueError: on a tool number outside 1-4 or a non-string preset.
    """
    spec = json.loads(Path(job_path).read_text())
    raw = spec.get("filaments")
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"'filaments' in {job_path} must be an object of tool -> preset")
    out: dict[int, str] = {}
    for tool, preset in raw.items():
        try:
            n = int(tool)
        except (TypeError, ValueError):
            raise ValueError(f"'filaments' key {tool!r} is not a tool number") from None
        if not 1 <= n <= 4:
            raise ValueError(f"'filaments' tool {n} is outside the U1's 1-4")
        if not isinstance(preset, str) or not preset.strip():
            raise ValueError(f"'filaments' tool {n} needs a preset name")
        out[n - 1] = preset
    return out or None


def load_name(job_path: Path | str) -> str:
    """The name this job shows under on the U1's screen.

    An explicit `"name"` in the spec wins. Otherwise the spec's own filename
    carries it, with a `job-` prefix dropped: `job-card-3up.json` is already
    describing itself as `card-3up`, and repeating "job" on a 3-inch screen
    costs four characters of the part that identifies it.
    """
    spec = json.loads(Path(job_path).read_text())
    raw = spec.get("name")
    if isinstance(raw, str) and raw.strip():
        return safe_stem(raw)

    stem = Path(job_path).stem
    for prefix in ("job-", "job_"):
        if stem.lower().startswith(prefix) and len(stem) > len(prefix):
            stem = stem[len(prefix):]
            break
    return safe_stem(stem)


def load_date_suffix(job_path: Path | str) -> bool:
    """Whether this job stamps its export date into the name (`"date_suffix"`).

    Off unless asked for. On, a re-slice lands beside its predecessor on the
    printer rather than silently replacing it.
    """
    spec = json.loads(Path(job_path).read_text())
    return bool(spec.get("date_suffix", False))


def load_assembly(job_path: Path | str) -> bool:
    """Whether the job's parts are one model sharing an origin (`"assembly": true`)."""
    spec = json.loads(Path(job_path).read_text())
    return bool(spec.get("assembly", False))


def load_process(job_path: Path | str) -> dict | None:
    """Parse the optional `process` block: raw project-config overrides.

    Deliberately unvalidated -- OrcaSlicer owns the key namespace, and pinning a
    whitelist here would go stale every release.

    Raises:
        ValueError: if `process` is present but not a JSON object.
    """
    spec = json.loads(Path(job_path).read_text())
    process = spec.get("process")
    if process is None:
        return None
    if not isinstance(process, dict):
        raise ValueError(f"'process' in {job_path} must be an object, got {type(process).__name__}")
    return process or None


def spawn_timelapse_recorder(
    moonraker: str,
    out_path: Path | str,
    *,
    api_key: str | None = None,
    interval: float = 5.0,
    log_path: Path | str | None = None,
) -> subprocess.Popen:
    """Launch `orca-auto u1 timelapse` detached, so it outlives this process.

    The recorder blocks for the length of the print, so it runs as its own
    process rather than inside whatever invoked `print --timelapse` -- a CLI
    call that returns the moment the job is pushed shouldn't then sit there
    for two more hours polling a webcam.

    `api_key` travels in the child's environment, not its argv -- `ps`/
    `/proc/<pid>/cmdline` are world-readable for as long as the recorder runs,
    which is hours, not the seconds a normal CLI invocation is exposed for.
    """
    cmd = [
        sys.executable, "-m", "orca_cli.cli", "u1", "timelapse",
        "--moonraker", moonraker,
        "--out", str(out_path),
        "--interval", str(interval),
    ]
    env = {**os.environ, "MOONRAKER_API_KEY": api_key} if api_key else None
    log_file = open(log_path, "ab") if log_path else subprocess.DEVNULL
    return subprocess.Popen(
        cmd, stdout=log_file, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True, env=env,
    )


def cmd_u1(args: Namespace) -> int:
    """Dispatch `orca-auto u1 {slice,print,filaments,timelapse}`."""
    if args.subcommand == "filaments":
        loaded = asyncio.run(read_loaded_filaments(args.moonraker, args.api_key))
        print(f"Loaded filament reported by {args.moonraker}:")
        _print_loaded(loaded)
        return 0

    if args.subcommand == "timelapse":
        try:
            result = asyncio.run(record_timelapse(
                args.moonraker, args.out,
                api_key=args.api_key, webcam_name=args.webcam_name,
                interval_s=args.interval, fps=args.fps,
                rotate_180=not args.no_rotate,
                wait_timeout_s=args.wait_timeout, max_duration_s=args.max_duration,
                keep_frames=args.keep_frames,
            ))
        except TimelapseError as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Wrote {result.video_path} ({result.frame_count} frames, "
              f"~{result.duration_s / 60:.1f} min captured)")
        return 0

    moonraker = getattr(args, "moonraker", None)
    loaded = None
    if moonraker and getattr(args, "read_filament", True):
        try:
            loaded = asyncio.run(read_loaded_filaments(moonraker, getattr(args, "api_key", None)))
        except Exception as exc:  # printer unreachable is not fatal for slicing
            print(f"Warning: could not read loaded filament from {moonraker}: {exc}")

    # The workdir is settled before parsing, because a `scad` part renders into it.
    if args.subcommand == "slice":
        workdir = args.workdir or str(Path(args.out).parent / "u1-work")
    else:
        workdir = args.workdir or "u1-work"

    scad_opts = {
        "openscad_bin": getattr(args, "openscad", None) or "openscad",
        "display": args.display,
        "force": getattr(args, "force_scad", False),
    }
    if getattr(args, "scad_timeout", None):
        scad_opts["timeout"] = float(args.scad_timeout)

    try:
        parts = load_parts(args.job, loaded=loaded, scad_workdir=workdir,
                           scad_opts=scad_opts)
        support = load_support(args.job)
        assembly = load_assembly(args.job)
        process = load_process(args.job)
        extra_filaments = load_filaments(args.job)
        name = getattr(args, "name", None) or load_name(args.job)
        if getattr(args, "date_suffix", None) is None:
            dated = load_date_suffix(args.job)
        else:
            dated = bool(args.date_suffix)
        if dated:
            name = with_date_suffix(name)
    except (OSError, ValueError, json.JSONDecodeError, ScadRenderError) as exc:
        print(f"Error: {exc}")
        return 1

    slice_kw = dict(datadir=args.datadir, orcaslicer_bin=args.bin, display=args.display)
    build_kw = dict(name=name, loaded=loaded, support=support, assembly=assembly,
                    process=process, extra_filaments=extra_filaments,
                    thumbnails=not getattr(args, "no_thumbnails", False))

    if args.subcommand == "slice":
        try:
            result = build_and_slice(parts, workdir, **build_kw, **slice_kw)
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
        if getattr(args, "timelapse", False) and args.timelapse_interval <= 0:
            print(f"Error: --timelapse-interval must be positive, got {args.timelapse_interval}")
            return 1
        try:
            result = asyncio.run(
                build_slice_push(
                parts, workdir,
                moonraker_url=args.moonraker, api_key=args.api_key,
                mode=args.mode, **build_kw,
                    read_filament=getattr(args, "read_filament", True), **slice_kw,
                )
            )
        except (ToolResolutionError, FilamentPresetError, MaterialMismatch) as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Pushed {result.gcode_path} to {args.moonraker} as {result.pushed_as} (mode={args.mode})")

        if getattr(args, "timelapse", False):
            out = Path(workdir) / f"{name}_timelapse.mp4"
            log_path = Path(workdir) / f"{name}_timelapse.log"
            spawn_timelapse_recorder(
                args.moonraker, out, api_key=args.api_key,
                interval=args.timelapse_interval, log_path=log_path,
            )
            print(f"Recording a timelapse in the background -> {out} "
                  f"(log: {log_path}; only captures once the print actually starts)")
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
    sl.add_argument("--openscad", default="openscad",
                    help="OpenSCAD executable, for job specs with 'scad' parts")
    sl.add_argument("--scad-timeout", dest="scad_timeout", type=float, default=None,
                    help="Seconds before a SCAD render is killed (default 900)")
    sl.add_argument("--force-scad", dest="force_scad", action="store_true",
                    help="Re-render 'scad' parts even when the cached STL is current")
    sl.add_argument("--name", default=None,
                    help="Name the job prints under on the U1 (default: the job spec's own filename)")
    sl.add_argument("--no-thumbnails", dest="no_thumbnails", action="store_true",
                    help="Skip the on-screen preview render")
    sl.add_argument("--date-suffix", dest="date_suffix", action="store_true",
                    default=None,
                    help="Stamp the export date into the name, card-3up(08.13.26).gcode, so a re-slice does not replace its predecessor on the printer")
    sl.add_argument("--no-date-suffix", dest="date_suffix", action="store_false",
                    help="Never stamp the date, even if the job spec asks for it")
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
    pr.add_argument("--openscad", default="openscad",
                    help="OpenSCAD executable, for job specs with 'scad' parts")
    pr.add_argument("--scad-timeout", dest="scad_timeout", type=float, default=None,
                    help="Seconds before a SCAD render is killed (default 900)")
    pr.add_argument("--force-scad", dest="force_scad", action="store_true",
                    help="Re-render 'scad' parts even when the cached STL is current")
    pr.add_argument("--name", default=None,
                    help="Name the job prints under on the U1 (default: the job spec's own filename)")
    pr.add_argument("--no-thumbnails", dest="no_thumbnails", action="store_true",
                    help="Skip the on-screen preview render")
    pr.add_argument("--date-suffix", dest="date_suffix", action="store_true",
                    default=None,
                    help="Stamp the export date into the name, card-3up(08.13.26).gcode, so a re-slice does not replace its predecessor on the printer")
    pr.add_argument("--no-date-suffix", dest="date_suffix", action="store_false",
                    help="Never stamp the date, even if the job spec asks for it")
    pr.add_argument("--timelapse", action="store_true",
                    help="After pushing, record a timelapse in the background from the "
                         "printer's own webcam (workaround for the native TIMELAPSE_START "
                         "macro never arming on a Moonraker-pushed job)")
    pr.add_argument("--timelapse-interval", dest="timelapse_interval", type=float, default=5.0,
                    help="Seconds between timelapse frames (default 5.0)")
    pr.set_defaults(func=cmd_u1, read_filament=True)

    fl = u1_sub.add_parser("filaments", help="Show which spools the U1 currently holds")
    fl.add_argument("--moonraker", required=True, help="U1 Moonraker base URL")
    fl.add_argument("--api-key", dest="api_key", default=None, help="Moonraker API key")
    fl.set_defaults(func=cmd_u1)

    tl = u1_sub.add_parser("timelapse",
                           help="Poll the printer's webcam and assemble a timelapse "
                                "while a print runs")
    tl.add_argument("--moonraker", required=True, help="U1 Moonraker base URL")
    tl.add_argument("--api-key", dest="api_key", default=os.environ.get("MOONRAKER_API_KEY"),
                    help="Moonraker API key (default: $MOONRAKER_API_KEY)")
    tl.add_argument("--out", "-o", required=True, help="Destination mp4 path")
    tl.add_argument("--webcam-name", dest="webcam_name", default=None,
                    help="Webcam to use by name (default: the first enabled one)")
    tl.add_argument("--interval", type=float, default=5.0,
                    help="Seconds between captured frames (default 5.0)")
    tl.add_argument("--fps", type=int, default=30,
                    help="Output video framerate (default 30)")
    tl.add_argument("--no-rotate", dest="no_rotate", action="store_true",
                    help="Skip the 180-degree flip (the U1's raw feed is upside-down)")
    tl.add_argument("--wait-timeout", dest="wait_timeout", type=float, default=1800.0,
                    help="Give up if no print starts within this many seconds (default 1800)")
    tl.add_argument("--max-duration", dest="max_duration", type=float, default=6 * 3600,
                    help="Stop capturing after this many seconds regardless of print "
                         "state, as a backstop against a wedged status poll (default 6h)")
    tl.add_argument("--keep-frames", dest="keep_frames", action="store_true",
                    help="Don't delete the captured JPEGs after encoding")
    tl.set_defaults(func=cmd_u1)
