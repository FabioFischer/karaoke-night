import hashlib
import json
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

import httpx2
import pytest
from fastapi.testclient import TestClient
from stage.client import StudioClient
from stage.store import Store
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


class LocalLibrary:
    """A Stage library folder of packages that hold only their manifest."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def add(
        self,
        song_id: str,
        title: str,
        artist: str,
        *,
        video_id: str = "XXXXXXXXXXX",
        source: dict | None = None,
        duration_s: float = 245.3,
    ) -> str:
        package = self.path / song_id
        package.mkdir(parents=True)
        manifest = {
            "schema": 1,
            "pipeline_version": 1,
            "id": song_id,
            "title": title,
            "artist": artist,
            "duration_s": duration_s,
            "source": source or {"type": "youtube_music", "video_id": video_id},
            "gain_db": -2.5,
            "stems": {},
            "models": {"vocals": "a", "karaoke": "b", "instrumental": "c"},
            "created_at": CREATED_AT,
        }
        (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return song_id


@pytest.fixture
def local_library(tmp_path: Path) -> LocalLibrary:
    """An empty Stage library, not created until a song is added."""
    return LocalLibrary(tmp_path / "library")


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


STEMS = ("lead", "backing", "drums", "bass", "guitar", "piano", "other")


class Packages:
    """A Stage library of packages of the 7 stems with placeholder hashes."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def add(
        self,
        song_id: str,
        *,
        title: str = "Song Title",
        video_id: str = "XXXXXXXXXXX",
        duration_s: float = 245.3,
        lyrics: str | None = None,
    ) -> str:
        """Synced lyrics are written when given."""
        return _add_package(self.path, song_id, title, video_id, duration_s, lyrics)


@pytest.fixture
def packages(tmp_path: Path) -> Packages:
    return Packages(tmp_path / "songs")


def _add_package(
    library: Path,
    song_id: str,
    title: str,
    video_id: str,
    duration_s: float,
    lyrics: str | None,
) -> str:
    package = library / song_id
    package.mkdir(parents=True)
    manifest = {
        "schema": 1,
        "pipeline_version": 1,
        "id": song_id,
        "title": title,
        "artist": "Artist Name",
        "duration_s": duration_s,
        "source": {"type": "youtube_music", "video_id": video_id},
        "gain_db": 0.0,
        "stems": {
            stem: {"file": f"stems/{stem}.flac", "sha256": "0" * 64} for stem in STEMS
        },
        "models": {"vocals": "a", "karaoke": "b", "instrumental": "c"},
        "created_at": CREATED_AT,
    }
    if lyrics is not None:
        (package / "lyrics.lrc").write_text(lyrics, encoding="utf-8")
        manifest["lyrics"] = {
            "provider": "lrclib",
            "file": "lyrics.lrc",
            "synced": True,
        }
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return song_id


class FakePlayer:
    """A Player that records its calls and catches two calls at once.

    position() returns positions in order, the last one repeating; ended()
    turns true once positions are used up, when end is set. failures maps a
    method name to the exception it raises. Each call holds for delay_s, so
    a call made while another runs shows up in overlaps.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.positions: list[float] = [0.0]
        self.end = False
        self.failures: dict[str, Exception] = {}
        self.delay_s = 0.0
        self.overlaps = 0
        self._reads = 0
        self._active = 0
        self._counter = threading.Lock()

    def _call(self, name: str, *args) -> None:
        with self._counter:
            self._active += 1
            if self._active > 1:
                self.overlaps += 1
        try:
            if self.delay_s:
                time.sleep(self.delay_s)
            # One lookup: a test may remove the failure while a thread calls.
            failure = self.failures.get(name)
            if failure is not None:
                raise failure
            self.calls.append((name, *args))
        finally:
            with self._counter:
                self._active -= 1

    def load(self, manifest: Path) -> None:
        self._call("load", manifest)

    def play(self) -> None:
        self._reads = 0
        self._call("play")

    def stop(self) -> None:
        self._call("stop")

    def set_mute(self, stem: str, muted: bool) -> None:
        self._call("set_mute", stem, muted)

    def transpose(self, semitones: int) -> None:
        self._call("transpose", semitones)

    def position(self) -> float:
        self._call("position")
        self._reads += 1
        return self.positions[min(self._reads, len(self.positions)) - 1]

    def ended(self) -> bool:
        self._call("ended")
        return self.end and self._reads >= len(self.positions)

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture
def player() -> FakePlayer:
    return FakePlayer()


STORE_URL = "https://store.invalid"
STORE_TOKEN = "test-store-token"


class FakeStore:
    """The Upstash Redis REST API over a dict, as a MockTransport.

    It knows the commands the Stage sends: SET (with EX), GET, DEL, LPOP,
    RPUSH and TIME. commands holds every command received, values the
    strings stored, expiry the EX of each key set with one. now_ms is what
    TIME answers. down makes every request fail as an unreachable host does,
    and on_command, when set, runs before each command with it.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expiry: dict[str, int] = {}
        self.commands: list[list] = []
        self.now_ms = 1_700_000_000_000.0
        self.down = False
        self.on_command: Callable[[list], None] | None = None

    def http(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handle))

    def client(self, token: str = STORE_TOKEN) -> Store:
        return Store(STORE_URL, token, http=self.http())

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.down:
            raise httpx2.ConnectError("the store is down", request=request)
        assert str(request.url) == STORE_URL, request.url
        assert request.method == "POST"
        if request.headers.get("authorization") != f"Bearer {STORE_TOKEN}":
            return httpx2.Response(401, json={"error": "Unauthorized"})
        command = json.loads(request.content)
        self.commands.append(command)
        if self.on_command is not None:
            self.on_command(command)
        return httpx2.Response(200, json={"result": self._run(*command)})

    def _run(self, name: str, *args):
        if name == "SET":
            key, value, *options = args
            self.values[key] = value
            self.expiry.pop(key, None)
            if options:
                assert options[0] == "EX", options
                self.expiry[key] = options[1]
            return "OK"
        if name == "GET":
            return self.values.get(args[0])
        if name == "DEL":
            found = args[0] in self.values or args[0] in self.lists
            self.values.pop(args[0], None)
            self.lists.pop(args[0], None)
            return int(found)
        if name == "LPOP":
            items = self.lists.get(args[0])
            return items.pop(0) if items else None
        if name == "RPUSH":
            self.lists.setdefault(args[0], []).extend(args[1:])
            return len(self.lists[args[0]])
        if name == "TIME":
            seconds, micro = divmod(round(self.now_ms * 1000), 1_000_000)
            return [str(seconds), str(micro)]
        raise AssertionError(f"unexpected command {name}")

    def json(self, key: str):
        return json.loads(self.values[key])

    def names(self) -> list[str]:
        return [command[0] for command in self.commands]


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()
