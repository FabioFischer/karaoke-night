import logging
from collections.abc import Callable

import pytest
import requests
import yt_dlp
from stage import search
from stage.search import Result
from yt_dlp.utils import DownloadError
from ytmusicapi.exceptions import YTMusicServerError

FIRST = "first-song--first-artist--a1b2c3"
SECOND = "second-song--second-artist--d4e5f6"


def ytmusic_song(
    video_id: str,
    title: str = "Song Title",
    artists: tuple[str, ...] = ("Artist Name",),
    **fields,
) -> dict:
    """A result of YTMusic.search(filter="songs"), in the shape it has."""
    return {
        "category": "Songs",
        "resultType": "song",
        "videoId": video_id,
        "title": title,
        "artists": [{"name": name, "id": f"id of {name}"} for name in artists],
        "album": {"name": "Album Name", "id": "album-id"},
        "duration": "2:06",
        "duration_seconds": 126,
        "thumbnails": [
            {"url": f"https://thumbs.example/{video_id}/60", "width": 60, "height": 60},
            {
                "url": f"https://thumbs.example/{video_id}/120",
                "width": 120,
                "height": 120,
            },
        ],
        "isExplicit": False,
        **fields,
    }


def ytdlp_video(video_id: str, title: str = "Video Title", **fields) -> dict:
    """An entry of a flat yt-dlp ytsearch, in the shape it has."""
    return {
        "_type": "url",
        "ie_key": "Youtube",
        "id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "channel": "Channel Name",
        "uploader": "Uploader Name",
        "duration": 153,
        "thumbnails": [
            {
                "url": f"https://thumbs.example/{video_id}/360",
                "width": 360,
                "height": 202,
            },
            {
                "url": f"https://thumbs.example/{video_id}/720",
                "width": 720,
                "height": 404,
            },
        ],
        **fields,
    }


def answers(results: list[dict]) -> Callable[[str], list[dict]]:
    return lambda query: results


def fails(error: Exception) -> Callable[[str], list[dict]]:
    def client(query: str) -> list[dict]:
        raise error

    return client


def unused(query: str) -> list[dict]:
    raise AssertionError("this client should not be called")


def ytmusic_offline(query: str) -> list[dict]:
    raise requests.exceptions.ConnectionError("no route to host")


def ytdlp_offline(query: str) -> list[dict]:
    raise DownloadError("no route to host")


def find(query: str, library, ytmusic=ytmusic_offline, ytdlp=ytdlp_offline):
    return search.find(query, library.path, ytmusic, ytdlp)


def song_ids(found) -> list[str | None]:
    return [result.song_id for result in found.results]


def test_a_library_match_is_a_ready_result_with_what_its_manifest_holds(
    local_library,
) -> None:
    local_library.add(FIRST, "First Song", "First Artist", video_id="AAAAAAAAAAA")

    found = find("first", local_library)

    assert found.results == [
        Result(
            title="First Song",
            artist="First Artist",
            origin="library",
            video_id="AAAAAAAAAAA",
            album=None,
            duration_s=245.3,
            thumbnail_url=None,
            song_id=FIRST,
        )
    ]


@pytest.mark.parametrize(
    "query",
    ["cancao", "CANÇÃO", "Canção", "cançao exemplo", "AVO", "straße", "STRASSE"],
)
def test_library_matches_ignore_case_and_accents(local_library, query: str) -> None:
    local_library.add(FIRST, "Canção de Exemplo Straße", "Avô")

    assert song_ids(find(query, local_library)) == [FIRST]


def test_every_word_must_appear_in_the_title_or_the_artist(local_library) -> None:
    local_library.add(FIRST, "First Song", "First Artist")
    local_library.add(SECOND, "Second Song", "Second Artist")

    assert song_ids(find("song first", local_library)) == [FIRST]
    assert song_ids(find("  sec   artist ", local_library)) == [SECOND]
    assert song_ids(find("song", local_library)) == [FIRST, SECOND]
    assert song_ids(find("first missing", local_library)) == []


