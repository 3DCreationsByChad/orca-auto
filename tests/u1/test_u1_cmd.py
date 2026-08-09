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
