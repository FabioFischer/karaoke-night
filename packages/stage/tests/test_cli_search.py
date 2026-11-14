import pytest
from stage import cli, search

FIRST = "first-song--first-artist--a1b2c3"
SECOND = "second-song--second-artist--d4e5f6"


def offline(query: str) -> list[dict]:
    raise OSError("no route to host")


@pytest.fixture(autouse=True)
def no_network(monkeypatch) -> None:
    """Both online searches fail unless a test answers for them."""
    monkeypatch.setattr(search, "ytmusic_songs", offline)
    monkeypatch.setattr(search, "ytdlp_videos", offline)


def answer(monkeypatch, client: str, results: list[dict]) -> None:
    monkeypatch.setattr(search, client, lambda query: results)


def stage_search(query: str, library) -> int:
    return cli.main(["search", query, "--library", str(library.path)])


def test_search_prints_ready_songs_then_new_ones(
    local_library, monkeypatch, capsys
) -> None:
    local_library.add(FIRST, "First Song", "First Artist", video_id="AAAAAAAAAAA")
    answer(
        monkeypatch,
        "ytmusic_songs",
        [
            {
                "videoId": "BBBBBBBBBBB",
                "title": "First Song (Live)",
                "artists": [{"name": "First Artist"}, {"name": "Guest Singer"}],
                "album": {"name": "Live Album"},
                "duration_seconds": 191,
                "thumbnails": [],
            },
            {
                "videoId": "CCCCCCCCCCC",
                "title": "First Song",
                "artists": [{"name": "Cover Band"}],
                "album": None,
                "thumbnails": [],
            },
        ],
    )

    assert stage_search("first song", local_library) == 0

    out, err = capsys.readouterr()
    assert out.splitlines() == [
        f"ready  First Song  First Artist  -  4:05  AAAAAAAAAAA  {FIRST}",
        (
            "new    First Song (Live)  First Artist, Guest Singer  Live Album  3:11  "
            "BBBBBBBBBBB"
        ),
        "new    First Song  Cover Band  -  -  CCCCCCCCCCC",
    ]
    assert err == ""


def test_search_from_yt_dlp_shows_the_channel_as_artist(
    local_library, monkeypatch, capsys
) -> None:
    answer(monkeypatch, "ytmusic_songs", [])
    answer(
        monkeypatch,
        "ytdlp_videos",
        [
            {
                "id": "EEEEEEEEEEE",
                "title": "Video Title",
                "channel": "Channel Name",
                "duration": 153.0,
            }
        ],
    )

    assert stage_search("video", local_library) == 0

    assert capsys.readouterr().out.splitlines() == [
        "new    Video Title  Channel Name  -  2:33  EEEEEEEEEEE"
    ]


def test_search_without_internet_lists_the_library_with_the_notice(
    local_library, capsys
) -> None:
    local_library.add(
        SECOND,
        "Second Song",
        "Second Artist",
        source={"type": "file", "sha256": "0" * 64},
    )

    assert stage_search("second", local_library) == 0

    out, err = capsys.readouterr()
    assert out.splitlines() == [
        f"ready  Second Song  Second Artist  -  4:05  -  {SECOND}",
        "could not search online; only songs already in the library are listed",
    ]
    assert err.splitlines() == [
        (
            "warning: YouTube Music search failed (no route to host); searching "
            "with yt-dlp"
        ),
        "warning: yt-dlp search failed (no route to host)",
    ]


def test_search_with_no_result_says_so(local_library, monkeypatch, capsys) -> None:
    answer(monkeypatch, "ytmusic_songs", [])
    answer(monkeypatch, "ytdlp_videos", [])

    assert stage_search("nothing like it", local_library) == 0

    assert capsys.readouterr().out == "no results for 'nothing like it'\n"


def test_search_with_no_result_and_no_internet_gives_both_lines(
    local_library, capsys
) -> None:
    assert stage_search("song", local_library) == 0

    assert capsys.readouterr().out.splitlines() == [
        "no results for 'song'",
        "could not search online; only songs already in the library are listed",
    ]


def test_search_uses_the_library_in_the_current_folder_by_default(
    local_library, monkeypatch, capsys
) -> None:
    local_library.add(FIRST, "First Song", "First Artist")
    monkeypatch.chdir(local_library.path.parent)

    assert cli.main(["search", "first"]) == 0

    assert capsys.readouterr().out.startswith("ready  First Song  First Artist  ")


def test_search_needs_the_text(capsys) -> None:
    with pytest.raises(SystemExit) as exit_:
        cli.main(["search"])

    assert exit_.value.code == 2
    assert "text" in capsys.readouterr().err
