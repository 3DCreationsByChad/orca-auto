import re
import argparse
import json
from argparse import Namespace
from pathlib import Path

import pytest

from orca_cli import u1_cmd
from orca_api.u1.pipeline import U1JobResult
from orca_api.u1.threemf_builder import U1Part


def _write_job(tmp_path):
    (tmp_path / "a.stl").write_bytes(b"solid\nendsolid\n")
    spec = {"parts": [{"stl": "a.stl", "tool_index": 0, "filament": "PLA", "color": "#519F61"}]}
    job = tmp_path / "job.json"
    job.write_text(json.dumps(spec))
    return job


def test_load_parts_resolves_stl_relative_to_job_file(tmp_path):
    job = _write_job(tmp_path)
    parts = u1_cmd.load_parts(job)
    assert len(parts) == 1
    assert isinstance(parts[0], U1Part)
    assert Path(parts[0].stl_path) == (tmp_path / "a.stl")
    assert parts[0].assignment.tool_index == 0
    assert parts[0].assignment.filament == "PLA"


def test_load_parts_rejects_empty(tmp_path):
    job = tmp_path / "empty.json"
    job.write_text(json.dumps({"parts": []}))
    with pytest.raises(ValueError):
        u1_cmd.load_parts(job)


def test_cmd_u1_slice_invokes_build_and_slice(tmp_path, monkeypatch, capsys):
    job = _write_job(tmp_path)
    out = tmp_path / "out.gcode"
    called = {}

    def fake_build_and_slice(parts, workdir, **kw):
        called["parts"] = parts
        called["workdir"] = workdir
        Path(out).write_text("; g\n")
        return U1JobResult(project_3mf=tmp_path / "job.3mf", gcode_path=out)

    monkeypatch.setattr(u1_cmd, "build_and_slice", fake_build_and_slice)

    args = u1_cmd.Namespace(
        subcommand="slice", job=str(job), out=str(out), workdir=str(tmp_path / "w"),
        datadir="/dd", bin="orcaslicer", display=":99",
    )
    rc = u1_cmd.cmd_u1(args)
    assert rc == 0
    assert len(called["parts"]) == 1
    assert "out.gcode" in capsys.readouterr().out


def test_cmd_u1_print_invokes_build_slice_push(tmp_path, monkeypatch, capsys):
    job = _write_job(tmp_path)

    async def fake_build_slice_push(parts, workdir, **kw):
        assert kw["moonraker_url"] == "http://u1.local"
        assert kw["mode"] == "queue"
        return U1JobResult(project_3mf=tmp_path / "job.3mf",
                           gcode_path=tmp_path / "job.gcode", pushed_as="job.gcode")

    monkeypatch.setattr(u1_cmd, "build_slice_push", fake_build_slice_push)

    args = u1_cmd.Namespace(
        subcommand="print", job=str(job), workdir=str(tmp_path / "w"),
        moonraker="http://u1.local", api_key=None, mode="queue",
        datadir="/dd", bin="orcaslicer", display=":99",
    )
    rc = u1_cmd.cmd_u1(args)
    assert rc == 0
    assert "job.gcode" in capsys.readouterr().out


def test_filaments_subcommand_reports_what_the_printer_holds(capsys, monkeypatch):
    """`orca-auto u1 filaments` answers 'what is actually in the machine right now'."""
    from orca_api.u1.loaded_filament import LoadedFilament

    async def fake_read(url, api_key=None):
        return [
            LoadedFilament(0, "PLA", "SnapSpeed", "#080A0D", 230, 220, 60, 190, 230, "Snapmaker"),
            None,
            None,
            None,
        ]

    monkeypatch.setattr(u1_cmd, "read_loaded_filaments", fake_read)
    rc = u1_cmd.cmd_u1(Namespace(subcommand="filaments", moonraker="http://u1.local", api_key=None))

    out = capsys.readouterr().out
    assert rc == 0
    assert "PLA SnapSpeed" in out
    assert "#080A0D" in out
    assert "230" in out          # first-layer temp from the tag
    assert "not tagged" in out   # the three untagged slots are stated, not hidden


