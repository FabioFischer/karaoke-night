import hashlib
import json
import shutil
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from stage.client import StudioClient
from studio.api import create_app
from studio.jobs import JobQueue

TOKEN = "test-token"
BASE_URL = "http://testserver"
CREATED_AT = "2026-11-04T20:00:00Z"


def _no_build(request, on_stage) -> None:
    raise AssertionError("no job expected")


class Studio:
    """The real Studio API over a temporary master library.

    Packages are written as raw JSON with placeholder stem bytes. requests and
    responses record every exchange of the clients it hands out.
    """

    def __init__(self, library: Path) -> None:
        self.library = library
        self.app = create_app(library, TOKEN, JobQueue(_no_build))
        self.requests: list[httpx2.Request] = []
        self.responses: list[httpx2.Response] = []

    def add(
        self,
        song_id: str,
        *,
        created_at: str = CREATED_AT,
        pipeline_version: int = 1,
        lyrics: bool = True,
        extra: dict | None = None,
        folder: str | None = None,
    ) -> str:
        package = self.library / (folder or song_id)
        shutil.rmtree(package, ignore_errors=True)
        (package / "stems").mkdir(parents=True)
        stems = {}
        for stem in ("lead", "other"):
            data = f"{stem} of {song_id} made {created_at}\n".encode() * 2_000
            (package / "stems" / f"{stem}.flac").write_bytes(data)
            stems[stem] = {
                "file": f"stems/{stem}.flac",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        manifest = {
            "schema": 1,
            "pipeline_version": pipeline_version,
            "id": song_id,
            "title": f"Title of {song_id}",
            "artist": "Artist Name",
            "duration_s": 245.3,
            "source": {"type": "youtube_music", "video_id": "XXXXXXXXXXX"},
            "gain_db": -2.5,
            "stems": stems,
            "models": {"vocals": "a", "karaoke": "b", "instrumental": "c"},
            "created_at": created_at,
            **(extra or {}),
        }
        if lyrics:
            (package / "lyrics.lrc").write_bytes(b"[00:01.00] Line one\r\n")
            manifest["lyrics"] = {
                "provider": "lrclib",
                "file": "lyrics.lrc",
                "synced": True,
            }
        (package / "manifest.json").write_bytes(
            json.dumps(manifest, indent=4).encode("utf-8")
        )
        return song_id

    def file(self, song_id: str, path: str) -> Path:
        return self.library / song_id / path

    def http(self) -> TestClient:
        client = TestClient(self.app)
        client.event_hooks = {
            "request": [self.requests.append],
            "response": [self.responses.append],
        }
        return client

    def client(self, http: httpx2.Client | None = None, token: str = TOKEN):
        return StudioClient(BASE_URL, token, http=http or self.http())

    def file_requests(self) -> list[tuple[str, str | None]]:
        """(path, Range header) of each file request, in order."""
        return [
            (request.url.path, request.headers.get("range"))
            for request in self.requests
            if "/files/" in request.url.path
        ]


@pytest.fixture
def studio(tmp_path: Path) -> Studio:
    library = tmp_path / "studio"
    library.mkdir()
    return Studio(library)


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """The Stage library, not created: the sync creates it."""
    return tmp_path / "stage"


@pytest.fixture
def refuse_rename(monkeypatch):
    """refuse_rename(*names): Path.rename of those folders fails like Windows
    does for a folder with an open file. Called with no names, it allows all again.
    """
    rename = Path.rename
    refused: set[str] = set()

    def guarded(self, target):
        if self.name in refused:
            raise PermissionError(5, "Access is denied", str(self))
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", guarded)

    def refuse(*names: str) -> None:
        refused.clear()
        refused.update(names)

    return refuse
