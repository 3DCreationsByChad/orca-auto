import functools

import httpx
import pytest

from orca_api.u1.moonraker_client import MoonrakerClient
from orca_api.u1.timelapse import (
    TimelapseError,
    assemble_video,
    capture_frames,
    discover_snapshot_url,
    record_timelapse,
    wait_for_print_start,
)


def _client(handler) -> MoonrakerClient:
    transport = httpx.MockTransport(handler)
    return MoonrakerClient(base_url="http://printer.local", transport=transport)


async def test_discover_snapshot_url_picks_first_enabled():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"webcams": [
            {"name": "disabled cam", "enabled": False, "snapshot_url": "http://x/a.jpg"},
            {"name": "U1 Camera", "enabled": True, "snapshot_url": "http://printer.local:7125/server/files/camera/monitor.jpg"},
        ]}})

    async with _client(handler) as c:
        url = await discover_snapshot_url(c)

    assert url == "http://printer.local:7125/server/files/camera/monitor.jpg"


async def test_discover_snapshot_url_by_name():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"webcams": [
            {"name": "front", "snapshot_url": "http://x/front.jpg"},
            {"name": "top", "snapshot_url": "http://x/top.jpg"},
        ]}})

    async with _client(handler) as c:
        url = await discover_snapshot_url(c, webcam_name="top")

    assert url == "http://x/top.jpg"


async def test_discover_snapshot_url_unknown_name_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"webcams": [
            {"name": "front", "snapshot_url": "http://x/front.jpg"},
        ]}})

    async with _client(handler) as c:
        with pytest.raises(TimelapseError, match="front"):
            await discover_snapshot_url(c, webcam_name="nope")


async def test_discover_snapshot_url_no_webcams_raises():
    async with _client(lambda r: httpx.Response(200, json={"result": {"webcams": []}})) as c:
        with pytest.raises(TimelapseError, match="no configured webcams"):
            await discover_snapshot_url(c)


async def test_wait_for_print_start_returns_once_printing():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        state = "standby" if calls["n"] < 3 else "printing"
        return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": state}}}})

    async with _client(handler) as c:
        await wait_for_print_start(c, poll_s=0, timeout_s=10)

    assert calls["n"] == 3


async def test_wait_for_print_start_times_out():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": "standby"}}}})

    async with _client(handler) as c:
        with pytest.raises(TimelapseError, match="no print started"):
            await wait_for_print_start(c, poll_s=0, timeout_s=0)


