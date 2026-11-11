"""Tests for search.py. No network: every client is a fake passed in.

Run from the repo root: python -m unittest discover -s spikes/s4-search -v
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import search


class ParseSongs(unittest.TestCase):
    def test_splits_on_the_last_separator(self):
        songs = search.parse_songs("Song Title - Part Two - Artist Name\n")
        self.assertEqual(songs, [{
            "line": "Song Title - Part Two - Artist Name",
            "title": "Song Title - Part Two",
            "artist": "Artist Name",
            "query": "Song Title - Part Two Artist Name",
        }])

    def test_skips_comments_and_blank_lines(self):
        text = "# a comment\n\n   \n  # indented comment\nSong Title - Artist Name\n"
        self.assertEqual([s["line"] for s in search.parse_songs(text)], ["Song Title - Artist Name"])

    def test_keeps_text_as_typed(self):
        song = search.parse_songs("song titel (Live)  -  artst name\n")[0]
        self.assertEqual(song["title"], "song titel (Live) ")
        self.assertEqual(song["artist"], " artst name")
        self.assertEqual(song["line"], "song titel (Live)  -  artst name")

    def test_hyphen_without_spaces_does_not_split(self):
        song = search.parse_songs("Song-Title - Artist-Name")[0]
        self.assertEqual((song["title"], song["artist"]), ("Song-Title", "Artist-Name"))

    def test_line_without_separator_is_all_title(self):
        song = search.parse_songs("Song Title Only")[0]
        self.assertEqual((song["title"], song["artist"], song["query"]), ("Song Title Only", "", "Song Title Only"))

    def test_strips_a_byte_order_mark_and_line_endings(self):
        songs = search.parse_songs("\ufeffSong Title - Artist Name\r\nOther Song - Other Artist\r\n")
        self.assertEqual([(s["line"], s["title"], s["artist"], s["query"]) for s in songs], [
            ("Song Title - Artist Name", "Song Title", "Artist Name", "Song Title Artist Name"),
            ("Other Song - Other Artist", "Other Song", "Other Artist", "Other Song Other Artist"),
        ])


def record(**fields):
    base = {
        "id": 1, "trackName": "Song Title", "artistName": "Artist Name", "albumName": "Album Name",
        "duration": 200.0, "instrumental": False, "plainLyrics": None, "syncedLyrics": None,
    }
    return {**base, **fields}


class Classify(unittest.TestCase):
    def test_synced_wins_over_plain(self):
        self.assertEqual(search.classify(record(plainLyrics="a\nb", syncedLyrics="[00:01.00] a")), "synced")

    def test_plain_only(self):
        self.assertEqual(search.classify(record(plainLyrics="a\nb")), "plain")

    def test_instrumental(self):
        self.assertEqual(search.classify(record(instrumental=True)), "instrumental")

    def test_empty_lyrics_are_none(self):
        self.assertEqual(search.classify(record(plainLyrics="", syncedLyrics="  \n")), "none")

    def test_no_record_is_none(self):
        self.assertEqual(search.classify(None), "none")


class FakeLrclib:
    """Answers each path from a table of (status, body), and records the calls."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, params))
        return self.replies[path]


NOT_FOUND = (404, {"message": "Failed to find specified track", "name": "TrackNotFound", "statusCode": 404})


