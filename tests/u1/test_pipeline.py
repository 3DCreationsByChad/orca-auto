from pathlib import Path

import httpx
import pytest

from orca_api.u1 import pipeline as pl
from orca_api.u1.slice import SliceResult
from orca_api.u1.thumbnail import PreviewError
from orca_api.u1.tool_map import ToolAssignment
from orca_api.u1.threemf_builder import U1Part


@pytest.fixture(autouse=True)
def _no_real_openscad(monkeypatch):
    """Keep the preview render out of unit tests.

    `build_and_slice` embeds a thumbnail by default, which otherwise shells out
    to a real OpenSCAD -- a hidden dependency that makes these tests pass or
    fail on what happens to be installed. Tests that care about the preview
    stub this themselves.
    """
    monkeypatch.setattr(pl, "render_thumbnails", lambda *a, **kw: [])


def _parts(tmp_path):
    stl = tmp_path / "cube.stl"
    stl.write_bytes(b"solid\nendsolid\n")
    return [U1Part(stl_path=str(stl),
                   assignment=ToolAssignment(tool_index=0, filament="PLA", color="#519F61"))]


def test_build_and_slice_wires_builder_then_slicer(tmp_path, monkeypatch):
    work = tmp_path / "work"

    def fake_build(parts, out_path, datadir, *a, **kw):
        # the builder resolves filament presets out of the datadir, so the
        # pipeline must hand its datadir down -- not just to the slicer
        assert Path(datadir) == Path("/dd")
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

    def fake_build(parts, out_path, datadir, *a, **kw):
        Path(out_path).write_bytes(b"PK")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; g\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/printer/objects/query":
            # a printer whose spools carry no RFID tags
            return httpx.Response(200, json={"result": {"status": {}}})
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


async def test_push_reads_loaded_filaments_from_the_printer_before_building(tmp_path):
    """The printer is the authority on what is loaded, so ask it before slicing --
    not after, when the temperatures are already baked into the G-code."""
    work = tmp_path / "work"
    seen = {}

    def fake_build(parts, out_path, datadir, *a, **kw):
        seen["loaded"] = kw.get("loaded")
        Path(out_path).write_bytes(b"PK")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; g\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/printer/objects/query":
            return httpx.Response(200, json={"result": {"status": {"filament_detect": {"info": [
                {"MAIN_TYPE": "PLA", "SUB_TYPE": "SnapSpeed", "ARGB_COLOR": 0xFF080A0D,
                 "FIRST_LAYER_TEMP": 230, "OTHER_LAYER_TEMP": 220, "BED_TEMP": 60,
                 "HOTEND_MIN_TEMP": 190, "HOTEND_MAX_TEMP": 230, "VENDOR": "Snapmaker"},
            ]}}}})
        if request.url.path == "/server/files/upload":
            return httpx.Response(201, json={"item": {"path": "job.gcode"}})
        return httpx.Response(200, json={"result": "ok"})

    try:
        await pl.build_slice_push(
            _parts(tmp_path), work,
            moonraker_url="http://u1.local", mode="queue",
            transport=httpx.MockTransport(handler),
        )
    finally:
        monkeypatch.undo()

    assert seen["loaded"] is not None
    assert seen["loaded"][0].first_layer_temp == 230


async def test_read_filament_can_be_turned_off(tmp_path):
    work = tmp_path / "work"
    seen = {}

    def fake_build(parts, out_path, datadir, *a, **kw):
        seen["loaded"] = kw.get("loaded")
        Path(out_path).write_bytes(b"PK")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; g\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/printer/objects/query", "should not query filaments"
        if request.url.path == "/server/files/upload":
            return httpx.Response(201, json={"item": {"path": "job.gcode"}})
        return httpx.Response(200, json={"result": "ok"})

    try:
        await pl.build_slice_push(
            _parts(tmp_path), work,
            moonraker_url="http://u1.local", mode="queue", read_filament=False,
            transport=httpx.MockTransport(handler),
        )
    finally:
        monkeypatch.undo()

    assert seen["loaded"] is None


def test_the_job_name_reaches_both_intermediates(tmp_path, monkeypatch):
    """Every job used to land as job.gcode, so each one overwrote the last on the
    printer and the file list held one unidentifiable entry."""
    def fake_build(parts, out_path, datadir, *a, **kw):
        Path(out_path).write_bytes(b"PK")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; sliced\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)

    result = pl.build_and_slice(_parts(tmp_path), tmp_path / "w", name="card-3up")
    assert result.gcode_path.name == "card-3up.gcode"
    assert result.project_3mf.name == "card-3up.3mf"


def test_a_hostile_job_name_cannot_write_outside_the_workdir(tmp_path, monkeypatch):
    work = tmp_path / "w"

    def fake_build(parts, out_path, datadir, *a, **kw):
        Path(out_path).write_bytes(b"PK")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; sliced\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)

    result = pl.build_and_slice(_parts(tmp_path), work, name="../../etc/passwd")
    assert result.gcode_path.parent == work


def test_the_preview_is_embedded_in_the_sliced_gcode(tmp_path, monkeypatch):
    def fake_build(parts, out_path, datadir, *a, **kw):
        Path(out_path).write_bytes(b"PK")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; HEADER_BLOCK_END\nG28\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)
    monkeypatch.setattr(pl, "render_thumbnails", lambda *a, **kw: [(b"\x89PNG", 48, 48)])

    result = pl.build_and_slice(_parts(tmp_path), tmp_path / "w")
    assert "; thumbnail begin 48x48" in result.gcode_path.read_text()


def test_a_failed_preview_does_not_lose_the_slice(tmp_path, monkeypatch):
    """The slice is the expensive part and the print is the point. A missing
    picture costs a picture; a raised exception costs the job."""
    def fake_build(parts, out_path, datadir, *a, **kw):
        Path(out_path).write_bytes(b"PK")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; HEADER_BLOCK_END\nG28\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    def boom(*a, **kw):
        raise PreviewError("no GL context")

    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)
    monkeypatch.setattr(pl, "render_thumbnails", boom)

    result = pl.build_and_slice(_parts(tmp_path), tmp_path / "w")
    assert result.gcode_path.read_text() == "; HEADER_BLOCK_END\nG28\n"


def test_thumbnails_can_be_turned_off(tmp_path, monkeypatch):
    called = []

    def fake_build(parts, out_path, datadir, *a, **kw):
        Path(out_path).write_bytes(b"PK")
        return Path(out_path)

    def fake_slice(in_3mf, out_gcode, **kw):
        Path(out_gcode).write_text("; g\n")
        return SliceResult(gcode_path=Path(out_gcode), output_3mf=Path(str(out_gcode) + ".3mf"))

    monkeypatch.setattr(pl, "build_u1_3mf", fake_build)
    monkeypatch.setattr(pl, "slice_3mf", fake_slice)
    monkeypatch.setattr(pl, "render_thumbnails", lambda *a, **kw: called.append(1) or [])

    pl.build_and_slice(_parts(tmp_path), tmp_path / "w", thumbnails=False)
    assert called == []