@pytest.mark.parametrize("query", ["", "   ", " \t\n ", "́"])
def test_a_query_without_words_finds_nothing_and_searches_nothing_online(
    local_library, query: str
) -> None:
    local_library.add(FIRST, "First Song", "First Artist")
    queries = []

    def client(query: str) -> list[dict]:
        queries.append(query)
        return [ytmusic_song("BBBBBBBBBBB")]

    found = find(query, local_library, ytmusic=client, ytdlp=client)

    assert (found.results, found.notice) == ([], None)
    assert queries == []


def test_a_song_from_a_local_file_matches_without_a_video_id(local_library) -> None:
    local_library.add(
        FIRST, "First Song", "First Artist", source={"type": "file", "sha256": "0" * 64}
    )

    [result] = find("first", local_library).results

    assert (result.song_id, result.video_id) == (FIRST, None)


def test_without_a_library_folder_only_online_results_are_listed(
    local_library,
) -> None:
    found = find("song", local_library, ytmusic=answers([ytmusic_song("BBBBBBBBBBB")]))

    assert [result.video_id for result in found.results] == ["BBBBBBBBBBB"]
    assert found.notice is None


def test_library_matches_come_before_youtube_music_results(local_library) -> None:
    local_library.add(FIRST, "First Song", "First Artist", video_id="AAAAAAAAAAA")
    ytmusic = answers(
        [
            ytmusic_song("BBBBBBBBBBB", "First Song", ("Cover Band", "Guest Singer")),
            ytmusic_song("CCCCCCCCCCC", "First Song (Live)", ("First Artist",)),
        ]
    )

    found = find("first song", local_library, ytmusic=ytmusic, ytdlp=unused)

    assert found.results == [
        Result(
            title="First Song",
            artist="First Artist",
            origin="library",
            video_id="AAAAAAAAAAA",
            duration_s=245.3,
            song_id=FIRST,
        ),
        Result(
            title="First Song",
            artist="Cover Band, Guest Singer",
            origin="ytmusic",
            video_id="BBBBBBBBBBB",
            album="Album Name",
            duration_s=126,
            thumbnail_url="https://thumbs.example/BBBBBBBBBBB/120",
        ),
        Result(
            title="First Song (Live)",
            artist="First Artist",
            origin="ytmusic",
            video_id="CCCCCCCCCCC",
            album="Album Name",
            duration_s=126,
            thumbnail_url="https://thumbs.example/CCCCCCCCCCC/120",
        ),
    ]
    assert found.notice is None


def test_the_query_goes_to_the_online_search_as_typed(local_library) -> None:
    queries = []

    def ytmusic(query: str) -> list[dict]:
        queries.append(query)
        return []

    def ytdlp(query: str) -> list[dict]:
        queries.append(query)
        return []

    find("  Sóng  Title ", local_library, ytmusic=ytmusic, ytdlp=ytdlp)

    assert queries == ["  Sóng  Title ", "  Sóng  Title "]


def test_an_online_result_already_in_the_library_shows_once_as_the_library_entry(
    local_library,
) -> None:
    local_library.add(FIRST, "First Song", "First Artist", video_id="AAAAAAAAAAA")
    ytmusic = answers([ytmusic_song("AAAAAAAAAAA", "First Song", ("First Artist",))])

    found = find("first", local_library, ytmusic=ytmusic)

    assert [(r.origin, r.song_id, r.video_id) for r in found.results] == [
        ("library", FIRST, "AAAAAAAAAAA")
    ]


def test_an_online_result_in_the_library_the_words_missed_joins_the_ready_ones(
    local_library,
) -> None:
    local_library.add(FIRST, "First Song", "First Artist", video_id="AAAAAAAAAAA")
    local_library.add(SECOND, "Second Song", "Second Artist", video_id="DDDDDDDDDDD")
    ytmusic = answers(
        [
            ytmusic_song("BBBBBBBBBBB", "Frist Song (Cover)"),
            ytmusic_song("DDDDDDDDDDD", "Second Song", ("Second Artist",)),
        ]
    )

    found = find("frist", local_library, ytmusic=ytmusic)

    assert [(r.origin, r.song_id, r.video_id) for r in found.results] == [
        ("library", SECOND, "DDDDDDDDDDD"),
        ("ytmusic", None, "BBBBBBBBBBB"),
    ]


