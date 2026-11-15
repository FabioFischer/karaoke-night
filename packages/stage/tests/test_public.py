import json
import logging
import threading

import pytest
from stage.player import ReaperUnreachable
from stage.playlist import Playlist
from stage.public import Publisher, RequestReader
from stage.search import OFFLINE_NOTICE, Found, Result
from stage.session import Session

FIRST = "first-song--artist-name--a1b2c3"
SECOND = "second-song--artist-name--d4e5f6"
LYRICS = "[00:01.00] Line one\r\n[00:03.50] Line two\r\n"
# The fake store's TIME at the Stage's clock START: the store clock is the
# Stage's clock plus OFFSET_MS.
START = 100.0
STORE_START_MS = 1_700_000_000_000
OFFSET_MS = STORE_START_MS - START * 1000


class Clock:
    """The Stage's monotonic clock, moved by hand."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


class Stage:
    """A Stage of two library songs, no Studio and a fake player, on one clock."""

    def __init__(self, packages, player, store) -> None:
        packages.add(FIRST, title="First Song", duration_s=200.0, lyrics=LYRICS)
        packages.add(SECOND, title="Second Song", duration_s=180.0)
        self.library = packages.path
        self.packages = packages
        self.player = player
        self.store = store
        store.now_ms = STORE_START_MS
        self.clock = Clock()
        self.playlist = Playlist(packages.path, None)
        self.session = Session(
            self.playlist, packages.path, player, follow=False, clock=self.clock
        )
        self.publisher = Publisher(
            store.client(),
            packages.path,
            self.playlist,
            self.session,
            clock=self.clock,
        )

    def add(self, song_id: str):
        return self.playlist.add(
            Result(title="T", artist="A", origin="library", song_id=song_id)
        )

    def tick(self, seconds: float) -> None:
        self.clock.now += seconds
        self.store.now_ms += seconds * 1000

    def snapshot(self) -> dict:
        return self.store.json("karaoke:snapshot")


@pytest.fixture
def stage(packages, player, store) -> Stage:
    return Stage(packages, player, store)


def song_json(song_id: str, title: str, duration_s: float) -> dict:
    return {
        "title": title,
        "artist": "Artist Name",
        "origin": "library",
        "video_id": "XXXXXXXXXXX",
        "album": None,
        "duration_s": duration_s,
        "thumbnail_url": None,
        "song_id": song_id,
    }


def test_the_first_cycle_learns_the_store_clock_then_publishes(stage) -> None:
    stage.publisher.cycle()

    assert stage.store.names() == ["TIME", "SET", "SET"]
    assert stage.snapshot() == {
        "version": 1,
        "published_at_ms": STORE_START_MS,
        "session": {
            "state": "setup",
            "entry_id": None,
            "duration_s": None,
            "anchor": None,
        },
        "playlist": [],
        "lyrics": None,
    }


def test_a_playing_snapshot_carries_the_anchor_and_the_lyrics(stage) -> None:
    first = stage.add(FIRST)
    second = stage.add(SECOND)
    stage.session.start()
    stage.player.positions = [42.5]
    stage.tick(42.0)
    stage.session.poll()
    stage.tick(0.2)

    stage.publisher.cycle()

    assert stage.snapshot() == {
        "version": 1,
        "published_at_ms": STORE_START_MS + 42_200,
        "session": {
            "state": "playing",
            "entry_id": first.id,
            "duration_s": 200.0,
            "anchor": {"position_s": 42.5, "at_ms": STORE_START_MS + 42_000},
        },
        "playlist": [
            {
                "id": first.id,
                "state": "ready",
                "step": None,
                "progress": None,
                "error": None,
                "song": song_json(FIRST, "First Song", 200.0),
            },
            {
                "id": second.id,
                "state": "ready",
                "step": None,
                "progress": None,
                "error": None,
                "song": song_json(SECOND, "Second Song", 180.0),
            },
        ],
        "lyrics": {
            "song_id": FIRST,
            "status": "synced",
            "lines": [
                {"time_s": 1.0, "text": "Line one"},
                {"time_s": 3.5, "text": "Line two"},
            ],
        },
    }


def test_the_anchor_of_a_start_is_position_zero_when_play_returned(stage) -> None:
    stage.add(FIRST)
    stage.tick(5.0)
    stage.session.start()
    stage.tick(0.3)

    stage.publisher.cycle()

    assert stage.snapshot()["session"]["anchor"] == {
        "position_s": 0.0,
        "at_ms": STORE_START_MS + 5_000,
    }


def test_the_clock_offset_takes_half_the_round_trip(stage) -> None:
    def slow_time(command: list) -> None:
        if command == ["TIME"]:
            stage.clock.now += 0.4

    stage.store.on_command = slow_time

    stage.publisher.cycle()

    # TIME was asked at 100.0 and answered at 100.4: the store read its clock
    # at 100.2 by the Stage's clock, and the snapshot was published at 100.4.
    assert stage.snapshot()["published_at_ms"] == STORE_START_MS + 200


def test_the_clock_is_learned_again_every_minute(stage) -> None:
    for _ in range(5):
        stage.publisher.cycle()
        stage.tick(20.0)

    # At 0, 20, 40, 60 and 80 s: learned at 0 and at 60.
    assert stage.store.names().count("TIME") == 2


def test_a_new_store_clock_moves_the_times_published(stage) -> None:
    stage.publisher.cycle()
    stage.tick(60.0)
    stage.store.now_ms += 1_000

    stage.publisher.cycle()

    assert stage.snapshot()["published_at_ms"] == STORE_START_MS + 61_000


def test_nothing_is_published_until_the_store_clock_is_known(stage) -> None:
    stage.store.down = True
    stage.publisher.cycle()
    assert stage.store.values == {}

    stage.store.down = False
    stage.publisher.cycle()

    assert "karaoke:snapshot" in stage.store.values


def test_an_unchanged_snapshot_is_published_again_every_3_s(stage) -> None:
    stage.publisher.cycle()
    stage.tick(2.9)
    stage.publisher.cycle()
    assert snapshot_sets(stage) == 1

    stage.tick(0.1)
    stage.publisher.cycle()

    assert snapshot_sets(stage) == 2
    assert stage.snapshot()["published_at_ms"] == STORE_START_MS + 3_000


def snapshot_sets(stage) -> int:
    return sum(
        1
        for command in stage.store.commands
        if command[:2] == ["SET", "karaoke:snapshot"]
    )


def test_a_change_is_published_on_the_next_cycle(stage) -> None:
    stage.publisher.cycle()
    entry = stage.add(FIRST)
    stage.tick(0.5)

    stage.publisher.cycle()

    assert snapshot_sets(stage) == 2
    assert [item["id"] for item in stage.snapshot()["playlist"]] == [entry.id]
    assert stage.snapshot()["session"]["entry_id"] == entry.id


def test_a_new_position_alone_waits_for_the_3_s_refresh(stage) -> None:
    stage.add(FIRST)
    stage.session.start()
    stage.publisher.cycle()
    stage.player.positions = [1.0]
    stage.tick(1.0)
    stage.session.poll()

    stage.publisher.cycle()

    assert snapshot_sets(stage) == 1


def test_the_end_of_a_song_is_published_without_anchor_or_lyrics(stage) -> None:
    entry = stage.add(FIRST)
    stage.session.start()
    stage.publisher.cycle()
    stage.player.positions = [200.0]
    stage.player.end = True
    stage.session.poll()

    stage.publisher.cycle()

    assert stage.snapshot()["session"] == {
        "state": "results",
        "entry_id": entry.id,
        "duration_s": 200.0,
        "anchor": None,
    }
    assert stage.snapshot()["lyrics"] is None


def test_details_and_session_errors_are_not_published(stage) -> None:
    stage.add(FIRST)
    stage.playlist.add(
        Result(
            title="New Song",
            artist="Artist Name",
            origin="ytmusic",
            video_id="YYYYYYYYYYY",
        )
    )
    stage.playlist.advance()
    stage.session.start()
    stage.player.failures["position"] = ReaperUnreachable("could not reach REAPER")
    stage.session.poll()
    assert stage.session.view().error == "reaper_unreachable"

    stage.publisher.cycle()

    snapshot = stage.snapshot()
    assert set(snapshot["session"]) == {"state", "entry_id", "duration_s", "anchor"}
    failed = snapshot["playlist"][1]
    assert set(failed) == {"id", "state", "step", "progress", "error", "song"}
    assert (failed["state"], failed["error"]) == ("failed", "no_studio")


def test_a_song_without_lyrics_publishes_status_none(stage) -> None:
    stage.add(SECOND)
    stage.session.start()

    stage.publisher.cycle()

    assert stage.snapshot()["lyrics"] == {
        "song_id": SECOND,
        "status": "none",
        "lines": [],
    }


def test_the_next_song_publishes_its_own_lyrics(stage) -> None:
    stage.add(FIRST)
    stage.add(SECOND)
    stage.session.start()
    stage.publisher.cycle()
    stage.session.stop()

    stage.session.play_next()
    stage.publisher.cycle()

    assert stage.snapshot()["lyrics"] == {
        "song_id": SECOND,
        "status": "none",
        "lines": [],
    }


def test_lyrics_that_cannot_be_decoded_publish_as_null(stage, caplog) -> None:
    (stage.library / FIRST / "lyrics.lrc").write_bytes(b"[00:01.00] Caf\xe9\r\n")
    stage.add(FIRST)
    stage.session.start()

    with caplog.at_level(logging.WARNING, logger="stage"):
        stage.publisher.cycle()

    snapshot = stage.snapshot()
    assert snapshot["session"]["state"] == "playing"
    assert snapshot["lyrics"] is None
    assert caplog.messages == [
        (
            f"the lyrics of {FIRST} are not published: 'utf-8' codec can't decode "
            "byte 0xe9 in position 14: invalid continuation byte"
        )
    ]


def test_an_invalid_manifest_is_warned_once_however_often_it_is_scanned(
    stage, caplog
) -> None:
    broken = stage.library / "broken-song--artist-name--0a0b0c"
    broken.mkdir()
    (broken / "manifest.json").write_text("{", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="stage"):
        for _ in range(4):
            stage.publisher.cycle()
            stage.tick(3.0)

    assert library_sets(stage) == 1
    assert [message.split(":")[0] for message in caplog.messages] == [
        "broken-song--artist-name--0a0b0c is not listed"
    ]


def test_the_library_is_published_at_start(stage) -> None:
    stage.publisher.cycle()

    assert stage.store.json("karaoke:library") == {
        "published_at_ms": STORE_START_MS,
        "songs": [
            song_json(FIRST, "First Song", 200.0),
            song_json(SECOND, "Second Song", 180.0),
        ],
    }


def test_the_library_is_published_again_only_when_it_changes(stage) -> None:
    stage.publisher.cycle()
    stage.tick(3.0)
    stage.publisher.cycle()
    assert library_sets(stage) == 1

    third = stage.packages.add("third-song--artist-name--0a0b0c", title="Third")
    stage.tick(3.0)
    stage.publisher.cycle()

    assert library_sets(stage) == 2
    songs = stage.store.json("karaoke:library")["songs"]
    assert [song["song_id"] for song in songs] == [FIRST, SECOND, third]


def library_sets(stage) -> int:
    return sum(
        1
        for command in stage.store.commands
        if command[:2] == ["SET", "karaoke:library"]
    )


def test_a_failing_store_is_tried_again_on_the_next_cycle(stage) -> None:
    stage.publisher.cycle()
    stage.add(FIRST)
    stage.store.down = True
    stage.publisher.cycle()

    stage.store.down = False
    stage.publisher.cycle()

    assert len(stage.snapshot()["playlist"]) == 1


def test_the_publisher_thread_publishes_and_stops(stage) -> None:
    published = threading.Event()
    stage.store.on_command = lambda command: (
        published.set() if command[:2] == ["SET", "karaoke:snapshot"] else None
    )

    stage.publisher.start()
    assert published.wait(5)
    stage.publisher.stop()


# The request reader.


class Reader:
    """The request reader over a Stage, with a search the test controls."""

    def __init__(self, stage: Stage) -> None:
        self.stage = stage
        self.store = stage.store
        self.queries: list[str] = []
        self.found = Found([])
        self.release = threading.Event()
        self.release.set()
        self.reader = RequestReader(
            stage.store.client(), stage.playlist, stage.session, self.find
        )
        # The first cycle only clears the requests left from before.
        self.reader.cycle()

    def find(self, query: str) -> Found:
        self.queries.append(query)
        assert self.release.wait(5)
        return self.found

    def send(self, *requests) -> None:
        self.store.lists.setdefault("karaoke:requests", []).extend(
            request if isinstance(request, str) else json.dumps(request)
            for request in requests
        )

    def run(self) -> None:
        """One cycle, then every search it started."""
        self.reader.cycle()
        self.reader.close()

    def answer(self, request_id: str) -> dict:
        return self.store.json(f"karaoke:answer:{request_id}")


@pytest.fixture
def reader(stage) -> Reader:
    return Reader(stage)


def library_result(song_id: str) -> dict:
    return {"title": "T", "artist": "A", "origin": "library", "song_id": song_id}


def test_the_first_cycle_deletes_the_requests_left_from_before(stage) -> None:
    stage.store.lists["karaoke:requests"] = [
        json.dumps({"id": "old", "op": "add", "song": library_result(FIRST)})
    ]
    reader = RequestReader(stage.store.client(), stage.playlist, stage.session, None)

    reader.cycle()
    reader.cycle()

    assert stage.store.commands[0] == ["DEL", "karaoke:requests"]
    assert stage.playlist.entries() == []
    assert "karaoke:answer:old" not in stage.store.values


def test_no_request_is_read_until_the_old_ones_are_deleted(stage) -> None:
    stage.store.down = True
    reader = RequestReader(stage.store.client(), stage.playlist, stage.session, None)
    reader.cycle()
    stage.store.down = False
    stage.store.lists["karaoke:requests"] = ["left from before"]

    reader.cycle()

    assert stage.store.names() == ["DEL"]


def test_an_add_of_a_library_song_answers_the_entry(reader, stage) -> None:
    reader.send({"id": "r1", "op": "add", "song": library_result(FIRST)})

    reader.run()

    (entry,) = stage.playlist.entries()
    assert (entry.state, entry.song.song_id) == ("ready", FIRST)
    assert reader.answer("r1") == {"ok": True, "entry_id": entry.id}
    assert stage.store.expiry["karaoke:answer:r1"] == 120


def test_an_add_of_a_new_song_queues_it(reader, stage) -> None:
    song = {
        "title": "New Song",
        "artist": "Artist Name",
        "origin": "ytmusic",
        "video_id": "YYYYYYYYYYY",
        "thumbnail_url": "https://example.invalid/thumbnail.jpg",
    }
    reader.send({"id": "r1", "op": "add", "song": song})

    reader.run()

    (entry,) = stage.playlist.entries()
    assert (entry.state, entry.song.video_id) == ("queued", "YYYYYYYYYYY")
    assert reader.answer("r1") == {"ok": True, "entry_id": entry.id}


def test_an_add_that_cannot_become_a_job_is_refused(reader, stage) -> None:
    song = {"title": "T", "artist": "A", "origin": "ytmusic"}
    reader.send({"id": "r1", "op": "add", "song": song})

    reader.run()

    assert reader.answer("r1") == {"ok": False, "error": "not_addable"}
    assert stage.playlist.entries() == []


def test_a_remove_answers_ok_or_the_refusal(reader, stage) -> None:
    playing = stage.add(FIRST)
    other = stage.add(SECOND)
    stage.session.start()
    reader.send(
        {"id": "r1", "op": "remove", "entry_id": other.id},
        {"id": "r2", "op": "remove", "entry_id": other.id},
        {"id": "r3", "op": "remove", "entry_id": playing.id},
    )

    reader.run()

    assert reader.answer("r1") == {"ok": True}
    assert reader.answer("r2") == {"ok": False, "error": "unknown_entry"}
    assert reader.answer("r3") == {"ok": False, "error": "entry_playing"}
    assert [entry.id for entry in stage.playlist.entries()] == [playing.id]


def test_a_search_answers_the_results(reader) -> None:
    reader.found = Found(
        [Result(title="T", artist="A", origin="ytmusic", video_id="YYYYYYYYYYY")]
    )
    reader.send({"id": "r1", "op": "search", "q": "some words"})

    reader.run()

    assert reader.queries == ["some words"]
    assert reader.answer("r1") == {
        "ok": True,
        "results": [
            {
                "title": "T",
                "artist": "A",
                "origin": "ytmusic",
                "video_id": "YYYYYYYYYYY",
                "album": None,
                "duration_s": None,
                "thumbnail_url": None,
                "song_id": None,
            }
        ],
        "notice": None,
    }
    assert reader.store.expiry["karaoke:answer:r1"] == 120


def test_a_search_that_failed_online_says_offline(reader) -> None:
    reader.found = Found([], OFFLINE_NOTICE)
    reader.send({"id": "r1", "op": "search", "q": "words"})

    reader.run()

    assert reader.answer("r1") == {"ok": True, "results": [], "notice": "offline"}


def test_a_slow_search_does_not_hold_the_requests_behind_it(reader, stage) -> None:
    reader.release.clear()
    reader.send(
        {"id": "r1", "op": "search", "q": "slow"},
        {"id": "r2", "op": "add", "song": library_result(FIRST)},
    )

    reader.reader.cycle()

    assert reader.answer("r2")["ok"] is True
    assert "karaoke:answer:r1" not in reader.store.values
    reader.release.set()
    reader.reader.close()
    assert reader.answer("r1")["ok"] is True


@pytest.mark.parametrize(
    "request_",
    [
        {"id": "r1", "op": "play"},
        {"id": "r1"},
        {"id": "r1", "op": "add"},
        {"id": "r1", "op": "add", "song": {"title": "T"}},
        {"id": "r1", "op": "add", "song": "a song"},
        {"id": "r1", "op": "remove"},
        {"id": "r1", "op": "remove", "entry_id": 7},
        {"id": "r1", "op": "search"},
        {"id": "r1", "op": "search", "q": ["words"]},
    ],
)
def test_an_unreadable_request_with_an_id_is_invalid(reader, stage, request_) -> None:
    reader.send(request_)

    reader.run()

    assert reader.answer("r1") == {"ok": False, "error": "invalid_request"}
    assert stage.playlist.entries() == []
    assert reader.queries == []


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps(["r1", "add"]),
        json.dumps({"op": "search", "q": "words"}),
        json.dumps({"id": 7, "op": "search", "q": "words"}),
        json.dumps({"id": "", "op": "search", "q": "words"}),
    ],
)
def test_a_request_without_an_id_is_dropped(reader, raw, caplog) -> None:
    reader.send(raw)

    with caplog.at_level(logging.WARNING, logger="stage"):
        reader.run()

    assert [
        key for key in reader.store.values if key.startswith("karaoke:answer:")
    ] == []
    assert reader.queries == []
    assert caplog.messages == [f"dropped a public request without an id: {raw[:200]}"]


def test_requests_are_applied_in_order(reader, stage) -> None:
    reader.send(
        {"id": "r1", "op": "add", "song": library_result(FIRST)},
        {"id": "r2", "op": "add", "song": library_result(SECOND)},
    )

    reader.run()

    entries = stage.playlist.entries()
    assert [entry.song.song_id for entry in entries] == [FIRST, SECOND]
    assert reader.answer("r2")["entry_id"] == entries[1].id
    assert "karaoke:requests" not in stage.store.lists or (
        stage.store.lists["karaoke:requests"] == []
    )


def test_a_failing_store_stops_the_cycle_until_the_next_one(reader, stage) -> None:
    reader.send({"id": "r1", "op": "add", "song": library_result(FIRST)})
    stage.store.down = True
    reader.reader.cycle()
    stage.store.down = False

    reader.run()

    assert reader.answer("r1")["ok"] is True


def test_a_search_that_breaks_is_logged_and_answers_nothing(reader, caplog) -> None:
    def broken(query: str) -> Found:
        raise RuntimeError("broken search")

    reader.reader = RequestReader(
        reader.store.client(), reader.stage.playlist, reader.stage.session, broken
    )
    reader.reader.cycle()
    reader.send({"id": "r1", "op": "search", "q": "words"})

    with caplog.at_level(logging.WARNING, logger="stage"):
        reader.run()

    assert "karaoke:answer:r1" not in reader.store.values
    assert caplog.messages == ["public request r1 failed"]


def test_the_reader_thread_reads_and_stops(reader, stage) -> None:
    answered = threading.Event()
    stage.store.on_command = lambda command: (
        answered.set() if command[:2] == ["SET", "karaoke:answer:r1"] else None
    )
    reader.send({"id": "r1", "op": "add", "song": library_result(FIRST)})

    reader.reader.start()
    assert answered.wait(5)
    reader.reader.stop()
