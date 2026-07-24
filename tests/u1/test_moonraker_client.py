import httpx
import pytest

from orca_api.u1.moonraker_client import MoonrakerClient


def _client(handler) -> MoonrakerClient:
    transport = httpx.MockTransport(handler)
    return MoonrakerClient(base_url="http://printer.local", transport=transport)


async def test_get_status_returns_print_stats():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/printer/objects/query"
        return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": "standby"}}}})

    async with _client(handler) as c:
        status = await c.get_status()
    assert status["print_stats"]["state"] == "standby"


async def test_upload_gcode_posts_multipart(tmp_path):
    gcode = tmp_path / "part.gcode"
    gcode.write_text("G28\n")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(201, json={"item": {"path": "part.gcode"}})

    async with _client(handler) as c:
        result = await c.upload_gcode(str(gcode), start=False)

    assert captured["path"] == "/server/files/upload"
    assert b"part.gcode" in captured["body"]
    assert b'name="print"' in captured["body"]
    assert b"false" in captured["body"]
    assert result == "part.gcode"


async def test_upload_gcode_start_true_sets_print_flag(tmp_path):
    gcode = tmp_path / "part.gcode"
    gcode.write_text("G28\n")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(201, json={"item": {"path": "part.gcode"}})

    async with _client(handler) as c:
        await c.upload_gcode(str(gcode), start=True)

    assert b"true" in captured["body"]


async def test_enqueue_posts_filename():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"result": {"queued_jobs": []}})

    async with _client(handler) as c:
        await c.enqueue("part.gcode")

    assert captured["path"] == "/server/job_queue/job"
    assert captured["params"]["filenames"] == "part.gcode"


async def test_push_queue_uploads_then_enqueues(tmp_path):
    gcode = tmp_path / "part.gcode"
    gcode.write_text("G28\n")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/server/files/upload":
            return httpx.Response(201, json={"item": {"path": "part.gcode"}})
        return httpx.Response(200, json={"result": {"queued_jobs": []}})

    async with _client(handler) as c:
        await c.push(str(gcode), mode="queue")

    assert calls == ["/server/files/upload", "/server/job_queue/job"]


async def test_push_start_uploads_with_print_flag_only(tmp_path):
    gcode = tmp_path / "part.gcode"
    gcode.write_text("G28\n")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(201, json={"item": {"path": "part.gcode"}})

    async with _client(handler) as c:
        await c.push(str(gcode), mode="start")

    assert calls == ["/server/files/upload"]


async def test_push_rejects_unknown_mode(tmp_path):
    gcode = tmp_path / "part.gcode"
    gcode.write_text("G28\n")

    async with _client(lambda r: httpx.Response(200, json={})) as c:
        with pytest.raises(ValueError, match="mode"):
            await c.push(str(gcode), mode="bogus")


async def test_upload_raises_on_http_error(tmp_path):
    gcode = tmp_path / "part.gcode"
    gcode.write_text("G28\n")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "disk full"})

    async with _client(handler) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await c.upload_gcode(str(gcode))