def test_an_online_result_without_a_video_id_is_not_a_local_file_song(
    local_library,
) -> None:
    local_library.add(
        SECOND,
        "Second Song",
        "Second Artist",
        source={"type": "file", "sha256": "0" * 64},
    )
    ytmusic = answers([ytmusic_song(None, "First Song")])

    found = find("first", local_library, ytmusic=ytmusic)

    assert [(r.origin, r.song_id, r.video_id) for r in found.results] == [
        ("ytmusic", None, None)
    ]


def test_youtube_music_gives_at_most_10_results(local_library) -> None:
    ytmusic = answers([ytmusic_song(f"video{n:06d}") for n in range(20)])

    found = find("song", local_library, ytmusic=ytmusic)

    assert [result.video_id for result in found.results] == [
        f"video{n:06d}" for n in range(10)
    ]


def test_a_youtube_music_result_without_album_duration_or_thumbnails(
    local_library,
) -> None:
    song = ytmusic_song("BBBBBBBBBBB", album=None, thumbnails=[])
    del song["duration_seconds"]
    del song["duration"]

    [result] = find("song", local_library, ytmusic=answers([song])).results

    assert (result.album, result.duration_s, result.thumbnail_url) == (None, None, None)


def test_a_youtube_music_result_without_those_keys_at_all(local_library) -> None:
    song = ytmusic_song("BBBBBBBBBBB")
    for key in ("album", "duration", "duration_seconds", "thumbnails"):
        del song[key]

    [result] = find("song", local_library, ytmusic=answers([song])).results

    assert (result.album, result.duration_s, result.thumbnail_url) == (None, None, None)


def test_the_largest_thumbnail_is_chosen(local_library) -> None:
    song = ytmusic_song(
        "BBBBBBBBBBB",
        thumbnails=[
            {"url": "https://thumbs.example/226", "width": 226, "height": 226},
            {"url": "https://thumbs.example/544", "width": 544, "height": 544},
            {"url": "https://thumbs.example/60", "width": 60, "height": 60},
            {"url": "https://thumbs.example/unsized"},
        ],
    )

    [result] = find("song", local_library, ytmusic=answers([song])).results

    assert result.thumbnail_url == "https://thumbs.example/544"


def test_a_youtube_music_failure_falls_back_to_yt_dlp(local_library, caplog) -> None:
    ytmusic = fails(YTMusicServerError("music service down"))
    ytdlp = answers([ytdlp_video("EEEEEEEEEEE", "Video Title")])

    with caplog.at_level(logging.WARNING, logger="stage"):
        found = find("song", local_library, ytmusic=ytmusic, ytdlp=ytdlp)

    assert found.results == [
        Result(
            title="Video Title",
            artist="Channel Name",
            origin="yt-dlp",
            video_id="EEEEEEEEEEE",
            album=None,
            duration_s=153,
            thumbnail_url="https://thumbs.example/EEEEEEEEEEE/720",
        )
    ]
    assert found.notice is None
    assert caplog.messages == [
        "YouTube Music search failed (music service down); searching with yt-dlp"
    ]


def test_a_youtube_music_answer_in_an_unknown_shape_falls_back_to_yt_dlp(
    local_library,
) -> None:
    ytmusic = answers([{"unexpected": "shape"}])
    ytdlp = answers([ytdlp_video("EEEEEEEEEEE")])

    found = find("song", local_library, ytmusic=ytmusic, ytdlp=ytdlp)

    assert [result.origin for result in found.results] == ["yt-dlp"]


def test_an_empty_youtube_music_answer_falls_back_to_yt_dlp(
    local_library, caplog
) -> None:
    with caplog.at_level(logging.WARNING, logger="stage"):
        found = find(
            "song",
            local_library,
            ytmusic=answers([]),
            ytdlp=answers([ytdlp_video("EEEEEEEEEEE")]),
        )

    assert [result.video_id for result in found.results] == ["EEEEEEEEEEE"]
    assert caplog.messages == []


def test_yt_dlp_gives_at_most_10_results(local_library) -> None:
    ytdlp = answers([ytdlp_video(f"video{n:06d}") for n in range(12)])

    found = find("song", local_library, ytmusic=answers([]), ytdlp=ytdlp)

    assert len(found.results) == 10