class FindLyrics(unittest.TestCase):
    def find(self, lrclib, duration=200):
        return search.find_lyrics(lrclib, "Song Title", "Artist Name", "Album Name", duration)

    def test_get_hit(self):
        lrclib = FakeLrclib({"/api/get": (200, record(id=7, syncedLyrics="[00:01.00] a"))})
        result = self.find(lrclib)
        self.assertEqual((result["status"], result["route"], result["record"]["id"]), ("synced", "get", 7))
        self.assertEqual(lrclib.calls, [("/api/get", {
            "track_name": "Song Title", "artist_name": "Artist Name", "album_name": "Album Name", "duration": 200,
        })])

    def test_get_404_then_search_hit_within_tolerance(self):
        hits = [record(id=8, duration=260.0, plainLyrics="x"), record(id=9, duration=202.0, plainLyrics="y")]
        lrclib = FakeLrclib({"/api/get": NOT_FOUND, "/api/search": (200, hits)})
        result = self.find(lrclib)
        self.assertEqual((result["status"], result["route"], result["record"]["id"]), ("plain", "search", 9))
        self.assertEqual(lrclib.calls[1], ("/api/search", {"track_name": "Song Title", "artist_name": "Artist Name"}))
        self.assertEqual(result["search_hits"], 2)

    def test_get_record_without_lyrics_falls_through_to_search(self):
        hits = [record(id=6, duration=201.0, syncedLyrics="[00:01.00] a")]
        lrclib = FakeLrclib({"/api/get": (200, record(id=5)), "/api/search": (200, hits)})
        result = self.find(lrclib)
        self.assertEqual((result["status"], result["route"], result["record"]["id"]), ("synced", "search", 6))
        self.assertEqual([path for path, _ in lrclib.calls], ["/api/get", "/api/search"])

    def test_get_record_without_lyrics_is_kept_when_search_has_nothing_better(self):
        hits = [record(id=6, duration=200.0)]
        lrclib = FakeLrclib({"/api/get": (200, record(id=5)), "/api/search": (200, hits)})
        result = self.find(lrclib)
        self.assertEqual((result["status"], result["route"], result["record"]["id"], result["search_hits"]), ("none", "get", 5, 1))

    def test_get_record_with_plain_lyrics_does_not_search(self):
        lrclib = FakeLrclib({"/api/get": (200, record(id=5, plainLyrics="a"))})
        result = self.find(lrclib)
        self.assertEqual((result["status"], result["route"]), ("plain", "get"))
        self.assertEqual(len(lrclib.calls), 1)

    def test_search_picks_the_best_hit_within_tolerance(self):
        cases = [
            ([record(id=1), record(id=2, plainLyrics="x"), record(id=3, syncedLyrics="[00:01.00] a")], ("synced", 3)),
            ([record(id=1, instrumental=True), record(id=2, plainLyrics="x")], ("plain", 2)),
            ([record(id=1), record(id=2, instrumental=True)], ("instrumental", 2)),
            ([record(id=1, plainLyrics="x"), record(id=2, plainLyrics="y")], ("plain", 1)),
        ]
        for hits, expected in cases:
            with self.subTest(expected=expected):
                lrclib = FakeLrclib({"/api/get": NOT_FOUND, "/api/search": (200, hits)})
                result = self.find(lrclib)
                self.assertEqual((result["status"], result["record"]["id"]), expected)

    def test_a_better_hit_outside_the_tolerance_does_not_count(self):
        hits = [record(id=1, plainLyrics="x"), record(id=2, duration=200.0 + search.DURATION_TOLERANCE_S + 1, syncedLyrics="[00:01.00] a")]
        lrclib = FakeLrclib({"/api/get": NOT_FOUND, "/api/search": (200, hits)})
        result = self.find(lrclib)
        self.assertEqual((result["status"], result["record"]["id"]), ("plain", 1))

    def test_search_hits_with_the_wrong_duration_are_none(self):
        hits = [record(duration=200.0 + search.DURATION_TOLERANCE_S + 1, syncedLyrics="[00:01.00] a")]
        lrclib = FakeLrclib({"/api/get": NOT_FOUND, "/api/search": (200, hits)})
        result = self.find(lrclib)
        self.assertEqual((result["status"], result["route"], result["record"]), ("none", None, None))
        self.assertEqual(result["search_hits"], 1)

    def test_nothing_anywhere(self):
        lrclib = FakeLrclib({"/api/get": NOT_FOUND, "/api/search": (200, [])})
        result = self.find(lrclib)
        self.assertEqual((result["status"], result["route"], result["search_hits"]), ("none", None, 0))

    def test_without_album_or_duration_they_are_not_sent_and_the_first_hit_counts(self):
        lrclib = FakeLrclib({"/api/get": NOT_FOUND, "/api/search": (200, [record(id=3, duration=999.0, plainLyrics="x")])})
        result = search.find_lyrics(lrclib, "Song Title", "Artist Name", None, None)
        self.assertEqual(lrclib.calls[0], ("/api/get", {"track_name": "Song Title", "artist_name": "Artist Name"}))
        self.assertEqual((result["route"], result["record"]["id"]), ("search", 3))

    def test_other_status_is_an_error(self):
        lrclib = FakeLrclib({"/api/get": (503, None)})
        with self.assertRaises(search.LrclibError):
            self.find(lrclib)

    def test_record_summary_leaves_the_lyrics_text_out(self):
        lrclib = FakeLrclib({"/api/get": (200, record(syncedLyrics="[00:01.00] a\n[00:02.00] b", plainLyrics="a\nb"))})
        summary = self.find(lrclib)["record"]
        self.assertNotIn("syncedLyrics", summary)
        self.assertNotIn("plainLyrics", summary)
        self.assertEqual((summary["synced_lines"], summary["plain_lines"]), (2, 2))


