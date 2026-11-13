"""The Stage's client of the Studio API (docs/PLAN.md section 5.3)."""

from pathlib import Path
from typing import Self

import httpx2
from core.studio_api import SongSummary
from pydantic import TypeAdapter, ValidationError

from stage.library import describe

_SONGS = TypeAdapter(list[SongSummary])
# Per operation (connect, each socket read, write, pool), never for a whole
# request: a stem streams as long as no single wait for data passes 30 s.
_TIMEOUT = httpx2.Timeout(30.0)
_RANGE_NOT_SATISFIABLE = 416
_UNREACHABLE = (httpx2.HTTPError, httpx2.InvalidURL)


class StudioError(Exception):
    """The Studio could not be reached, or answered with an error."""


class _RangeNotSatisfiable(Exception):
    pass


class StudioClient:
    """One HTTP client for a whole sync, so every request reuses the connection.

    http is the client to send through; without one, the StudioClient makes
    its own and closes it on close().
    """

    def __init__(
        self, base_url: str, token: str, http: httpx2.Client | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = {"Authorization": f"Bearer {token}"}
        self._owned = http is None
        self._http = httpx2.Client(timeout=_TIMEOUT) if http is None else http

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def songs(self) -> list[SongSummary]:
        raw = self._get("/songs")
        try:
            return _SONGS.validate_json(raw)
        except ValidationError as error:
            reason = describe(error)
            raise StudioError(
                f"the Studio sent an invalid song list ({reason})"
            ) from None

    def manifest(self, song_id: str) -> bytes:
        """The manifest.json bytes as the Studio stores them."""
        return self._get(f"/songs/{song_id}/manifest")

    def download(self, song_id: str, path: str, target: Path) -> int:
        """Download a package file to target and return the bytes received.

        A target already on disk is taken as the start of the file, and only
        the rest is asked for. A server that sends the whole file instead, or
        that has no rest to send, gets the file written again from zero.
        """
        url = f"/songs/{song_id}/files/{path}"
        size = target.stat().st_size if target.exists() else 0
        try:
            return self._stream(url, target, start=size)
        except _RangeNotSatisfiable:
            return self._stream(url, target, start=0)

    def _stream(self, url: str, target: Path, start: int) -> int:
        headers = self._auth | ({"Range": f"bytes={start}-"} if start else {})
        try:
            with self._http.stream(
                "GET", self.base_url + url, headers=headers
            ) as response:
                if start and response.status_code == _RANGE_NOT_SATISFIABLE:
                    raise _RangeNotSatisfiable
                _raise_for_status(response)
                mode = "ab" if response.status_code == 206 else "wb"
                received = 0
                with target.open(mode) as file:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
                        received += len(chunk)
                return received
        except _UNREACHABLE as error:
            raise StudioError(f"could not reach the Studio ({error})") from None

    def _get(self, url: str) -> bytes:
        try:
            response = self._http.get(self.base_url + url, headers=self._auth)
        except _UNREACHABLE as error:
            raise StudioError(f"could not reach the Studio ({error})") from None
        _raise_for_status(response)
        return response.content


def _raise_for_status(response: httpx2.Response) -> None:
    if response.is_success:
        return
    response.read()
    try:
        detail = response.json()["detail"]
    except (ValueError, KeyError, TypeError):
        detail = response.reason_phrase
    raise StudioError(f"the Studio answered {response.status_code}: {detail}")
