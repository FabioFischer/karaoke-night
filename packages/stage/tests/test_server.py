import logging
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stage.player import ListenerSilent
from stage.playlist import Playlist
from stage.search import OFFLINE_NOTICE, Found, Result
from stage.server import create_app
from stage.session import Session

FIRST = "first-song--artist-name--a1b2c3"
SECOND = "second-song--artist-name--d4e5f6"
STEMS = ("lead", "backing", "drums", "bass", "guitar", "piano", "other")
LYRICS = "[00:01.00] Line one\r\n[00:03.50][00:10.00] Line two\r\n"


class Stage:
    """The Stage API over a library of two packages, a fake player and no Studio."""

    def __init__(self, packages, player, web: Path | None = None) -> None:
        packages.add(FIRST, title="First Song", duration_s=200.0, lyrics=LYRICS)
        packages.add(SECOND, title="Second Song", duration_s=180.0)
        self.library = packages.path
        self.player = player
        self.playlist = Playlist(packages.path, None)
        self.session = Session(self.playlist, packages.path, player, follow=False)
        self.queries: list[str] = []
        self.found = Found([])
        self.client = TestClient(
            create_app(
                packages.path, self.playlist, self.session, web=web, find=self.find
            )
        )

    def find(self, query: str) -> Found:
        self.queries.append(query)
        return self.found

    def add(self, song_id: str) -> dict:
        response = self.client.post(
            "/api/playlist",
            json={"title": "T", "artist": "A", "origin": "library", "song_id": song_id},
        )
        assert response.status_code == 201, response.text
        return response.json()


@pytest.fixture
def stage(packages, player) -> Stage:
    return Stage(packages, player)


def test_state_of_a_new_stage(stage) -> None:
    response = stage.client.get("/api/state")

    assert response.status_code == 200
    assert response.json() == {
        "session": {
            "state": "setup",
            "entry_id": None,
            "muted": ["lead"],
            "key": 0,
            "position_s": None,
            "duration_s": None,
            "error": None,
            "detail": None,
            "stems": [],
        },
        "playlist": [],
    }


def test_state_lists_the_stems_of_the_song_of_the_session_in_setup(stage) -> None:
    stage.client.post(
        "/api/playlist",
        json={
            "title": "New Song",
            "artist": "Artist Name",
            "origin": "ytmusic",
            "video_id": "YYYYYYYYYYY",
        },
    )
    not_in_library = stage.client.get("/api/state").json()["session"]
    stage.client.post("/api/session/skip")
    stage.add(FIRST)
    in_setup = stage.client.get("/api/state").json()["session"]
    stage.client.post("/api/session/start")
    playing = stage.client.get("/api/state").json()["session"]

    assert not_in_library["stems"] == []
    assert in_setup["stems"] == list(STEMS)
    assert playing["stems"] == []


def test_state_reads_only_the_manifest_of_the_song_of_the_session(
    stage, caplog
) -> None:
    broken = stage.library / "broken-song--artist-name--0a0b0c"
    broken.mkdir()
    (broken / "manifest.json").write_text("{", encoding="utf-8")
    stage.add(FIRST)
    # Adding a song scans the library, which warns about the broken package.
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="stage"):
        session = stage.client.get("/api/state").json()["session"]

    assert session["stems"] == list(STEMS)
    assert caplog.messages == []


def test_state_has_no_stems_for_a_missing_or_invalid_manifest(stage, caplog) -> None:
    stage.add(FIRST)
    manifest = stage.library / FIRST / "manifest.json"

    with caplog.at_level(logging.WARNING, logger="stage"):
        manifest.write_text("{", encoding="utf-8")
        invalid = stage.client.get("/api/state").json()["session"]
        manifest.unlink()
        missing = stage.client.get("/api/state").json()["session"]

    assert invalid["stems"] == []
    assert missing["stems"] == []
    assert caplog.messages == []


def test_state_reads_no_manifest_outside_the_library(stage) -> None:
    shutil.copytree(stage.library / FIRST, stage.library.parent / FIRST)
    stage.client.post(
        "/api/playlist",
        json={
            "title": "New Song",
            "artist": "Artist Name",
            "origin": "ytmusic",
            "video_id": "YYYYYYYYYYY",
            "song_id": f"../{FIRST}",
        },
    )

    assert stage.client.get("/api/state").json()["session"]["stems"] == []


