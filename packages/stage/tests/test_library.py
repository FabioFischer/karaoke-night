import json
import logging
import sys
from pathlib import Path

import pytest
from stage import library as lib

SONG = "song-title--artist-name--a1b2c3"


def write_song(library: Path, name: str, **fields) -> bytes:
    """A local package folder with only its manifest, written with CRLF on purpose."""
    folder = library / name
    folder.mkdir(parents=True)
    manifest = {
        "schema": 1,
        "pipeline_version": 1,
        "id": name,
        "title": "Song Title",
        "artist": "Artist Name",
        "duration_s": 245.3,
        "source": {"type": "youtube_music", "video_id": "XXXXXXXXXXX"},
        "gain_db": -2.5,
        "stems": {},
        "models": {"vocals": "a", "karaoke": "b", "instrumental": "c"},
        "created_at": "2026-11-04T20:00:00Z",
        "future_field": [1, 2],
        **fields,
    }
    raw = json.dumps(manifest, indent=2).replace("\n", "\r\n").encode("utf-8")
    (folder / "manifest.json").write_bytes(raw)
    return raw


@pytest.mark.parametrize(
    "song_id",
    [
        SONG,
        "--artist-name--a1b2c3",
        "song-title----0f9e8d",
        "a--b--000000",
    ],
)
def test_ids_in_the_shape_the_studio_generates_are_accepted(song_id: str) -> None:
    assert lib.is_song_id(song_id)


@pytest.mark.parametrize(
    "song_id",
    [
        "",
        ".",
        "..",
        "a/b",
        "a\\b",
        "C:x",
        ".hidden",
        "Song-Title--Artist-Name--a1b2c3",
        "song-title--artist-name--A1B2C3",
        "song.title--artist-name--a1b2c3",
        "song-title--artist-name--a1b2c3.",
        "song-title--artist-name--a1b2c3 ",
        "song-title--artist-name--a1b2c",
        "song-title--artist-name--a1b2c3d",
        "song-title--artist-name",
        "con",
        "../song-title--artist-name--a1b2c3",
        "song-title--artist-name--a1b2c3\n",
    ],
)
def test_other_ids_are_refused(song_id: str) -> None:
    assert not lib.is_song_id(song_id)


def test_scan_lists_packages_by_folder_with_the_manifest_as_stored(
    tmp_path: Path,
) -> None:
    raw = write_song(tmp_path, SONG)
    write_song(tmp_path, ".in-flight.partial")
    (tmp_path / "no-manifest--artist-name--a1b2c3").mkdir()
    (tmp_path / "loose-file.txt").write_bytes(b"not a package")

    songs = lib.scan(tmp_path)

    assert list(songs) == [SONG]
    song = songs[SONG]
    assert song.folder == tmp_path / SONG
    assert song.manifest_bytes == raw
    assert song.manifest.title == "Song Title"


def test_scan_of_a_missing_library_is_empty(tmp_path: Path) -> None:
    assert lib.scan(tmp_path / "missing") == {}


def test_a_package_with_an_invalid_manifest_is_skipped_with_a_warning(
    tmp_path: Path, caplog
) -> None:
    write_song(tmp_path, SONG)
    write_song(tmp_path, "broken-song--artist-name--a1b2c3", duration_s="long")

    with caplog.at_level(logging.WARNING, logger="stage"):
        songs = lib.scan(tmp_path)

    assert list(songs) == [SONG]
    assert caplog.messages == [
        (
            "broken-song--artist-name--a1b2c3 is not listed: invalid manifest.json "
            "(duration_s: Input should be a valid number, unable to parse string "
            "as a number)"
        )
    ]


def test_delete_renames_then_removes(tmp_path: Path) -> None:
    write_song(tmp_path, SONG)

    lib.delete(lib.scan(tmp_path)[SONG].folder)

    assert list(tmp_path.iterdir()) == []


def test_delete_of_a_busy_folder_keeps_it_whole(tmp_path: Path, refuse_rename) -> None:
    raw = write_song(tmp_path, SONG)
    refuse_rename(SONG)

    with pytest.raises(lib.Busy):
        lib.delete(tmp_path / SONG)

    assert [path.name for path in tmp_path.iterdir()] == [SONG]
    assert (tmp_path / SONG / "manifest.json").read_bytes() == raw


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks open files")
def test_delete_of_a_folder_with_a_really_open_file_is_busy(tmp_path: Path) -> None:
    write_song(tmp_path, SONG)

    with (tmp_path / SONG / "manifest.json").open("rb"), pytest.raises(lib.Busy):
        lib.delete(tmp_path / SONG)

    assert (tmp_path / SONG / "manifest.json").is_file()
