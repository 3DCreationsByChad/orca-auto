import subprocess
import zipfile
from pathlib import Path

import pytest

from orca_api.u1.slice import SliceError, slice_3mf, _build_command


def _fake_completed(cmd, returncode=0, stdout="ok", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def _write_sliced_3mf_from_cmd(cmd, gcode_body="; fake\nG28\nG92 E0\n"):
    """Emulate OrcaSlicer: write an output .3mf containing an embedded gcode."""
    out = Path(cmd[cmd.index("--export-3mf") + 1])
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("Metadata/plate_1.gcode", gcode_body)


def test_build_command_uses_datadir_and_project_3mf(tmp_path):
    in_3mf = tmp_path / "job.3mf"
    out_3mf = tmp_path / "sliced.3mf"
    cmd = _build_command(in_3mf, out_3mf, Path("/dd"), "orcaslicer", True)
    assert cmd[0] == "orcaslicer"
    assert "--datadir" in cmd and cmd[cmd.index("--datadir") + 1] == "/dd"
    assert "--arrange" in cmd and "--slice" in cmd
    assert "--export-3mf" in cmd and cmd[cmd.index("--export-3mf") + 1] == str(out_3mf)
    assert "--allow-newer-file" in cmd
    assert cmd[-1] == str(in_3mf)  # input .3mf is the positional last arg


def test_slice_3mf_extracts_embedded_gcode(tmp_path, monkeypatch):
    in_3mf = tmp_path / "job.3mf"
    in_3mf.write_bytes(b"PK\x03\x04fake-project")
    out_gcode = tmp_path / "job.gcode"

    def fake_run(cmd, **kw):
        _write_sliced_3mf_from_cmd(cmd)
        return _fake_completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = slice_3mf(in_3mf, out_gcode, datadir=Path("/dd"))
    assert result.gcode_path == out_gcode
    assert out_gcode.read_text().startswith("; fake")
    assert result.output_3mf.exists()  # the sliced .3mf is kept alongside the gcode


def test_slice_3mf_raises_on_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        slice_3mf(tmp_path / "nope.3mf", tmp_path / "o.gcode", datadir=Path("/dd"))


def test_slice_3mf_raises_when_slicer_fails(tmp_path, monkeypatch):
    in_3mf = tmp_path / "job.3mf"
    in_3mf.write_bytes(b"x")

    def fake_run(cmd, **kw):
        return _fake_completed(cmd, returncode=1, stdout="", stderr="process not compatible")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SliceError) as ei:
        slice_3mf(in_3mf, tmp_path / "o.gcode", datadir=Path("/dd"))
    assert "process not compatible" in str(ei.value)


def test_slice_3mf_raises_when_no_gcode_in_output(tmp_path, monkeypatch):
    in_3mf = tmp_path / "job.3mf"
    in_3mf.write_bytes(b"x")

    def fake_run(cmd, **kw):
        out = Path(cmd[cmd.index("--export-3mf") + 1])
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("Metadata/other.config", "{}")
        return _fake_completed(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SliceError) as ei:
        slice_3mf(in_3mf, tmp_path / "o.gcode", datadir=Path("/dd"))
    assert "no .gcode" in str(ei.value).lower()