def test_print_accepts_the_read_filament_opt_out():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    u1_cmd.add_u1_subparser(sub)

    args = parser.parse_args(["u1", "print", "job.json", "--moonraker", "http://u1.local",
                              "--no-read-filament"])
    assert args.read_filament is False

    args = parser.parse_args(["u1", "print", "job.json", "--moonraker", "http://u1.local"])
    assert args.read_filament is True


def _job(tmp_path, parts, support=None):
    (tmp_path / "a.stl").write_bytes(b"solid\nendsolid\n")
    (tmp_path / "b.stl").write_bytes(b"solid\nendsolid\n")
    spec = {"parts": parts}
    if support:
        spec["support"] = support
    p = tmp_path / "job.json"
    p.write_text(json.dumps(spec))
    return p


def _loaded_black_and_yellow():
    from orca_api.u1.loaded_filament import parse_filament_detect
    def tag(mat, argb):
        return {"MAIN_TYPE": mat, "SUB_TYPE": "", "ARGB_COLOR": argb, "FIRST_LAYER_TEMP": 230,
                "OTHER_LAYER_TEMP": 220, "BED_TEMP": 60, "HOTEND_MIN_TEMP": 190,
                "HOTEND_MAX_TEMP": 250, "VENDOR": "Snapmaker"}
    return parse_filament_detect([tag("PLA", 0xFF080A0D), tag("PLA", 0xFFF4C032)])


def test_colour_selects_the_tool_when_no_index_is_given(tmp_path):
    job = _job(tmp_path, [{"stl": "a.stl", "color": "black", "filament": "PLA"},
                          {"stl": "b.stl", "color": "#F4C032", "filament": "PLA"}])

    parts = u1_cmd.load_parts(job, loaded=_loaded_black_and_yellow())

    assert [p.assignment.tool_index for p in parts] == [0, 1]


def test_explicit_tool_index_wins_over_colour(tmp_path):
    job = _job(tmp_path, [{"stl": "a.stl", "tool_index": 1, "color": "black", "filament": "PLA"}])

    parts = u1_cmd.load_parts(job, loaded=_loaded_black_and_yellow())

    assert parts[0].assignment.tool_index == 1


def test_colour_without_a_printer_says_what_to_do(tmp_path):
    job = _job(tmp_path, [{"stl": "a.stl", "color": "black", "filament": "PLA"}])

    with pytest.raises(ValueError, match="tool_index|--moonraker"):
        u1_cmd.load_parts(job)


def test_part_with_neither_index_nor_colour_is_rejected(tmp_path):
    job = _job(tmp_path, [{"stl": "a.stl", "filament": "PLA"}])

    with pytest.raises(ValueError):
        u1_cmd.load_parts(job, loaded=_loaded_black_and_yellow())


def test_support_block_is_parsed_from_the_job(tmp_path):
    from orca_api.u1.support import SupportSpec
    job = _job(tmp_path, [{"stl": "a.stl", "tool_index": 0, "filament": "PLA"}],
               support={"interface": "PETG", "body": "PLA"})

    assert u1_cmd.load_support(job) == SupportSpec(body="PLA", interface="PETG")


def test_no_support_block_means_none(tmp_path):
    job = _job(tmp_path, [{"stl": "a.stl", "tool_index": 0, "filament": "PLA"}])

    assert u1_cmd.load_support(job) is None


def _write_job_with(tmp_path, **extra):
    (tmp_path / "a.stl").write_bytes(b"solid\nendsolid\n")
    spec = {
        "parts": [{"stl": "a.stl", "tool_index": 0, "filament": "PLA"}],
        **extra,
    }
    job = tmp_path / "job.json"
    job.write_text(json.dumps(spec))
    return job


def test_load_assembly_defaults_to_false(tmp_path):
    assert u1_cmd.load_assembly(_write_job_with(tmp_path)) is False


def test_load_assembly_reads_the_flag(tmp_path):
    assert u1_cmd.load_assembly(_write_job_with(tmp_path, assembly=True)) is True


def test_load_process_returns_none_when_absent(tmp_path):
    assert u1_cmd.load_process(_write_job_with(tmp_path)) is None


def test_load_process_returns_the_overrides(tmp_path):
    job = _write_job_with(tmp_path, process={"wall_generator": "arachne"})
    assert u1_cmd.load_process(job) == {"wall_generator": "arachne"}


