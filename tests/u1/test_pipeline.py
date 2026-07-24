from pathlib import Path

import httpx
import pytest

from orca_api.u1 import pipeline as pl
from orca_api.u1.slice import SliceResult
from orca_api.u1.tool_map import ToolAssignment
from orca_api.u1.threemf_builder import U1Part


def _parts(tmp_path):
    stl = tmp_path / "cube.stl"
    stl.write_bytes(b"solid\nendsolid\n")
    return [U1Part(stl_path=str(stl),
                   assignment=ToolAssignment(tool_index=0, filament="PLA", color="#519F61"))]


def test_build_and_slice_wires_builder_then_slicer(tmp_path, monkeypatch):
    work = tmp_path / "work"

    def fake_build(parts, out_path):
        Path(out_path).write_bytes(b"PK\x03\x04project")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; sliced\nG28\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)

    result = pl.build_and_slice(_parts(tmp_path), work, datadir=Path("/dd"))
    assert result.project_3mf.exists()
    assert result.gcode_path.read_text().startswith("; sliced")
    assert result.pushed_as is None


@pytest.mark.asyncio
async def test_build_slice_push_uploads_to_moonraker(tmp_path, monkeypatch):
    work = tmp_path / "work"

    def fake_build(parts, out_path):
        Path(out_path).write_bytes(b"PK")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; g\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/server/files/upload":
            return httpx.Response(201, json={"item": {"path": "job.gcode"}})
        if request.url.path == "/server/job_queue/job":
            return httpx.Response(200, json={"result": {}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    result = await pl.build_slice_push(
        _parts(tmp_path), work,
        moonraker_url="http://u1.local", mode="queue",
        datadir=Path("/dd"), transport=transport,
    )
    assert result.pushed_as == "job.gcode"
    assert result.gcode_path.exists()
