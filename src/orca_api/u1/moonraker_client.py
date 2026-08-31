"""Async Moonraker REST client for pushing G-code to the Snapmaker U1."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from orca_api.u1.loaded_filament import LoadedFilament, parse_filament_detect

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class MoonrakerClient:
    """Minimal async Moonraker client: upload, enqueue, start, status.

    Args:
        base_url: Moonraker base URL, e.g. "http://printer.local" or "http://192.168.1.50".
        api_key: Optional Moonraker API key (X-Api-Key header).
        transport: Optional httpx transport (used to inject a MockTransport in tests).
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"X-Api-Key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
        )

    async def __aenter__(self) -> "MoonrakerClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_status(self) -> dict[str, Any]:
        """Return Moonraker's queried objects (print_stats)."""
        resp = await self._client.get(
            "/printer/objects/query", params={"print_stats": ""}
        )
        resp.raise_for_status()
        return resp.json()["result"]["status"]

    async def list_webcams(self) -> list[dict[str, Any]]:
        """Return Moonraker's configured webcams (name, snapshot_url, etc.)."""
        resp = await self._client.get("/server/webcams/list")
        resp.raise_for_status()
        return resp.json()["result"]["webcams"]

    async def get_snapshot(self, url: str) -> bytes:
        """Fetch one JPEG frame from a webcam's `snapshot_url`.

        `url` is the full URL a webcam entry reports. It usually names this
        same printer on a different port, but a misconfigured or hostile
        webcam entry could point anywhere -- so the Moonraker API key only
        rides along when `url` shares this client's host.
        """
        same_host = httpx.URL(url).host == self._client.base_url.host
        headers = None if same_host else {"X-Api-Key": None}
        resp = await self._client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content

    async def get_loaded_filaments(self) -> list[LoadedFilament | None]:
        """Read the RFID tag of each loaded spool, one entry per tool.

        Untagged spools (and printers that don't report `filament_detect` at all)
        come back as `None` -- unknown, not empty.
        """
        resp = await self._client.get(
            "/printer/objects/query", params={"filament_detect": ""}
        )
        resp.raise_for_status()
        detect = resp.json()["result"]["status"].get("filament_detect") or {}
        return parse_filament_detect(detect.get("info") or [])

    async def upload_gcode(self, gcode_path: str, *, start: bool = False) -> str:
        """Upload a G-code file to Moonraker's gcodes root.

        Args:
            gcode_path: Local path to the G-code file.
            start: If True, Moonraker starts the print immediately after upload.

        Returns:
            The uploaded file's path as Moonraker reports it.
        """
        p = Path(gcode_path)
        with p.open("rb") as fh:
            files = {"file": (p.name, fh, "application/octet-stream")}
            data = {"root": "gcodes", "print": "true" if start else "false"}
            resp = await self._client.post(
                "/server/files/upload", files=files, data=data
            )
        resp.raise_for_status()
        return resp.json()["item"]["path"]

    async def enqueue(self, filename: str) -> None:
        """Add an already-uploaded G-code file to Moonraker's job queue."""
        resp = await self._client.post(
            "/server/job_queue/job", params={"filenames": filename}
        )
        resp.raise_for_status()

    async def push(self, gcode_path: str, *, mode: str = "queue") -> str:
        """Send G-code to the U1.

        Args:
            gcode_path: Local path to the sliced G-code.
            mode: "queue" (upload then add to the job queue) or "start"
                (upload and begin printing immediately).

        Returns:
            The uploaded file's Moonraker path.
        """
        if mode not in ("queue", "start"):
            raise ValueError(f"unknown push mode {mode!r} (expected 'queue' or 'start')")
        filename = await self.upload_gcode(gcode_path, start=(mode == "start"))
        if mode == "queue":
            await self.enqueue(filename)
        return filename