def test_load_process_rejects_a_non_object(tmp_path):
    job = _write_job_with(tmp_path, process=["wall_generator"])
    with pytest.raises(ValueError, match="must be an object"):
        u1_cmd.load_process(job)


def test_cmd_u1_slice_forwards_assembly_and_process(tmp_path, monkeypatch):
    """A job asking for one assembled model must not silently slice as two."""
    job = _write_job_with(tmp_path, assembly=True, process={"wall_generator": "arachne"})
    out = tmp_path / "out.gcode"
    called = {}

    def fake_build_and_slice(parts, workdir, **kw):
        called.update(kw)
        Path(out).write_text("; g\n")
        return U1JobResult(project_3mf=tmp_path / "job.3mf", gcode_path=out)

    monkeypatch.setattr(u1_cmd, "build_and_slice", fake_build_and_slice)
    args = u1_cmd.Namespace(
        subcommand="slice", job=str(job), out=str(out), workdir=str(tmp_path / "w"),
        datadir="/dd", bin="orcaslicer", display=":99",
    )
    assert u1_cmd.cmd_u1(args) == 0
    assert called["assembly"] is True
    assert called["process"] == {"wall_generator": "arachne"}


def _fake_openscad(tmp_path):
    """Stand-in for OpenSCAD that emits a minimal valid binary STL."""
    import struct
    stl = (b"\0" * 80 + struct.pack("<I", 2) + b"\0" * 100).hex()
    script = tmp_path / "fake-openscad"
    script.write_text(
        "#!/bin/sh\n"
        'echo "$@" >> ' + str(tmp_path / "scad-calls.txt") + "\n"
        'for a in "$@"; do case "$a" in *.stl) printf "' + stl + '" | xxd -r -p > "$a";; esac; done\n'
    )
    script.chmod(0o755)
    return str(script)


def test_load_parts_renders_a_scad_part(tmp_path):
    (tmp_path / "card.scad").write_text("cube(1);")
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"parts": [
        {"scad": "card.scad", "params": {"FACE_DOWN": True},
         "tool_index": 0, "filament": "PLA"}
    ]}))
    parts = u1_cmd.load_parts(
        job, scad_workdir=tmp_path / "w",
        scad_opts={"openscad_bin": _fake_openscad(tmp_path)},
    )
    assert len(parts) == 1
    assert Path(parts[0].stl_path).is_file()
    assert Path(parts[0].stl_path).parent == tmp_path / "w" / "scad"
    assert "-D FACE_DOWN=true" in (tmp_path / "scad-calls.txt").read_text()


def test_a_second_run_reuses_the_rendered_stl(tmp_path):
    (tmp_path / "card.scad").write_text("cube(1);")
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"parts": [
        {"scad": "card.scad", "tool_index": 0, "filament": "PLA"}
    ]}))
    opts = {"openscad_bin": _fake_openscad(tmp_path)}
    u1_cmd.load_parts(job, scad_workdir=tmp_path / "w", scad_opts=opts)
    u1_cmd.load_parts(job, scad_workdir=tmp_path / "w", scad_opts=opts)
    calls = (tmp_path / "scad-calls.txt").read_text().strip().splitlines()
    assert len(calls) == 1  # the second run must not re-render


def test_a_part_with_neither_stl_nor_scad_is_rejected(tmp_path):
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"parts": [{"tool_index": 0, "filament": "PLA"}]}))
    with pytest.raises(ValueError, match="either 'stl' or 'scad'"):
        u1_cmd.load_parts(job)


def test_a_scad_part_without_a_workdir_is_rejected(tmp_path):
    (tmp_path / "card.scad").write_text("cube(1);")
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"parts": [
        {"scad": "card.scad", "tool_index": 0, "filament": "PLA"}
    ]}))
    with pytest.raises(ValueError, match="somewhere to render to"):
        u1_cmd.load_parts(job)


