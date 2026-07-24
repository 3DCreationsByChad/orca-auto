import json
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
