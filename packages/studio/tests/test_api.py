import hashlib
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from core.manifest import Manifest
from fastapi.testclient import TestClient
from studio import cli
from studio import library as lib
from studio.api import create_app
from studio.jobs import JobQueue
from studio.package import song_id

TOKEN = "test-token"
VIDEO_ID = "XXXXXXXXXXX"
OTHER_ID = "YYYYYYYYYYY"
NOW = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)


def job_body(video_id: str = VIDEO_ID, **extra) -> dict:
    return {
        "source": {"type": "youtube_music", "video_id": video_id},
        "title": "Song Title",
        "artist": "Artist Name",
        **extra,
    }


def make_package(
    library: Path, title: str, artist: str, video_id: str, extra: dict | None = None
) -> str:
    """A package on disk with placeholder stem bytes, written as raw JSON."""
    package_id = song_id(title, artist, video_id)
    folder = library / package_id
    (folder / "stems").mkdir(parents=True)
    stems = {}
    for stem in ("lead", "other"):
        data = f"{stem} placeholder bytes for {package_id}".encode() * 8
        (folder / "stems" / f"{stem}.flac").write_bytes(data)
        stems[stem] = {
            "file": f"stems/{stem}.flac",
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    (folder / "lyrics.lrc").write_bytes(b"[00:01.00] Line one\n")
    manifest = {
        "schema": 1,
        "pipeline_version": 1,
        "id": package_id,
        "title": title,
        "artist": artist,
        "duration_s": 245.3,
        "source": {"type": "youtube_music", "video_id": video_id},
        "gain_db": -2.5,
        "stems": stems,
        "lyrics": {"provider": "lrclib", "file": "lyrics.lrc", "synced": True},
        "models": {"vocals": "a", "karaoke": "b", "instrumental": "c"},
        "created_at": "2026-11-04T20:00:00Z",
        **(extra or {}),
    }
    (folder / "manifest.json").write_bytes(json.dumps(manifest).encode("utf-8"))
    return package_id


@pytest.fixture
def library(tmp_path: Path) -> Path:
    path = tmp_path / "library"
    path.mkdir()
    return path


@pytest.fixture
def queue(library: Path, fake_tools) -> JobQueue:
    build = cli.song_builder(library, Path("models"), fake_tools.tools())
    return JobQueue(build, clock=lambda: NOW)


@pytest.fixture
def client(library: Path, queue: JobQueue) -> TestClient:
    app = create_app(library, TOKEN, queue)
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


ROUTES = [
    ("POST", "/jobs"),
    ("GET", "/jobs/some-job"),
    ("GET", "/songs"),
    ("GET", "/songs/some-song/manifest"),
    ("GET", "/songs/some-song/files/stems/lead.flac"),
    ("DELETE", "/songs/some-song"),
    ("DELETE", "/songs"),
]


@pytest.mark.parametrize(("method", "url"), ROUTES)
@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer wrong-token",
        f"Basic {TOKEN}",
        TOKEN,
        "Bearer ",
        "Bearer tést".encode(),
    ],
    ids=["missing", "wrong", "other-scheme", "no-scheme", "empty", "non-ascii"],
)
def test_every_route_needs_the_token(
    library: Path, queue: JobQueue, method: str, url: str, authorization
) -> None:
    make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    client = TestClient(create_app(library, TOKEN, queue))
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.request(method, url, headers=headers, json=job_body())

    assert response.status_code == 401
    assert response.json() == {"detail": "missing or wrong token"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert len(list(library.iterdir())) == 1


def test_docs_routes_are_off(client: TestClient) -> None:
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(url).status_code == 404


def test_new_job_is_queued_and_runs_to_a_package(
    client: TestClient, queue: JobQueue, library: Path, fake_tools
) -> None:
    response = client.post("/jobs", json=job_body())

    assert response.status_code == 200
    created = response.json()
    expected_id = song_id("Song Title", "Artist Name", VIDEO_ID)
    assert created == {"job_id": created["job_id"], "song_id": expected_id}
    job_url = f"/jobs/{created['job_id']}"
    assert client.get(job_url).json() == {
        "job_id": created["job_id"],
        "song_id": expected_id,
        "status": "queued",
        "step": None,
        "attempt": 0,
        "retry_at": None,
        "error": None,
    }

    assert queue.run_ready() is True

    done = client.get(job_url).json()
    assert (done["status"], done["attempt"], done["error"]) == ("done", 1, None)
    assert (library / expected_id / "manifest.json").is_file()
    assert ("download", VIDEO_ID) in fake_tools.calls
    assert not any(call[0] == "fetch_info" for call in fake_tools.calls)


def test_job_shows_the_separation_step_while_it_runs(
    client: TestClient, queue: JobQueue, fake_tools
) -> None:
    job_id = client.post("/jobs", json=job_body()).json()["job_id"]
    seen = []
    separator = fake_tools.separator

    def watching_separator(step, model_file, audio):
        job = client.get(f"/jobs/{job_id}").json()
        seen.append((job["status"], job["step"]))
        return separator(step, model_file, audio)

    fake_tools.separator = watching_separator
    queue.run_ready()

    assert seen == [
        ("separating", "vocals"),
        ("separating", "karaoke"),
        ("separating", "instruments"),
    ]


def test_download_failure_waits_for_a_retry(
    client: TestClient, queue: JobQueue, fake_tools
) -> None:
    fake_tools.fail["download"] = OSError("HTTP Error 403: Forbidden")
    job_id = client.post("/jobs", json=job_body()).json()["job_id"]

    queue.run_ready()

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "queued"
    assert job["attempt"] == 1
    assert job["retry_at"] == "2026-11-13T20:02:00Z"
    assert job["error"] == "HTTP Error 403: Forbidden"


def test_job_title_and_album_reach_the_package_and_the_lyrics(
    client: TestClient, queue: JobQueue, library: Path, fake_tools
) -> None:
    body = job_body(album="Album Name", duration_s=245.3)
    song = client.post("/jobs", json=body).json()["song_id"]
    queue.run_ready()

    manifest = Manifest.load(library / song / "manifest.json")
    assert (manifest.title, manifest.artist) == ("Song Title", "Artist Name")
    (lyrics_call,) = [call for call in fake_tools.calls if call[0] == "lyrics"]
    assert lyrics_call[2]["album_name"] == "Album Name"
    assert lyrics_call[2]["duration"] == 245


def test_file_source_is_rejected(client: TestClient, queue: JobQueue) -> None:
    body = job_body()
    body["source"] = {"type": "file", "sha256": "0" * 64}

    response = client.post("/jobs", json=body)

    assert response.status_code == 422
    assert queue.run_ready() is False


def test_video_already_in_the_library_gives_a_done_job(
    client: TestClient, queue: JobQueue, library: Path, fake_tools
) -> None:
    existing = make_package(library, "Older Title", "Older Artist", VIDEO_ID)

    created = client.post("/jobs", json=job_body()).json()

    assert created["song_id"] == existing
    job = client.get(f"/jobs/{created['job_id']}").json()
    assert (job["status"], job["song_id"], job["attempt"]) == ("done", existing, 0)
    assert queue.run_ready() is False
    assert fake_tools.calls == []


def test_active_job_of_the_same_video_is_returned(
    client: TestClient, queue: JobQueue, fake_tools
) -> None:
    first = client.post("/jobs", json=job_body()).json()

    again = client.post("/jobs", json=job_body(title="Other Title")).json()

    assert again == first
    fake_tools.fail["download"] = OSError("HTTP Error 403: Forbidden")
    queue.run_ready()
    assert client.post("/jobs", json=job_body()).json() == first


def test_failed_job_does_not_count_as_active(
    client: TestClient, queue: JobQueue, fake_tools
) -> None:
    fake_tools.fail["make_runner"] = RuntimeError("no GPU")
    first = client.post("/jobs", json=job_body()).json()
    queue.run_ready()
    assert client.get(f"/jobs/{first['job_id']}").json()["status"] == "failed"

    again = client.post("/jobs", json=job_body()).json()

    assert again["job_id"] != first["job_id"]
    assert client.get(f"/jobs/{again['job_id']}").json()["status"] == "queued"


def test_unknown_job_is_404(client: TestClient) -> None:
    response = client.get("/jobs/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown job"}


def test_songs_lists_one_summary_per_package(client: TestClient, library: Path):
    first = make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    second = make_package(library, "Other Title", "Artist Name", OTHER_ID)
    (library / ".in-flight.abc.tmp").mkdir()
    (library / ".in-flight.abc.tmp" / "manifest.json").write_text("{}")
    (library / "no-manifest").mkdir()
    (library / "stray.txt").write_text("not a package")

    response = client.get("/songs")

    assert response.status_code == 200
    assert sorted(response.json(), key=lambda song: song["id"]) == sorted(
        [
            {
                "id": package_id,
                "title": title,
                "artist": "Artist Name",
                "duration_s": 245.3,
                "pipeline_version": 1,
                "created_at": "2026-11-04T20:00:00Z",
            }
            for package_id, title in [(first, "Song Title"), (second, "Other Title")]
        ],
        key=lambda song: song["id"],
    )


def test_songs_of_an_empty_library(client: TestClient) -> None:
    assert client.get("/songs").json() == []


def test_manifest_is_served_as_stored(client: TestClient, library: Path) -> None:
    package = make_package(
        library, "Song Title", "Artist Name", VIDEO_ID, extra={"melody": {"x": 1}}
    )
    stored = (library / package / "manifest.json").read_bytes()

    response = client.get(f"/songs/{package}/manifest")

    assert response.status_code == 200
    assert response.content == stored
    assert response.headers["content-type"] == "application/json"


def test_unknown_song_manifest_is_404(client: TestClient) -> None:
    response = client.get("/songs/missing/manifest")

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown song"}


@pytest.mark.parametrize("path", ["stems/lead.flac", "stems/other.flac", "lyrics.lrc"])
def test_manifest_files_are_served(client: TestClient, library: Path, path: str):
    package = make_package(library, "Song Title", "Artist Name", VIDEO_ID)

    response = client.get(f"/songs/{package}/files/{path}")

    assert response.status_code == 200
    assert response.content == (library / package / path).read_bytes()


@pytest.mark.parametrize(
    "path", ["manifest.json", "stems", "stems/guitar.flac", "work/mix.wav"]
)
def test_files_outside_the_manifest_are_404(
    client: TestClient, library: Path, path: str
) -> None:
    package = make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    (library / package / "work").mkdir()
    (library / package / "work" / "mix.wav").write_bytes(b"not listed")
    (library / package / "stems" / "guitar.flac").write_bytes(b"not listed")

    response = client.get(f"/songs/{package}/files/{path}")

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown file"}


def test_file_download_resumes_with_a_range(client: TestClient, library: Path):
    package = make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    whole = (library / package / "stems" / "lead.flac").read_bytes()
    url = f"/songs/{package}/files/stems/lead.flac"

    first = client.get(url, headers={"Range": "bytes=0-99"})
    rest = client.get(url, headers={"Range": f"bytes={len(first.content)}-"})

    assert first.status_code == 206
    assert first.headers["content-range"] == f"bytes 0-99/{len(whole)}"
    assert first.content == whole[:100]
    assert rest.status_code == 206
    assert rest.headers["content-range"] == f"bytes 100-{len(whole) - 1}/{len(whole)}"
    assert first.content + rest.content == whole
    assert client.get(url).headers["accept-ranges"] == "bytes"


TRAVERSALS = [
    "/songs/..%2Foutside/manifest",
    "/songs/%2E%2E/manifest",
    "/songs/%2E%2E%2Foutside/manifest",
    "/songs/..%5Coutside/manifest",
    "/songs/{song}/files/..%2F..%2Foutside%2Fmanifest.json",
    "/songs/{song}/files/%2E%2E/%2E%2E/outside/manifest.json",
    "/songs/{song}/files/%2E%2E%2F%2E%2E%2Foutside%2Fmanifest.json",
    "/songs/{song}/files/stems%2F..%2F..%2F..%2Foutside%2Fmanifest.json",
    "/songs/{song}/files/..%5C..%5Coutside%5Cmanifest.json",
]


@pytest.fixture
def outside(library: Path) -> Path:
    """Manifests in the library's parent and next to it, which no route may reach."""
    folder = library.parent / "outside"
    folder.mkdir()
    (folder / "manifest.json").write_text("outside the library")
    (library.parent / "manifest.json").write_text("outside the library")
    return folder


@pytest.mark.parametrize("url", TRAVERSALS)
def test_paths_cannot_leave_the_library(
    client: TestClient, library: Path, outside: Path, url: str
) -> None:
    song = make_package(library, "Song Title", "Artist Name", VIDEO_ID)

    response = client.get(url.replace("{song}", song))

    assert response.status_code == 404
    assert b"outside the library" not in response.content


@pytest.mark.parametrize(
    "url", ["/songs/..%2Foutside", "/songs/%2E%2E", "/songs/%2E%2E%2Foutside"]
)
def test_delete_cannot_leave_the_library(
    client: TestClient, library: Path, outside: Path, url: str
) -> None:
    song = make_package(library, "Song Title", "Artist Name", VIDEO_ID)

    response = client.delete(url)

    assert response.status_code == 404
    assert (outside / "manifest.json").is_file()
    assert (library / song / "manifest.json").is_file()


def test_delete_removes_the_package(client: TestClient, library: Path) -> None:
    song = make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    kept = make_package(library, "Other Title", "Artist Name", OTHER_ID)

    response = client.delete(f"/songs/{song}")

    assert response.status_code == 204
    assert response.content == b""
    assert [p.name for p in library.iterdir()] == [kept]
    assert [s["id"] for s in client.get("/songs").json()] == [kept]


def test_delete_unknown_song_is_404(client: TestClient) -> None:
    response = client.delete("/songs/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown song"}


def refuse_rename_of(monkeypatch, names: set[str]) -> None:
    """Path.rename fails like Windows does for a folder with an open file."""
    rename = Path.rename

    def guarded(self, target):
        if self.name in names:
            raise PermissionError(5, "Access is denied", str(self))
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", guarded)


def test_delete_of_a_busy_song_is_409_and_keeps_it(
    client: TestClient, library: Path, monkeypatch
) -> None:
    song = make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    refuse_rename_of(monkeypatch, {song})

    response = client.delete(f"/songs/{song}")

    assert response.status_code == 409
    assert response.json() == {"detail": "a file of the song is open; nothing deleted"}
    assert [p.name for p in library.iterdir()] == [song]
    assert len(list((library / song / "stems").iterdir())) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks open files")
def test_delete_of_a_song_with_a_really_open_file_is_409(
    client: TestClient, library: Path
) -> None:
    song = make_package(library, "Song Title", "Artist Name", VIDEO_ID)

    with (library / song / "stems" / "lead.flac").open("rb"):
        response = client.delete(f"/songs/{song}")

    assert response.status_code == 409
    assert (library / song / "manifest.json").is_file()
    assert len(list((library / song / "stems").iterdir())) == 2


def test_delete_all_reports_deleted_and_busy(
    client: TestClient, library: Path, monkeypatch
) -> None:
    gone = make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    busy = make_package(library, "Other Title", "Artist Name", OTHER_ID)
    (library / ".in-flight.abc.tmp").mkdir()
    refuse_rename_of(monkeypatch, {busy})

    response = client.delete("/songs")

    assert response.status_code == 200
    assert response.json() == {"deleted": [gone], "busy": [busy]}
    assert sorted(p.name for p in library.iterdir()) == [".in-flight.abc.tmp", busy]


def test_delete_all_of_an_empty_library(client: TestClient) -> None:
    assert client.delete("/songs").json() == {"deleted": [], "busy": []}


def test_invalid_manifests_are_skipped_and_the_rest_keeps_working(
    client: TestClient, queue: JobQueue, library: Path, caplog
) -> None:
    good = make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    newer = make_package(
        library, "Newer Title", "Artist Name", OTHER_ID, extra={"schema": 2}
    )
    (library / "bad-json").mkdir()
    (library / "bad-json" / "manifest.json").write_bytes(b"{not json")

    songs = client.get("/songs")
    lead = client.get(f"/songs/{good}/files/stems/lead.flac")
    job = client.post("/jobs", json=job_body(video_id="ZZZZZZZZZZZ"))
    deleted = client.delete("/songs")

    assert songs.status_code == 200
    assert [song["id"] for song in songs.json()] == [good]
    assert lead.status_code == 200
    assert job.status_code == 200
    assert queue.run_ready() is True
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == [good]
    assert {p.name for p in library.iterdir()} >= {newer, "bad-json"}
    warned = {r.getMessage().split(" ")[0] for r in caplog.records}
    assert warned >= {newer, "bad-json"}


def test_file_in_the_manifest_but_not_on_disk_is_404(
    client: TestClient, library: Path
) -> None:
    package = make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    (library / package / "stems" / "lead.flac").unlink()

    response = client.get(f"/songs/{package}/files/stems/lead.flac")

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown file"}


def test_a_trash_folder_left_behind_is_not_listed(
    client: TestClient, library: Path, monkeypatch
) -> None:
    song = make_package(library, "Song Title", "Artist Name", VIDEO_ID)
    monkeypatch.setattr("studio.library.shutil.rmtree", lambda *a, **k: None)

    assert client.delete(f"/songs/{song}").status_code == 204

    assert len(list(library.iterdir())) == 1
    assert client.get("/songs").json() == []
    assert client.get(f"/songs/{song}/manifest").status_code == 404


def test_concurrent_posts_of_one_video_make_one_job(
    client: TestClient, queue: JobQueue, monkeypatch
) -> None:
    """Each request waits inside the dedup for the other; only the lock keeps them apart."""
    both_inside = threading.Barrier(2, timeout=1)
    find_video = lib.find_video

    def find_video_together(library, video_id):
        try:
            both_inside.wait()
        except threading.BrokenBarrierError:
            pass
        return find_video(library, video_id)

    monkeypatch.setattr(lib, "find_video", find_video_together)
    responses = []

    def post() -> None:
        responses.append(client.post("/jobs", json=job_body()).json())

    threads = [threading.Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(responses) == 2
    assert responses[0] == responses[1]
    assert queue.run_ready() is True
    assert queue.run_ready() is False


@pytest.mark.parametrize(
    "video_id",
    ["XXXXXXXXXX", "XXXXXXXXXXXX", "XXXXXXXXXX!", "../../x/abc", "", "XXXXXXXXXX\n"],
    ids=["short", "long", "symbol", "path", "empty", "newline"],
)
def test_malformed_video_id_is_rejected(
    client: TestClient, queue: JobQueue, video_id: str
) -> None:
    response = client.post("/jobs", json=job_body(video_id=video_id))

    assert response.status_code == 422
    assert queue.run_ready() is False


def test_video_id_of_every_allowed_character_is_accepted(client: TestClient) -> None:
    assert (
        client.post("/jobs", json=job_body(video_id="aZ09_-aZ09_")).status_code == 200
    )