def test_cmd_u1_slice_renders_scad_parts_into_the_workdir(tmp_path, monkeypatch):
    (tmp_path / "card.scad").write_text("cube(1);")
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"parts": [
        {"scad": "card.scad", "tool_index": 0, "filament": "PLA"}
    ]}))
    out = tmp_path / "out.gcode"
    seen = {}

    def fake_build_and_slice(parts, workdir, **kw):
        seen["stl"] = parts[0].stl_path
        Path(out).write_text("; g\n")
        return U1JobResult(project_3mf=tmp_path / "job.3mf", gcode_path=out)

    monkeypatch.setattr(u1_cmd, "build_and_slice", fake_build_and_slice)
    args = u1_cmd.Namespace(
        subcommand="slice", job=str(job), out=str(out), workdir=str(tmp_path / "w"),
        datadir="/dd", bin="orcaslicer", display=":99",
        openscad=_fake_openscad(tmp_path), scad_timeout=None, force_scad=False,
    )
    assert u1_cmd.cmd_u1(args) == 0
    assert Path(seen["stl"]).is_file()


def test_a_failed_scad_render_stops_the_job_with_a_message(tmp_path, capsys):
    (tmp_path / "card.scad").write_text("cube(1);")
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"parts": [
        {"scad": "card.scad", "tool_index": 0, "filament": "PLA"}
    ]}))
    broken = tmp_path / "broken-openscad"
    broken.write_text("#!/bin/sh\nexit 2\n")
    broken.chmod(0o755)
    args = u1_cmd.Namespace(
        subcommand="slice", job=str(job), out=str(tmp_path / "o.gcode"),
        workdir=str(tmp_path / "w"), datadir="/dd", bin="orcaslicer", display=":99",
        openscad=str(broken), scad_timeout=None, force_scad=False,
    )
    assert u1_cmd.cmd_u1(args) == 1
    assert "OpenSCAD failed" in capsys.readouterr().out


def test_load_filaments_maps_1_based_tools_to_slots(tmp_path):
    job = _write_job_with(tmp_path, filaments={"4": "Snapmaker PETG @U1"})
    assert u1_cmd.load_filaments(job) == {3: "Snapmaker PETG @U1"}


def test_load_filaments_rejects_a_tool_outside_the_machine(tmp_path):
    with pytest.raises(ValueError, match="outside the U1"):
        u1_cmd.load_filaments(_write_job_with(tmp_path, filaments={"5": "PETG"}))


def test_load_filaments_absent_is_none(tmp_path):
    assert u1_cmd.load_filaments(_write_job_with(tmp_path)) is None


# --- the name a job carries onto the printer's screen -----------------------

def _named_job(tmp_path, filename, **extra):
    job = tmp_path / filename
    stl = tmp_path / "a.stl"
    stl.write_text("solid a\nendsolid a\n")
    spec = {"parts": [{"stl": "a.stl", "tool_index": 0, "filament": "PLA"}]}
    spec.update(extra)
    job.write_text(json.dumps(spec))
    return job


def test_load_name_prefers_an_explicit_name(tmp_path):
    job = _named_job(tmp_path, "job-whatever.json", name="Quad Lock card")
    assert u1_cmd.load_name(job) == "Quad-Lock-card"


def test_load_name_falls_back_to_the_spec_filename(tmp_path):
    job = _named_job(tmp_path, "card-3up.json")
    assert u1_cmd.load_name(job) == "card-3up"


def test_load_name_drops_a_redundant_job_prefix(tmp_path):
    # job-card-3up.json is already describing itself as card-3up
    assert u1_cmd.load_name(_named_job(tmp_path, "job-card-3up.json")) == "card-3up"
    assert u1_cmd.load_name(_named_job(tmp_path, "job_card.json")) == "card"


def test_load_name_keeps_a_spec_that_is_only_called_job(tmp_path):
    assert u1_cmd.load_name(_named_job(tmp_path, "job.json")) == "job"


def test_load_name_sanitises_a_hostile_name(tmp_path):
    job = _named_job(tmp_path, "x.json", name="../../etc/passwd")
    assert "/" not in u1_cmd.load_name(job)