YTMUSIC_RESULT = {
    "resultType": "song", "videoId": "vid1", "title": "Song Title",
    "artists": [{"name": "Artist Name", "id": "a1"}, {"name": "Guest Name", "id": "a2"}],
    "album": {"name": "Album Name", "id": "b1"}, "duration": "3:20", "duration_seconds": 200,
}


class YtmusicSearch(unittest.TestCase):
    def test_keeps_the_first_three_and_their_fields(self):
        calls = []

        def fake_search(query, filter):
            calls.append((query, filter))
            return [dict(YTMUSIC_RESULT, videoId=f"vid{i}") for i in range(20)]

        results = search.ytmusic_top(fake_search, "Song Title Artist Name")
        self.assertEqual(calls, [("Song Title Artist Name", "songs")])
        self.assertEqual([r["videoId"] for r in results], ["vid0", "vid1", "vid2"])
        self.assertEqual(results[0], {
            "videoId": "vid0", "title": "Song Title", "artists": ["Artist Name", "Guest Name"],
            "album": "Album Name", "duration_seconds": 200,
        })

    def test_missing_album(self):
        result = search.ytmusic_top(lambda query, filter: [dict(YTMUSIC_RESULT, album=None)], "q")[0]
        self.assertIsNone(result["album"])


class YtdlpSearch(unittest.TestCase):
    def test_maps_flat_entries(self):
        info = {"entries": [
            {"id": "v1", "title": "Song Title (Official Video)", "channel": "Artist Name", "duration": 201.0},
            {"id": "v2", "title": "Song Title (Live)", "channel": None, "uploader": "Some Uploader", "duration": None},
        ]}
        calls = []

        def fake_extract(url):
            calls.append(url)
            return info

        results = search.ytdlp_top(fake_extract, "Song Title Artist Name")
        self.assertEqual(calls, ["ytsearch3:Song Title Artist Name"])
        self.assertEqual(results, [
            {"id": "v1", "title": "Song Title (Official Video)", "channel": "Artist Name", "duration": 201.0},
            {"id": "v2", "title": "Song Title (Live)", "channel": "Some Uploader", "duration": None},
        ])


