"""The agent's side of the dashboard's JSON API (reached through the SSH tunnel)."""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from jobcopilot.apply.models import FormSnapshot

HEADERS = {"X-Jobcopilot-Agent": "1"}


class DashboardUnreachable(Exception):
    """The tunnel is down or the dashboard is not running."""


class AgentAPI:
    def __init__(self, base_url: str, client: httpx.Client):
        self.base_url, self.client = base_url.rstrip("/"), client

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            response = self.client.request(method, self.base_url + path, headers=HEADERS, **kw)
        except httpx.TransportError as e:
            raise DashboardUnreachable(str(e)) from e
        if response.status_code != 404:
            response.raise_for_status()
        return response

    def next_command(self) -> dict | None:
        response = self._request("GET", "/api/agent/next")
        return None if response.status_code == 204 else response.json()

    def application(self, app_id: int) -> dict:
        response = self._request("GET", f"/api/applications/{app_id}")
        response.raise_for_status()
        return response.json()

    def download_cv(self, app_id: int, dest_dir: Path) -> Path | None:
        return self._download(f"/api/applications/{app_id}/cv.pdf", dest_dir, "CV.pdf")

    def download_cover(self, app_id: int, dest_dir: Path) -> Path | None:
        return self._download(f"/api/applications/{app_id}/cover.pdf", dest_dir, "Cover-Letter.pdf")

    def _download(self, path: str, dest_dir: Path, default: str) -> Path | None:
        response = self._request("GET", path, timeout=120)
        if response.status_code == 404:
            return None
        m = re.search(r'filename="([^"/\\]+)"', response.headers.get("content-disposition", ""))
        path = dest_dir / (m.group(1) if m else default)
        path.write_bytes(response.content)
        return path

    def post_form(self, app_id: int, snap: FormSnapshot) -> None:
        # Drafting takes Claude a minute or two.
        self._request("POST", f"/api/applications/{app_id}/form", json=snap.model_dump(), timeout=600).raise_for_status()

    def report(self, cmd_id: int, status: str, filled=(), unfilled=(), error: str | None = None) -> None:
        body = {"status": status, "filled": list(filled), "unfilled": list(unfilled), "error": error}
        self._request("POST", f"/api/agent/commands/{cmd_id}/result", json=body)