async def test_capture_frames_stops_when_state_leaves_printing(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/printer/objects/query":
            calls["n"] += 1
            state = "printing" if calls["n"] <= 3 else "complete"
            return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": state}}}})
        return httpx.Response(200, content=b"\xff\xd8fake-jpeg")

    async with _client(handler) as c:
        count = await capture_frames(
            c, "http://printer.local/snap.jpg", tmp_path, interval_s=0, max_duration_s=100,
        )

    assert count == 3
    assert sorted(p.name for p in tmp_path.glob("frame_*.jpg")) == [
        "frame_000000.jpg", "frame_000001.jpg", "frame_000002.jpg",
    ]


async def test_capture_frames_respects_max_duration(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/printer/objects/query":
            return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": "printing"}}}})
        return httpx.Response(200, content=b"frame")

    async with _client(handler) as c:
        count = await capture_frames(
            c, "http://printer.local/snap.jpg", tmp_path, interval_s=1, max_duration_s=3,
        )

    assert count == 3


async def test_capture_frames_returns_zero_when_never_printing(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": "standby"}}}})

    async with _client(handler) as c:
        count = await capture_frames(
            c, "http://printer.local/snap.jpg", tmp_path, interval_s=0, max_duration_s=10,
        )

    assert count == 0
    assert list(tmp_path.glob("frame_*.jpg")) == []


async def test_capture_frames_clears_stale_frames_from_a_prior_run(tmp_path):
    (tmp_path / "frame_000000.jpg").write_bytes(b"old")
    (tmp_path / "frame_000001.jpg").write_bytes(b"old")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/printer/objects/query":
            return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": "complete"}}}})
        return httpx.Response(200, content=b"new")

    async with _client(handler) as c:
        count = await capture_frames(c, "http://printer.local/snap.jpg", tmp_path, interval_s=0)

    assert count == 0
    assert list(tmp_path.glob("frame_*.jpg")) == []


async def test_capture_frames_tolerates_a_few_transient_failures(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/printer/objects/query":
            calls["n"] += 1
            if calls["n"] == 2:
                return httpx.Response(502)
            state = "printing" if calls["n"] <= 4 else "complete"
            return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": state}}}})
        return httpx.Response(200, content=b"frame")

    async with _client(handler) as c:
        count = await capture_frames(
            c, "http://printer.local/snap.jpg", tmp_path,
            interval_s=0, max_consecutive_failures=2,
        )

    assert count == 3


async def test_capture_frames_gives_up_after_too_many_consecutive_failures(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/printer/objects/query":
            return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": "printing"}}}})
        return httpx.Response(502)

    async with _client(handler) as c:
        count = await capture_frames(
            c, "http://printer.local/snap.jpg", tmp_path,
            interval_s=0, max_duration_s=100, max_consecutive_failures=2,
        )

    assert count == 0


def test_assemble_video_raises_on_ffmpeg_failure(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    out = tmp_path / "out.mp4"

    with pytest.raises(TimelapseError, match="ffmpeg failed"):
        assemble_video(frames, out, ffmpeg_bin="false")


def test_assemble_video_invokes_ffmpeg_with_rotation_filter(tmp_path, monkeypatch):
    frames = tmp_path / "frames"
    frames.mkdir()
    out = tmp_path / "out.mp4"
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        class Result:
            returncode = 0
            stderr = ""
        return Result()

    monkeypatch.setattr("orca_api.u1.timelapse.subprocess.run", fake_run)
    assemble_video(frames, out, fps=24, rotate_180=True)

    assert "-vf" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("-vf") + 1] == "vflip,hflip"
    assert "24" in captured["cmd"]


def test_assemble_video_no_rotate_skips_flip(tmp_path, monkeypatch):
    frames = tmp_path / "frames"
    frames.mkdir()
    out = tmp_path / "out.mp4"
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        class Result:
            returncode = 0
            stderr = ""
        return Result()

    monkeypatch.setattr("orca_api.u1.timelapse.subprocess.run", fake_run)
    assemble_video(frames, out, rotate_180=False)

    assert captured["cmd"][captured["cmd"].index("-vf") + 1] == "null"


def _patch_moonraker_client(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        "orca_api.u1.timelapse.MoonrakerClient",
        functools.partial(MoonrakerClient, transport=httpx.MockTransport(handler)),
    )


async def test_record_timelapse_raises_when_no_print_starts(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/server/webcams/list":
            return httpx.Response(200, json={"result": {"webcams": [
                {"name": "cam", "snapshot_url": "http://printer.local/snap.jpg"},
            ]}})
        return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": "complete"}}}})

    _patch_moonraker_client(monkeypatch, handler)

    with pytest.raises(TimelapseError, match="no print started"):
        await record_timelapse(
            "http://printer.local", tmp_path / "out.mp4", wait_timeout_s=0, interval_s=1,
        )


async def test_record_timelapse_raises_on_too_few_frames(tmp_path, monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/server/webcams/list":
            return httpx.Response(200, json={"result": {"webcams": [
                {"name": "cam", "snapshot_url": "http://printer.local/snap.jpg"},
            ]}})
        if request.url.path == "/printer/objects/query":
            calls["n"] += 1
            # call 1 is wait_for_print_start's own check; call 2 is capture_frames'
            # first iteration -- one "printing" reading there yields exactly one frame.
            state = "printing" if calls["n"] <= 2 else "complete"
            return httpx.Response(200, json={"result": {"status": {"print_stats": {"state": state}}}})
        return httpx.Response(200, content=b"one-frame")

    _patch_moonraker_client(monkeypatch, handler)

    with pytest.raises(TimelapseError, match="only captured 1 frame"):
        await record_timelapse(
            "http://printer.local", tmp_path / "out.mp4", wait_timeout_s=1, interval_s=0.01,
        )


async def test_record_timelapse_rejects_non_positive_interval(tmp_path):
    with pytest.raises(TimelapseError, match="interval_s must be positive"):
        await record_timelapse("http://printer.local", tmp_path / "out.mp4", interval_s=0)