class Render(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(search.mmss(200), "3:20")
        self.assertEqual(search.mmss(65.6), "1:06")
        self.assertEqual(search.mmss(None), "?")

    def test_ytmusic_cell(self):
        result = {"title": "Song Title", "artists": ["Artist Name", "Guest Name"], "album": "Album Name", "duration_seconds": 200}
        self.assertEqual(search.ytmusic_cell(result), "Song Title · Artist Name, Guest Name · Album Name · 3:20")

    def test_cell_without_album(self):
        result = {"title": "Song Title", "artists": ["Artist Name"], "album": None, "duration_seconds": 200}
        self.assertEqual(search.ytmusic_cell(result), "Song Title · Artist Name · (no album) · 3:20")

    def song_result(self, **overrides):
        base = {
            "line": "Song Title - Artist Name",
            "ytmusic": {"seconds": 1.0, "error": None, "results": [
                {"title": "Song Title", "artists": ["Artist Name"], "album": "Album Name", "duration_seconds": 200},
                {"title": "Song | Title", "artists": ["Artist Name"], "album": None, "duration_seconds": 245},
            ]},
            "ytdlp": {"seconds": 1.0, "error": None, "results": [
                {"id": "v1", "title": "Song Title (Live)", "channel": "Artist Name", "duration": 260.0},
            ]},
            "lrclib": {"seconds": 0.5, "error": None, "status": "synced", "route": "search", "record": {"id": 1}, "search_hits": 3},
        }
        return {**base, **overrides}

    def test_row(self):
        self.assertEqual(search.markdown_row(self.song_result()), (
            "| Song Title - Artist Name"
            " | Song Title · Artist Name · Album Name · 3:20"
            " | Song \\| Title · Artist Name · (no album) · 4:05"
            " | "
            " | Song Title (Live) · Artist Name · 4:20"
            " | synced (search)"
            " |  |"
        ))

    def test_row_with_errors_and_unchecked_lyrics(self):
        result = self.song_result(
            ytmusic={"seconds": 0.1, "error": "URLError: offline", "results": []},
            ytdlp={"seconds": 0.1, "error": "DownloadError: offline", "results": []},
            lrclib={"seconds": None, "error": None, "status": None, "route": None, "record": None, "search_hits": None},
        )
        self.assertEqual(search.markdown_row(result), (
            "| Song Title - Artist Name | error: URLError: offline |  |  | error: DownloadError: offline | not checked |  |"
        ))

    def test_lyrics_cell_variants(self):
        self.assertEqual(search.lyrics_cell({"status": "plain", "route": "get", "error": None}), "plain (get)")
        self.assertEqual(search.lyrics_cell({"status": "none", "route": None, "error": None}), "none")
        self.assertEqual(search.lyrics_cell({"status": None, "route": None, "error": "LrclibError: 503"}), "error: LrclibError: 503")

    def test_table_header_has_a_verdict_column(self):
        header = search.markdown_table([self.song_result()]).splitlines()[0]
        self.assertTrue(header.startswith("| Line as typed |"))
        self.assertTrue(header.endswith("| Verdict |"))


class Run(unittest.TestCase):
    SONGS = search.parse_songs("Song Title - Artist Name\nOther Song - Other Artist\n")

    def test_an_error_on_one_song_does_not_stop_the_run(self):
        def ytmusic(query, filter):
            if query.startswith("Song Title"):
                raise OSError("network down")
            return [YTMUSIC_RESULT]

        lrclib = FakeLrclib({"/api/get": (200, record(syncedLyrics="[00:01.00] a"))})
        results = search.run(self.SONGS, ytmusic, lambda url: {"entries": []}, lrclib, pause_s=0)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["ytmusic"]["error"], "OSError: network down")
        self.assertIsNone(results[0]["lrclib"]["status"])
        self.assertEqual(results[1]["lrclib"]["status"], "synced")
        self.assertEqual(len(lrclib.calls), 1)

    def test_lyrics_are_looked_up_with_the_first_ytmusic_result(self):
        lrclib = FakeLrclib({"/api/get": NOT_FOUND, "/api/search": (200, [])})
        search.run(self.SONGS[:1], lambda query, filter: [YTMUSIC_RESULT], lambda url: {"entries": []}, lrclib, pause_s=0)
        self.assertEqual(lrclib.calls[0][1], {
            "track_name": "Song Title", "artist_name": "Artist Name, Guest Name", "album_name": "Album Name", "duration": 200,
        })

    def test_a_lyrics_error_is_recorded(self):
        lrclib = FakeLrclib({"/api/get": (500, None)})
        result = search.run(self.SONGS[:1], lambda query, filter: [YTMUSIC_RESULT], lambda url: {"entries": []}, lrclib, pause_s=0)[0]
        self.assertIsNone(result["lrclib"]["status"])
        self.assertIn("LrclibError", result["lrclib"]["error"])


class Summary(unittest.TestCase):
    def test_counts_and_medians(self):
        def song(status, error=None, yt=1.0, dl=2.0, lr=0.5):
            return {
                "ytmusic": {"seconds": yt}, "ytdlp": {"seconds": dl},
                "lrclib": {"seconds": lr, "status": status, "error": error},
            }

        results = [song("synced", yt=1.0), song("synced", yt=3.0), song("plain", yt=2.0), song(None, error="boom", lr=None)]
        summary = search.summarize(results)
        self.assertEqual(summary["songs"], 4)
        self.assertEqual(summary["lrclib"], {"synced": 2, "plain": 1, "instrumental": 0, "none": 0, "not checked": 0, "error": 1})
        self.assertEqual(summary["median_seconds"]["ytmusic"], 1.5)
        self.assertEqual(summary["median_seconds"]["lrclib"], 0.5)


class Main(unittest.TestCase):
    def test_writes_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            songs = Path(tmp) / "songs.txt"
            songs.write_text("# test\nSong Title - Artist Name\n", encoding="utf-8")
            clients = (
                lambda query, filter: [YTMUSIC_RESULT],
                lambda url: {"entries": []},
                FakeLrclib({"/api/get": (200, record(plainLyrics="a"))}),
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                search.main(["--songs", str(songs), "--out", tmp], clients=clients)
            data = json.loads((Path(tmp) / "results.local.json").read_text(encoding="utf-8"))
            self.assertEqual(data["songs"][0]["line"], "Song Title - Artist Name")
            table = (Path(tmp) / "results.local.md").read_text(encoding="utf-8")
            self.assertIn("| Song Title - Artist Name | Song Title · Artist Name, Guest Name", table)
            self.assertIn("plain: 1", out.getvalue())


if __name__ == "__main__":
    unittest.main()