def test_a_yt_dlp_result_without_channel_duration_or_thumbnails(local_library) -> None:
    video = ytdlp_video("EEEEEEEEEEE", channel=None, duration=None)
    del video["thumbnails"]

    [result] = find(
        "song", local_library, ytmusic=answers([]), ytdlp=answers([video])
    ).results

    assert (result.artist, result.duration_s, result.thumbnail_url) == ("", None, None)


def test_a_yt_dlp_result_already_in_the_library_shows_as_the_library_entry(
    local_library,
) -> None:
    local_library.add(FIRST, "First Song", "First Artist", video_id="AAAAAAAAAAA")
    ytdlp = answers([ytdlp_video("AAAAAAAAAAA"), ytdlp_video("EEEEEEEEEEE")])

    found = find("first", local_library, ytmusic=answers([]), ytdlp=ytdlp)

    assert [(r.origin, r.song_id) for r in found.results] == [
        ("library", FIRST),
        ("yt-dlp", None),
    ]


def test_when_both_searches_fail_the_library_matches_come_with_a_notice(
    local_library, caplog
) -> None:
    local_library.add(FIRST, "First Song", "First Artist")

    with caplog.at_level(logging.WARNING, logger="stage"):
        found = find(
            "first",
            local_library,
            ytmusic=fails(YTMusicServerError("music service down")),
            ytdlp=fails(DownloadError("no route to host")),
        )

    assert song_ids(found) == [FIRST]
    assert found.notice == search.OFFLINE_NOTICE
    assert caplog.messages == [
        "YouTube Music search failed (music service down); searching with yt-dlp",
        "yt-dlp search failed (no route to host)",
    ]


def test_a_yt_dlp_failure_after_an_empty_youtube_music_answer_gives_the_notice(
    local_library,
) -> None:
    found = find(
        "song",
        local_library,
        ytmusic=answers([]),
        ytdlp=fails(DownloadError("offline")),
    )

    assert (found.results, found.notice) == ([], search.OFFLINE_NOTICE)


def test_no_result_anywhere_is_not_a_failure(local_library) -> None:
    found = find("song", local_library, ytmusic=answers([]), ytdlp=answers([]))

    assert (found.results, found.notice) == ([], None)


def test_ytmusic_songs_searches_the_songs_filter(monkeypatch) -> None:
    calls = []

    class FakeYTMusic:
        def search(self, query: str, filter: str) -> list[dict]:
            calls.append((query, filter))
            return [ytmusic_song("BBBBBBBBBBB")]

    monkeypatch.setattr(search, "YTMusic", FakeYTMusic)

    assert search.ytmusic_songs("song title") == [ytmusic_song("BBBBBBBBBBB")]
    assert calls == [("song title", "songs")]


def test_ytdlp_videos_lists_10_hits_flat_without_downloading(monkeypatch) -> None:
    calls = []

    class FakeYoutubeDL:
        def __init__(self, options: dict) -> None:
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            pass

        def extract_info(self, url: str, download: bool) -> dict:
            calls.append((url, download))
            return {"_type": "playlist", "entries": iter([ytdlp_video("EEEEEEEEEEE")])}

    monkeypatch.setattr(search.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    assert search.ytdlp_videos("song title") == [ytdlp_video("EEEEEEEEEEE")]
    options, request = calls
    assert request == ("ytsearch10:song title", False)
    assert options["extract_flat"] is True
    assert options["quiet"] is True
    assert options["skip_download"] is True
    assert options["extractor_retries"] == 1


def test_ytdlp_videos_keeps_yt_dlp_from_printing(monkeypatch, capfd) -> None:
    created = []

    class RecordingYoutubeDL(yt_dlp.YoutubeDL):
        def __init__(self, options: dict) -> None:
            super().__init__(options)
            created.append(self)

        def extract_info(self, url: str, download: bool) -> dict:
            return {"entries": []}

    monkeypatch.setattr(search.yt_dlp, "YoutubeDL", RecordingYoutubeDL)
    search.ytdlp_videos("song title")
    [ydl] = created

    ydl.report_warning("a warning")
    with pytest.raises(DownloadError):
        ydl.report_error("an error")

    assert capfd.readouterr() == ("", "")
