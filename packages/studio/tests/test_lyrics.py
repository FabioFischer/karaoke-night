from importlib import metadata

import pytest
from studio.lyrics import (
    USER_AGENT,
    FoundLyrics,
    LrclibError,
    Paced,
    classify,
    lookup,
)

SYNCED = {"syncedLyrics": "[00:01.00] Line one", "plainLyrics": "Line one"}
PLAIN = {"syncedLyrics": None, "plainLyrics": "Line one\nLine two"}
INSTRUMENTAL = {"instrumental": True, "syncedLyrics": None, "plainLyrics": None}
EMPTY = {"syncedLyrics": "", "plainLyrics": " "}


class FakeGet:
    """Answers each LRCLIB route from a table and records the requests."""

    def __init__(self, routes: dict[str, tuple[int, object]]):
        self.routes = routes
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, path: str, params: dict) -> tuple[int, object]:
        self.requests.append((path, params))
        return self.routes[path]


def find(get) -> FoundLyrics | None:
    return lookup(get, "Song Title", "Artist Name", "Album Name", 200)


def test_classify_in_status_order() -> None:
    assert classify(None) == "none"
    assert classify(INSTRUMENTAL | SYNCED) == "instrumental"
    assert classify(SYNCED) == "synced"
    assert classify(PLAIN) == "plain"
    assert classify(EMPTY) == "none"


def test_synced_lyrics_go_to_an_lrc_file() -> None:
    get = FakeGet({"/api/get": (200, SYNCED)})

    assert find(get) == FoundLyrics(
        file="lyrics.lrc", text="[00:01.00] Line one", synced=True
    )
    assert get.requests == [
        (
            "/api/get",
            {
                "track_name": "Song Title",
                "artist_name": "Artist Name",
                "album_name": "Album Name",
                "duration": 200,
            },
        )
    ]


def test_plain_lyrics_go_to_a_txt_file() -> None:
    get = FakeGet({"/api/get": (200, PLAIN), "/api/search": (200, [])})

    assert find(get) == FoundLyrics(
        file="lyrics.txt", text="Line one\nLine two", synced=False
    )


def test_instrumental_gives_no_lyrics() -> None:
    get = FakeGet({"/api/get": (200, INSTRUMENTAL), "/api/search": (200, [])})

    assert find(get) is None


def test_nothing_found_gives_no_lyrics() -> None:
    get = FakeGet({"/api/get": (404, None), "/api/search": (200, [])})

    assert find(get) is None
    assert [path for path, _ in get.requests] == ["/api/get", "/api/search"]


def test_search_takes_the_best_hit_within_2_seconds() -> None:
    hits = [
        {"duration": 203, **SYNCED},
        {"duration": 201, **PLAIN},
        {"duration": 198, "syncedLyrics": "[00:02.00] Close enough"},
    ]
    get = FakeGet({"/api/get": (404, None), "/api/search": (200, hits)})

    assert find(get) == FoundLyrics(
        file="lyrics.lrc", text="[00:02.00] Close enough", synced=True
    )
    assert get.requests[1] == (
        "/api/search",
        {"track_name": "Song Title", "artist_name": "Artist Name"},
    )


def test_get_without_lyrics_still_searches() -> None:
    get = FakeGet({"/api/get": (200, EMPTY), "/api/search": (200, [PLAIN])})

    found = lookup(get, "Song Title", "Artist Name", None, None)

    assert found is not None and found.file == "lyrics.txt"
    assert "album_name" not in get.requests[0][1]
    assert "duration" not in get.requests[0][1]


@pytest.mark.parametrize(
    "routes",
    [
        {"/api/get": (500, None)},
        {"/api/get": (404, None), "/api/search": (503, None)},
    ],
    ids=["get", "search"],
)
def test_http_failure_raises(routes) -> None:
    with pytest.raises(LrclibError):
        find(FakeGet(routes))


def test_paced_waits_between_requests_only() -> None:
    waits: list[float] = []
    paced = Paced(lambda path, params: (200, path), 0.3, sleep=waits.append)

    assert paced("/a", {}) == (200, "/a")
    assert paced("/b", {}) == (200, "/b")

    assert waits == [0.3]


def test_user_agent_names_the_project_and_version() -> None:
    assert USER_AGENT == (
        f"karaoke-night/{metadata.version('studio')} "
        "(https://github.com/FabioFischer/karaoke-night)"
    )
