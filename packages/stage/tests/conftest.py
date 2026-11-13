import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

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


# The template's track order. Index 0 of the web interface is the master.
TEMPLATE_TRACKS = (
    "Mic 1",
    "Mic 2",
    "Lead",
    "Backing",
    "Drums",
    "Bass",
    "Guitar",
    "Piano",
    "Other",
)
_CMD = "SET/EXTSTATE/karaoke/cmd/"
_ACK = "GET/EXTSTATE/karaoke/ack"
_TRACK_FIELDS = "1.000000\t0.000000\t-1500\t-1500\t1.000000\t0\t0\t0\t1\t0"
PLAYING, STOPPED = 1, 0
# Far more reads than any test needs: a follow loop that never sees the end
# fails here instead of hanging the suite.
MAX_TRANSPORT_READS = 1_000


def wire(value: str) -> str:
    """value as GET/EXTSTATE sends it: newline, tab and backslash escaped."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")


class FakeReaper:
    """REAPER's web interface and the karaoke listener, as a MockTransport.

    commands holds every web interface command in the order received (one
    request may carry several, split on ";"), and sent every listener command,
    decoded. acks(command) gives the ack values the GET/EXTSTATE polls after
    that command see, in order; the last one repeats. transport is the list of
    (playstate, position) the TRANSPORT reads return, in order; the last one
    repeats. on_transport, when set, runs before each TRANSPORT reply with the
    number of reads so far. A read past MAX_TRANSPORT_READS fails the test.
    """

    def __init__(self, tracks: tuple[str, ...] = TEMPLATE_TRACKS) -> None:
        self.tracks = list(tracks)
        self.muted: dict[int, bool] = {}
        self.commands: list[str] = []
        self.sent: list[dict] = []
        self.acks: Callable[[dict], list[str]] = lambda command: [
            json.dumps({"id": command["id"], "op": command["op"], "ok": True})
        ]
        self.transport: list[tuple[int, float]] = [(STOPPED, 0.0)]
        self.on_transport: Callable[[int], None] | None = None
        self.transport_reads = 0
        self._pending: list[str] = []

    def http(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handle))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.raw_path.decode("ascii")
        assert request.url.host == "127.0.0.1", request.url
        assert request.url.port == 8080, request.url
        assert path.startswith("/_/"), path
        lines = [self._reply(command) for command in path[3:].split(";")]
        return httpx2.Response(200, text="".join(lines))

    def _reply(self, command: str) -> str:
        self.commands.append(command)
        if command == "TRACK":
            names = ["MASTER", *self.tracks]
            return "".join(
                f"TRACK\t{index}\t{name}\t{8 if self.muted.get(index) else 0}"
                f"\t{_TRACK_FIELDS}\n"
                for index, name in enumerate(names)
            )
        if command == "TRANSPORT":
            self.transport_reads += 1
            if self.transport_reads > MAX_TRANSPORT_READS:
                raise AssertionError(
                    f"TRANSPORT read {self.transport_reads} times: the song never ends"
                )
            if self.on_transport is not None:
                self.on_transport(self.transport_reads)
            state, position = self.transport[
                min(self.transport_reads, len(self.transport)) - 1
            ]
            return f"TRANSPORT\t{state}\t{position:.6f}\t0\t0:00.000\t1.1.00\n"
        if command.startswith("SET/TRACK/"):
            _, _, index, field, value = command.split("/")
            assert field == "MUTE", command
            self.muted[int(index)] = value == "1"
            return ""
        if command.startswith(_CMD):
            sent = json.loads(unquote(command[len(_CMD) :]))
            self.sent.append(sent)
            self._pending = self.acks(sent)
            return ""
        if command == _ACK:
            value = self._pending[0] if self._pending else ""
            if len(self._pending) > 1:
                self._pending.pop(0)
            return f"EXTSTATE\tkaraoke\tack\t{wire(value)}\n"
        if command.isdigit():
            return ""
        raise AssertionError(f"unexpected command {command}")

    def ops(self) -> list[str]:
        """Listener ops, mutes and actions in the order they reached REAPER."""
        steps = []
        for command in self.commands:
            if command.startswith(_CMD):
                sent = json.loads(unquote(command[len(_CMD) :]))
                steps.append(sent["op"])
            elif command.startswith("SET/TRACK/") or command.isdigit():
                steps.append(command)
        return steps


@pytest.fixture
def reaper() -> FakeReaper:
    return FakeReaper()