def test_adding_a_library_song_makes_a_ready_entry(stage) -> None:
    entry = stage.add(FIRST)

    assert entry == {
        "id": entry["id"],
        "state": "ready",
        "step": None,
        "progress": None,
        "error": None,
        "detail": None,
        "song": {
            "title": "First Song",
            "artist": "Artist Name",
            "origin": "library",
            "video_id": "XXXXXXXXXXX",
            "album": None,
            "duration_s": 200.0,
            "thumbnail_url": None,
            "song_id": FIRST,
        },
    }
    state = stage.client.get("/api/state").json()
    assert state["playlist"] == [entry]
    assert state["session"]["entry_id"] == entry["id"]
    assert state["session"]["duration_s"] == 200.0


def test_adding_a_new_song_queues_it_and_a_stage_without_studio_fails_it(
    stage,
) -> None:
    response = stage.client.post(
        "/api/playlist",
        json={
            "title": "New Song",
            "artist": "Artist Name",
            "origin": "ytmusic",
            "video_id": "YYYYYYYYYYY",
            "thumbnail_url": "https://example.invalid/thumbnail.jpg",
        },
    )
    assert response.status_code == 201
    assert response.json()["state"] == "queued"

    stage.playlist.advance()

    (entry,) = stage.client.get("/api/state").json()["playlist"]
    assert (entry["state"], entry["error"]) == ("failed", "no_studio")


def test_a_song_that_cannot_be_added_is_refused_with_a_code(stage) -> None:
    unknown = stage.client.post(
        "/api/playlist",
        json={
            "title": "T",
            "artist": "A",
            "origin": "library",
            "song_id": "x--y--000000",
        },
    )
    malformed = stage.client.post("/api/playlist", json={"title": "T"})

    assert unknown.status_code == 422
    assert unknown.json()["error"] == "not_addable"
    assert malformed.status_code == 422
    assert malformed.json()["error"] == "invalid_request"


def test_removing_entries(stage) -> None:
    first = stage.add(FIRST)
    second = stage.add(SECOND)
    stage.client.post("/api/session/start")

    playing = stage.client.delete(f"/api/playlist/{first['id']}")
    removed = stage.client.delete(f"/api/playlist/{second['id']}")
    unknown = stage.client.delete(f"/api/playlist/{second['id']}")

    assert (playing.status_code, playing.json()["error"]) == (409, "entry_playing")
    assert removed.status_code == 204
    assert (unknown.status_code, unknown.json()["error"]) == (404, "unknown_entry")


def test_a_whole_round_through_the_session_routes(stage) -> None:
    first = stage.add(FIRST)
    second = stage.add(SECOND)

    settings = stage.client.put(
        "/api/session/settings", json={"muted": ["lead", "drums"], "key": 2}
    )
    assert settings.status_code == 200
    assert settings.json()["session"]["muted"] == ["drums", "lead"]

    started = stage.client.post("/api/session/start").json()["session"]
    assert (started["state"], started["entry_id"], started["key"]) == (
        "playing",
        first["id"],
        2,
    )
    stopped = stage.client.post("/api/session/stop").json()["session"]
    assert stopped["state"] == "results"

    following = stage.client.post("/api/session/play_next").json()
    assert following["session"]["entry_id"] == second["id"]
    assert following["session"]["muted"] == ["drums", "lead"]
    assert following["session"]["key"] == 0
    assert [entry["id"] for entry in following["playlist"]] == [second["id"]]

    stage.client.post("/api/session/stop")
    after = stage.client.post("/api/session/setup_next").json()
    assert after["session"]["state"] == "setup"
    assert after["playlist"] == []


def test_skip_removes_the_current_song(stage) -> None:
    stage.add(FIRST)
    second = stage.add(SECOND)

    state = stage.client.post("/api/session/skip").json()

    assert [entry["id"] for entry in state["playlist"]] == [second["id"]]


def test_session_refusals_carry_a_code(stage, player) -> None:
    empty = stage.client.post("/api/session/start")
    wrong = stage.client.post("/api/session/stop")
    stage.add(FIRST)
    player.failures["load"] = ListenerSilent("the karaoke listener did not answer")
    silent = stage.client.post("/api/session/start")

    assert (empty.status_code, empty.json()["error"]) == (409, "no_song")
    assert (wrong.status_code, wrong.json()["error"]) == (409, "wrong_state")
    assert silent.status_code == 502
    assert silent.json() == {
        "error": "listener_silent",
        "detail": "the karaoke listener did not answer",
    }
    session = stage.client.get("/api/state").json()["session"]
    assert (session["state"], session["error"]) == ("setup", "listener_silent")