def test_slice_passes_the_derived_name_down(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "job-card-3up.json")
    out = tmp_path / "out.gcode"
    seen = {}

    def fake_build_and_slice(parts, workdir, **kw):
        seen.update(kw)
        Path(out).write_text("; g\n")
        return U1JobResult(project_3mf=tmp_path / "p.3mf", gcode_path=out)

    monkeypatch.setattr(u1_cmd, "build_and_slice", fake_build_and_slice)
    rc = u1_cmd.cmd_u1(u1_cmd.Namespace(
        subcommand="slice", job=str(job), out=str(out), workdir=str(tmp_path / "w"),
        datadir="/dd", bin="orcaslicer", display=":99",
    ))
    assert rc == 0
    assert seen["name"] == "card-3up"
    assert seen["thumbnails"] is True


def test_an_explicit_name_flag_beats_the_spec(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "job-card-3up.json", name="from-spec")
    out = tmp_path / "out.gcode"
    seen = {}

    def fake_build_and_slice(parts, workdir, **kw):
        seen.update(kw)
        Path(out).write_text("; g\n")
        return U1JobResult(project_3mf=tmp_path / "p.3mf", gcode_path=out)

    monkeypatch.setattr(u1_cmd, "build_and_slice", fake_build_and_slice)
    u1_cmd.cmd_u1(u1_cmd.Namespace(
        subcommand="slice", job=str(job), out=str(out), workdir=str(tmp_path / "w"),
        datadir="/dd", bin="orcaslicer", display=":99", name="from-flag",
    ))
    assert seen["name"] == "from-flag"


def test_no_thumbnails_flag_is_threaded(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "card.json")
    out = tmp_path / "out.gcode"
    seen = {}

    def fake_build_and_slice(parts, workdir, **kw):
        seen.update(kw)
        Path(out).write_text("; g\n")
        return U1JobResult(project_3mf=tmp_path / "p.3mf", gcode_path=out)

    monkeypatch.setattr(u1_cmd, "build_and_slice", fake_build_and_slice)
    u1_cmd.cmd_u1(u1_cmd.Namespace(
        subcommand="slice", job=str(job), out=str(out), workdir=str(tmp_path / "w"),
        datadir="/dd", bin="orcaslicer", display=":99", no_thumbnails=True,
    ))
    assert seen["thumbnails"] is False


def test_print_uploads_under_the_derived_name(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "job-card-3up.json")
    seen = {}

    async def fake_push(parts, workdir, **kw):
        seen.update(kw)
        return U1JobResult(project_3mf=tmp_path / "p.3mf",
                           gcode_path=tmp_path / "card-3up.gcode",
                           pushed_as="card-3up.gcode")

    monkeypatch.setattr(u1_cmd, "build_slice_push", fake_push)
    rc = u1_cmd.cmd_u1(u1_cmd.Namespace(
        subcommand="print", job=str(job), workdir=str(tmp_path / "w"),
        moonraker="http://u1.local", api_key=None, mode="queue",
        datadir="/dd", bin="orcaslicer", display=":99",
    ))
    assert rc == 0
    assert seen["name"] == "card-3up"


# --- the date suffix -------------------------------------------------------

def _capture_name(tmp_path, monkeypatch, job, **arg_overrides):
    out = tmp_path / "out.gcode"
    seen = {}

    def fake_build_and_slice(parts, workdir, **kw):
        seen.update(kw)
        Path(out).write_text("; g\n")
        return U1JobResult(project_3mf=tmp_path / "p.3mf", gcode_path=out)

    monkeypatch.setattr(u1_cmd, "build_and_slice", fake_build_and_slice)
    args = dict(subcommand="slice", job=str(job), out=str(out),
                workdir=str(tmp_path / "w"), datadir="/dd", bin="orcaslicer",
                display=":99")
    args.update(arg_overrides)
    assert u1_cmd.cmd_u1(u1_cmd.Namespace(**args)) == 0
    return seen["name"]


def test_the_date_is_off_unless_asked_for(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "job-card-3up.json")
    assert _capture_name(tmp_path, monkeypatch, job) == "card-3up"


def test_a_job_spec_can_ask_for_the_date(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "job-card-3up.json", date_suffix=True)
    name = _capture_name(tmp_path, monkeypatch, job)
    assert re.fullmatch(r"card-3up\(\d{2}\.\d{2}\.\d{2}\)", name), name


def test_the_flag_turns_it_on_for_a_spec_that_does_not_ask(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "card.json")
    name = _capture_name(tmp_path, monkeypatch, job, date_suffix=True)
    assert name.startswith("card(")


