import json
from pathlib import Path

import httpx2
import pytest
from stage import cli

SONG = "song-title--artist-name--a1b2c3"
STEMS = ("lead", "backing", "drums", "bass", "guitar", "piano", "other")
PLAYING, PAUSED, STOPPED = 1, 2, 0


@pytest.fixture
def song(tmp_path: Path) -> Path:
    """A library holding one package of 7 stems, 3.4 s long."""
    library = tmp_path / "library"
    package = library / SONG
    package.mkdir(parents=True)
    manifest = {
        "schema": 1,
        "pipeline_version": 1,
        "id": SONG,
        "title": "Song Title",
        "artist": "Artist Name",
        "duration_s": 3.4,
        "source": {"type": "youtube_music", "video_id": "XXXXXXXXXXX"},
        "gain_db": 0.0,
        "stems": {
            stem: {"file": f"stems/{stem}.flac", "sha256": "0" * 64} for stem in STEMS
        },
        "models": {"vocals": "a", "karaoke": "b", "instrumental": "c"},
        "created_at": "2026-11-04T20:00:00Z",
    }
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return library


@pytest.fixture(autouse=True)
def no_wait(monkeypatch) -> None:
    monkeypatch.setattr(cli, "POLL_INTERVAL_S", 0)


def stage_play(reaper, library: Path, *options: str) -> int:
    return cli.main(
        ["play", SONG, "--library", str(library), *options], http=reaper.http()
    )


def playing_to_the_end(reaper) -> None:
    reaper.transport = [
        (PLAYING, 0.0),
        (PLAYING, 0.4),
        (PLAYING, 0.9),
        (PLAYING, 1.1),
        (PLAYING, 1.6),
        (PLAYING, 2.2),
        (PLAYING, 3.3),
        (STOPPED, 3.4),
    ]


def test_play_loads_mutes_transposes_then_plays_and_exits_0_at_the_end(
    reaper, song: Path, capsys
) -> None:
    playing_to_the_end(reaper)

    assert (
        stage_play(reaper, song, "--mute", "lead", "backing", "--transpose", "-2") == 0
    )

    load, transpose = reaper.sent
    assert load["op"] == "load"
    assert load["manifest"] == str((song / SONG / "manifest.json").resolve())
    assert transpose["op"] == "transpose"
    assert transpose["semitones"] == -2
    # Template order: Mic 1, Mic 2, then the stems from index 3.
    assert reaper.ops() == [
        "1016",
        "load",
        "SET/TRACK/3/MUTE/1",
        "SET/TRACK/4/MUTE/1",
        "SET/TRACK/5/MUTE/0",
        "SET/TRACK/6/MUTE/0",
        "SET/TRACK/7/MUTE/0",
        "SET/TRACK/8/MUTE/0",
        "SET/TRACK/9/MUTE/0",
        "transpose",
        "40042",
        "1007",
    ]
    assert capsys.readouterr().out.splitlines() == [
        "playing Song Title by Artist Name (0:03)",
        "0:00 / 0:03",
        "0:01 / 0:03",
        "0:02 / 0:03",
        "0:03 / 0:03",
        "the song ended",
    ]


def test_play_unmutes_every_stem_and_sends_transpose_0_by_default(
    reaper, song: Path
) -> None:
    playing_to_the_end(reaper)
    reaper.muted = {index: True for index in range(10)}

    assert stage_play(reaper, song) == 0

    assert [reaper.muted[index] for index in range(3, 10)] == [False] * 7
    assert [command["op"] for command in reaper.sent] == ["load", "transpose"]
    assert reaper.sent[1]["semitones"] == 0


def test_a_pause_in_reaper_keeps_following_until_the_song_ends(
    reaper, song: Path, capsys
) -> None:
    reaper.transport = [
        (PLAYING, 0.0),
        (PLAYING, 1.1),
        (PAUSED, 1.2),
        (PAUSED, 1.2),
        (PLAYING, 2.1),
        (STOPPED, 3.4),
    ]

    assert stage_play(reaper, song) == 0

    assert capsys.readouterr().out.splitlines() == [
        "playing Song Title by Artist Name (0:03)",
        "0:00 / 0:03",
        "0:01 / 0:03",
        "0:02 / 0:03",
        "the song ended",
    ]


def test_ctrl_c_stops_playback_and_exits_130(reaper, song: Path, capsys) -> None:
    reaper.transport = [(PLAYING, 0.0), (PLAYING, 0.5)]

    def interrupt(reads: int) -> None:
        if reads == 3:
            raise KeyboardInterrupt

    reaper.on_transport = interrupt

    assert stage_play(reaper, song) == 130

    assert reaper.commands[-1] == "1016"
    assert capsys.readouterr().out.splitlines()[-1] == "stopped"


def test_an_unknown_song_exits_1(reaper, song: Path, capsys) -> None:
    code = cli.main(
        ["play", "other-song--artist--d4e5f6", "--library", str(song)],
        http=reaper.http(),
    )

    assert code == 1
    assert capsys.readouterr().err == (
        f"stage play: no song 'other-song--artist--d4e5f6' in {song}; "
        "stage list shows the songs\n"
    )
    assert reaper.commands == []


def test_an_unknown_stem_is_a_usage_error_listing_the_song_stems(
    reaper, song: Path, capsys
) -> None:
    with pytest.raises(SystemExit) as exit_:
        stage_play(reaper, song, "--mute", "lead", "vocals")

    assert exit_.value.code == 2
    assert capsys.readouterr().err.splitlines()[-1] == (
        "stage play: error: argument --mute: unknown stem 'vocals'; the stems of "
        "this song are: lead, backing, drums, bass, guitar, piano, other"
    )
    assert reaper.commands == []


def test_a_missing_reaper_exits_1(song: Path, capsys) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    http = httpx2.Client(transport=httpx2.MockTransport(refuse))

    assert cli.main(["play", SONG, "--library", str(song)], http=http) == 1

    assert capsys.readouterr().err == (
        "stage play: could not reach REAPER's web interface at "
        "http://127.0.0.1:8080 (connection refused); is REAPER open, with the web "
        "interface on port 8080 (reaper/README.md)?\n"
    )


def test_a_silent_listener_exits_1(reaper, song: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "ACK_TIMEOUT_S", 0.05)
    reaper.acks = lambda command: [""]

    assert stage_play(reaper, song) == 1

    assert capsys.readouterr().err == (
        "stage play: the karaoke listener in REAPER did not answer the load command "
        "within 0.05 s; restart it: run reaper/scripts/karaoke_listener.lua from "
        "REAPER's action list, or restart REAPER\n"
    )
    assert "1007" not in reaper.commands


def test_a_failed_load_exits_1(reaper, song: Path, capsys) -> None:
    reaper.acks = lambda command: [
        json.dumps({"id": command["id"], "ok": False, "error": "cannot open stem"})
    ]

    assert stage_play(reaper, song) == 1

    assert capsys.readouterr().err == (
        "stage play: the karaoke listener in REAPER could not load: cannot open stem\n"
    )


def test_play_needs_a_song_id(capsys) -> None:
    with pytest.raises(SystemExit) as exit_:
        cli.main(["play"])

    assert exit_.value.code == 2
    assert "song_id" in capsys.readouterr().err
