import json
from pathlib import Path

from stage import library as lib
from stage.lyrics import Line, SongLyrics, parse_lrc, song_lyrics

SONG = "song-title--artist-name--a1b2c3"


def test_parse_lrc_reads_each_stamped_line() -> None:
    text = "[00:01.50] First line\n[01:02.25] Second line\n"

    assert parse_lrc(text) == [Line(1.5, "First line"), Line(62.25, "Second line")]


def test_parse_lrc_accepts_crlf_and_a_byte_order_mark() -> None:
    text = "\ufeff[00:01.00] First line\r\n[00:02.00] Second line\r\n"

    assert parse_lrc(text) == [Line(1.0, "First line"), Line(2.0, "Second line")]


def test_parse_lrc_gives_a_line_with_several_stamps_once_per_stamp() -> None:
    text = "[00:10.00][00:30.00] Chorus line\n[00:20.00] Verse line\n"

    assert parse_lrc(text) == [
        Line(10.0, "Chorus line"),
        Line(20.0, "Verse line"),
        Line(30.0, "Chorus line"),
    ]


def test_parse_lrc_reads_stamps_without_or_with_three_digit_fractions() -> None:
    text = "[00:01] No fraction\n[00:02.125] Milliseconds\n[00:03.5] One digit\n"

    assert parse_lrc(text) == [
        Line(1.0, "No fraction"),
        Line(2.125, "Milliseconds"),
        Line(3.5, "One digit"),
    ]


def test_parse_lrc_keeps_an_empty_line_and_skips_tags_and_blank_lines() -> None:
    text = "[ar:Artist Name]\n[ti:Song Title]\n\n[00:01.00] Sung\n[00:05.00]\n"

    assert parse_lrc(text) == [Line(1.0, "Sung"), Line(5.0, "")]


def test_parse_lrc_shifts_every_line_by_the_offset() -> None:
    text = "[00:01.00] First line\n[00:02.00] Second line\n"

    assert parse_lrc(text, offset_ms=250) == [
        Line(1.25, "First line"),
        Line(2.25, "Second line"),
    ]
    assert parse_lrc(text, offset_ms=-500) == [
        Line(0.5, "First line"),
        Line(1.5, "Second line"),
    ]


def add_song(library: Path, lyrics: dict | None, files: dict[str, bytes]) -> lib.Song:
    package = library / SONG
    package.mkdir(parents=True)
    manifest = {
        "schema": 1,
        "pipeline_version": 1,
        "id": SONG,
        "title": "Song Title",
        "artist": "Artist Name",
        "duration_s": 245.3,
        "source": {"type": "youtube_music", "video_id": "XXXXXXXXXXX"},
        "gain_db": -2.5,
        "stems": {},
        "models": {"vocals": "a", "karaoke": "b", "instrumental": "c"},
        "created_at": "2026-11-04T20:00:00Z",
    }
    if lyrics is not None:
        manifest["lyrics"] = lyrics
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, data in files.items():
        (package / name).write_bytes(data)
    return lib.scan(library)[SONG]


def test_synced_lyrics_of_a_song_apply_its_offset(tmp_path: Path) -> None:
    song = add_song(
        tmp_path,
        {"provider": "lrclib", "file": "lyrics.lrc", "synced": True, "offset_ms": 100},
        {"lyrics.lrc": b"[00:01.00] Line one\r\n"},
    )

    assert song_lyrics(song) == SongLyrics("synced", [Line(1.1, "Line one")])


def test_unsynced_lyrics_come_as_lines_without_times(tmp_path: Path) -> None:
    song = add_song(
        tmp_path,
        {"provider": "lrclib", "file": "lyrics.txt", "synced": False},
        {"lyrics.txt": b"Line one\r\n\r\nLine two\r\n"},
    )

    assert song_lyrics(song) == SongLyrics(
        "unsynced", [Line(None, "Line one"), Line(None, ""), Line(None, "Line two")]
    )


def test_a_song_without_lyrics_says_so(tmp_path: Path) -> None:
    song = add_song(tmp_path, None, {})

    assert song_lyrics(song) == SongLyrics("none", [])
