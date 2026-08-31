"""Client-side timelapse capture for the Snapmaker U1.

The U1's own G-code carries the standard `TIMELAPSE_START` / `TIMELAPSE_TAKE_FRAME`
Klipper macros -- confirmed byte-identical to a native Snapmaker-slicer export --
but a job pushed directly over Moonraker never arms them: klippy.log shows
`[timelapse] not started!` at print end. Starting the same job from the
Snapmaker app or printer screen does work, which points at the toggle living in
that start-print flow rather than in the G-code. This module works around it
by polling the printer's own webcam snapshot endpoint independently of
whatever native recording state the printer thinks it's in.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from orca_api.u1.moonraker_client import MoonrakerClient

MAX_CONSECUTIVE_FETCH_FAILURES = 5

ACTIVE_STATES = {"printing"}


class TimelapseError(Exception):
    """Capture or encode failed."""


@dataclass(frozen=True)
class TimelapseResult:
    video_path: Path
    frame_count: int
    duration_s: float


async def discover_snapshot_url(client: MoonrakerClient, webcam_name: str | None = None) -> str:
    """The `snapshot_url` of the printer's webcam, by name or the first enabled one.

    Raises:
        TimelapseError: no webcams are configured, or `webcam_name` matches none.
    """
    webcams = await client.list_webcams()
    if not webcams:
        raise TimelapseError("printer reports no configured webcams")
    if webcam_name is not None:
        for cam in webcams:
            if cam.get("name") == webcam_name:
                return cam["snapshot_url"]
        names = [cam.get("name") for cam in webcams]
        raise TimelapseError(f"no webcam named {webcam_name!r} -- have {names}")
    enabled = [cam for cam in webcams if cam.get("enabled", True)]
    return (enabled or webcams)[0]["snapshot_url"]


async def wait_for_print_start(
    client: MoonrakerClient, *, poll_s: float, timeout_s: float
) -> None:
    """Block until `print_stats.state` is `printing`.

    Times against a monotonic clock, not a sum of `poll_s` -- the status
    request itself takes real time too, which a running total ignores.

    Raises:
        TimelapseError: nothing started within `timeout_s`.
    """
    start = time.monotonic()
    while True:
        status = await client.get_status()
        if status.get("print_stats", {}).get("state") in ACTIVE_STATES:
            return
        if time.monotonic() - start >= timeout_s:
            raise TimelapseError(f"no print started within {timeout_s:.0f}s")
        await asyncio.sleep(poll_s)


async def capture_frames(
    client: MoonrakerClient,
    snapshot_url: str,
    frames_dir: Path,
    *,
    interval_s: float = 5.0,
    max_duration_s: float = 6 * 3600,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FETCH_FAILURES,
) -> int:
    """Save one frame every `interval_s` while the printer reports `printing`.

    Stops the moment the state leaves `printing` (done, cancelled, errored),
    `max_duration_s` of wall-clock time elapses, or a status/snapshot request
    fails `max_consecutive_failures` times in a row -- a printer's embedded
    HTTP service dropping one request mid-print shouldn't throw away every
    frame captured so far, but a printer that's gone for good should still
    let the caller move on to `assemble_video` instead of hanging forever.

    Clears any frames already sitting in `frames_dir` before starting, so a
    retry after a failed run can't splice stale frames from a different print
    into the assembled video.

    Returns:
        The number of frames captured.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("frame_*.jpg"):
        stale.unlink()

    seq = 0
    consecutive_failures = 0
    start = time.monotonic()
    while time.monotonic() - start < max_duration_s:
        try:
            status = await client.get_status()
        except httpx.HTTPError:
            consecutive_failures += 1
            if consecutive_failures > max_consecutive_failures:
                break
            await asyncio.sleep(interval_s)
            continue
        if status.get("print_stats", {}).get("state") not in ACTIVE_STATES:
            break
        try:
            frame = await client.get_snapshot(snapshot_url)
        except httpx.HTTPError:
            consecutive_failures += 1
            if consecutive_failures > max_consecutive_failures:
                break
            await asyncio.sleep(interval_s)
            continue
        consecutive_failures = 0
        (frames_dir / f"frame_{seq:06d}.jpg").write_bytes(frame)
        seq += 1
        await asyncio.sleep(interval_s)
    return seq


def assemble_video(
    frames_dir: Path,
    out_path: Path,
    *,
    fps: int = 30,
    rotate_180: bool = True,
    ffmpeg_bin: str = "ffmpeg",
) -> None:
    """Stitch numbered frames into an mp4.

    Raises:
        TimelapseError: ffmpeg exited non-zero.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin, "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%06d.jpg"),
        "-vf", "vflip,hflip" if rotate_180 else "null",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TimelapseError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-2000:]}")


def _clear_frames(frames_dir: Path, *, remove_dir: bool) -> None:
    for frame in frames_dir.glob("frame_*.jpg"):
        frame.unlink()
    if remove_dir:
        frames_dir.rmdir()


async def record_timelapse(
    moonraker_url: str,
    out_path: Path | str,
    *,
    api_key: str | None = None,
    webcam_name: str | None = None,
    interval_s: float = 5.0,
    fps: int = 30,
    rotate_180: bool = True,
    wait_timeout_s: float = 1800.0,
    max_duration_s: float = 6 * 3600,
    frames_dir: Path | str | None = None,
    keep_frames: bool = False,
    ffmpeg_bin: str = "ffmpeg",
) -> TimelapseResult:
    """Wait for a print to start, capture it, and assemble the timelapse.

    Raises:
        TimelapseError: `interval_s` isn't positive, no webcam is configured,
            no print started in time, too few frames were captured to
            encode, or ffmpeg failed.
    """
    if interval_s <= 0:
        raise TimelapseError(f"interval_s must be positive, got {interval_s}")
    out_path = Path(out_path)
    own_frames_dir = frames_dir is None
    resolved_frames_dir = (
        Path(frames_dir) if frames_dir is not None
        else out_path.parent / f"{out_path.stem}_frames"
    )

    async with MoonrakerClient(moonraker_url, api_key=api_key) as client:
        snapshot_url = await discover_snapshot_url(client, webcam_name)
        await wait_for_print_start(
            client, poll_s=min(interval_s, 5.0), timeout_s=wait_timeout_s
        )
        frame_count = await capture_frames(
            client, snapshot_url, resolved_frames_dir,
            interval_s=interval_s, max_duration_s=max_duration_s,
        )

    if frame_count < 2:
        raise TimelapseError(f"only captured {frame_count} frame(s), nothing to assemble")

    assemble_video(
        resolved_frames_dir, out_path,
        fps=fps, rotate_180=rotate_180, ffmpeg_bin=ffmpeg_bin,
    )

    if not keep_frames:
        _clear_frames(resolved_frames_dir, remove_dir=own_frames_dir)

    return TimelapseResult(
        video_path=out_path, frame_count=frame_count, duration_s=frame_count * interval_s,
    )