def test_no_date_suffix_overrides_a_spec_that_asks(tmp_path, monkeypatch):
    # the spec is a default, the flag is an instruction
    job = _named_job(tmp_path, "card.json", date_suffix=True)
    assert _capture_name(tmp_path, monkeypatch, job, date_suffix=False) == "card"


def test_the_date_applies_to_an_explicit_name_too(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "card.json", date_suffix=True)
    name = _capture_name(tmp_path, monkeypatch, job, name="from-flag")
    assert name.startswith("from-flag(")


# --- print --timelapse ------------------------------------------------------

def _print_args(tmp_path, job, **overrides):
    args = dict(
        subcommand="print", job=str(job), workdir=str(tmp_path / "w"),
        moonraker="http://printer.local", api_key=None, mode="start",
        datadir="/dd", bin="orcaslicer", display=":99",
        timelapse=False, timelapse_interval=5.0,
    )
    args.update(overrides)
    return u1_cmd.Namespace(**args)


def _stub_successful_push(monkeypatch, tmp_path):
    async def fake_push(parts, workdir, **kw):
        return U1JobResult(project_3mf=tmp_path / "p.3mf",
                           gcode_path=tmp_path / "job.gcode",
                           pushed_as="job.gcode")
    monkeypatch.setattr(u1_cmd, "build_slice_push", fake_push)


def test_print_with_timelapse_spawns_recorder(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "job.json")
    _stub_successful_push(monkeypatch, tmp_path)
    seen = {}

    def fake_spawn(moonraker, out, *, api_key=None, interval=5.0, log_path=None):
        seen.update(moonraker=moonraker, out=out, api_key=api_key, interval=interval)

    monkeypatch.setattr(u1_cmd, "spawn_timelapse_recorder", fake_spawn)
    rc = u1_cmd.cmd_u1(_print_args(
        tmp_path, job, timelapse=True, timelapse_interval=3.0, api_key="secret",
    ))

    assert rc == 0
    assert seen["moonraker"] == "http://printer.local"
    assert seen["api_key"] == "secret"
    assert seen["interval"] == 3.0
    assert Path(seen["out"]).name == "job_timelapse.mp4"


def test_print_without_timelapse_flag_does_not_spawn(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "job.json")
    _stub_successful_push(monkeypatch, tmp_path)
    spawned = []
    monkeypatch.setattr(u1_cmd, "spawn_timelapse_recorder", lambda *a, **kw: spawned.append(1))

    rc = u1_cmd.cmd_u1(_print_args(tmp_path, job, timelapse=False))

    assert rc == 0
    assert spawned == []


def test_print_rejects_non_positive_timelapse_interval(tmp_path, monkeypatch):
    job = _named_job(tmp_path, "job.json")
    pushed = []
    monkeypatch.setattr(
        u1_cmd, "build_slice_push",
        lambda *a, **kw: pushed.append(1) or (_ for _ in ()).throw(AssertionError("should not push")),
    )
    spawned = []
    monkeypatch.setattr(u1_cmd, "spawn_timelapse_recorder", lambda *a, **kw: spawned.append(1))

    rc = u1_cmd.cmd_u1(_print_args(tmp_path, job, timelapse=True, timelapse_interval=0))

    assert rc == 1
    assert pushed == []
    assert spawned == []


def test_spawn_timelapse_recorder_puts_api_key_in_env_not_argv(monkeypatch, tmp_path):
    captured = {}

    class FakeProcess:
        pass

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(u1_cmd.subprocess, "Popen", fake_popen)
    u1_cmd.spawn_timelapse_recorder(
        "http://printer.local", tmp_path / "out.mp4", api_key="super-secret", interval=7.0,
    )

    assert "super-secret" not in captured["cmd"]
    assert captured["kwargs"]["env"]["MOONRAKER_API_KEY"] == "super-secret"


def test_spawn_timelapse_recorder_without_api_key_inherits_environment(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        u1_cmd.subprocess, "Popen",
        lambda cmd, **kwargs: captured.update(kwargs) or object(),
    )
    u1_cmd.spawn_timelapse_recorder("http://printer.local", tmp_path / "out.mp4")

    assert captured["env"] is None