def test_state_shows_the_position_while_playing(stage, player) -> None:
    stage.add(FIRST)
    player.positions = [12.5]
    stage.client.post("/api/session/start")

    stage.session.poll()

    session = stage.client.get("/api/state").json()["session"]
    assert (session["position_s"], session["duration_s"]) == (12.5, 200.0)


def test_search_wraps_the_stage_search(stage) -> None:
    stage.found = Found(
        [
            Result(
                title="Song", artist="Artist", origin="ytmusic", video_id="ZZZZZZZZZZZ"
            )
        ],
        OFFLINE_NOTICE,
    )

    response = stage.client.get("/api/search", params={"q": "song artist"})

    assert stage.queries == ["song artist"]
    assert response.json() == {
        "results": [
            {
                "title": "Song",
                "artist": "Artist",
                "origin": "ytmusic",
                "video_id": "ZZZZZZZZZZZ",
                "album": None,
                "duration_s": None,
                "thumbnail_url": None,
                "song_id": None,
            }
        ],
        "notice": "offline",
        "detail": OFFLINE_NOTICE,
    }


def test_search_with_internet_has_no_notice(stage) -> None:
    response = stage.client.get("/api/search", params={"q": "song"})

    assert response.json() == {"results": [], "notice": None, "detail": None}


def test_library_lists_every_song(stage) -> None:
    songs = stage.client.get("/api/library").json()["songs"]

    assert [(song["song_id"], song["title"]) for song in songs] == [
        (FIRST, "First Song"),
        (SECOND, "Second Song"),
    ]


def test_lyrics_of_a_library_song(stage) -> None:
    synced = stage.client.get(f"/api/library/{FIRST}/lyrics").json()
    none = stage.client.get(f"/api/library/{SECOND}/lyrics").json()
    unknown = stage.client.get("/api/library/unknown--song--000000/lyrics")

    assert synced == {
        "status": "synced",
        "lines": [
            {"time_s": 1.0, "text": "Line one"},
            {"time_s": 3.5, "text": "Line two"},
            {"time_s": 10.0, "text": "Line two"},
        ],
    }
    assert none == {"status": "none", "lines": []}
    assert (unknown.status_code, unknown.json()["error"]) == (404, "unknown_song")


@pytest.fixture
def web(tmp_path: Path) -> Path:
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>index</title>")
    (web / "assets" / "app.js").write_text("console.log('app')")
    (tmp_path / "secret.txt").write_text("outside the web folder")
    return web


def test_the_built_web_is_served_with_a_fallback_to_the_index(
    packages, player, web
) -> None:
    client = Stage(packages, player, web=web).client

    index = client.get("/")
    asset = client.get("/assets/app.js")
    route = client.get("/karaoke/stage/results")
    outside = client.get("/..%2Fsecret.txt")
    api = client.get("/api/unknown")

    assert "<title>index</title>" in index.text
    assert asset.text == "console.log('app')"
    assert "<title>index</title>" in route.text
    assert "outside" not in outside.text
    assert api.status_code == 404


def test_without_a_built_web_only_the_api_is_served(stage) -> None:
    assert stage.client.get("/karaoke").status_code == 404
    assert stage.client.get("/api/state").status_code == 200


@pytest.mark.parametrize("with_web", [False, True])
def test_unknown_paths_and_wrong_methods_answer_the_error_shape(
    packages, player, web, with_web
) -> None:
    client = Stage(packages, player, web=web if with_web else None).client

    unknown = client.get("/api/unknown")
    posted = client.post("/api/unknown")
    wrong_method = client.get("/api/session/start")

    assert (unknown.status_code, unknown.json()) == (
        404,
        {"error": "not_found", "detail": "GET /api/unknown: Not Found"},
    )
    assert (posted.status_code, posted.json()["error"]) == (404, "not_found")
    assert (wrong_method.status_code, wrong_method.json()) == (
        405,
        {
            "error": "method_not_allowed",
            "detail": "GET /api/session/start: Method Not Allowed",
        },
    )
    assert wrong_method.headers["allow"] == "POST"


def test_a_path_outside_the_api_without_a_web_answers_the_error_shape(stage) -> None:
    response = stage.client.get("/karaoke")

    assert response.json() == {
        "error": "not_found",
        "detail": "GET /karaoke: Not Found",
    }
