from pathlib import Path

import pytest
from core.manifest import Manifest, YouTubeMusicSource
from studio import cli
from studio.package import song_id

VIDEO_ID = "XXXXXXXXXXX"
SOURCE = YouTubeMusicSource(type="youtube_music", video_id=VIDEO_ID)


def build(fake_tools, library: Path, **song) -> tuple[Path, list[tuple]]:
    stages: list[tuple] = []
    package = cli.build_song(
        library,
        Path("models"),
        fake_tools.tools(),
        source=SOURCE,
        title=song.pop("title", "Song Title"),
        artist=song.pop("artist", "Artist Name"),
        on_stage=lambda status, step=None: stages.append((status, step)),
        **song,
    )
    return package, stages


def test_build_song_reports_each_stage_as_it_starts(tmp_path: Path, fake_tools):
    package, stages = build(fake_tools, tmp_path)

    assert stages == [
        ("downloading", None),
        ("separating", "vocals"),
        ("separating", "karaoke"),
        ("separating", "instruments"),
        ("lyrics", None),
        ("packaging", None),
    ]
    assert package == tmp_path / song_id("Song Title", "Artist Name", VIDEO_ID)
    assert Manifest.load(package / "manifest.json").source == SOURCE
    assert not any(call[0] == "fetch_info" for call in fake_tools.calls)


def test_stage_is_reported_before_the_step_runs(tmp_path: Path, fake_tools):
    order: list[str] = []
    separator = fake_tools.separator

    def recording_separator(step, model_file, audio):
        order.append(f"run {step}")
        return separator(step, model_file, audio)

    fake_tools.separator = recording_separator
    download = fake_tools.download

    def recording_download(video_id, out_dir):
        order.append("run download")
        return download(video_id, out_dir)

    fake_tools.download = recording_download

    cli.build_song(
        tmp_path,
        Path("models"),
        fake_tools.tools(),
        source=SOURCE,
        title="Song Title",
        artist="Artist Name",
        on_stage=lambda status, step=None: order.append(f"stage {status} {step}"),
    )

    assert order[:4] == [
        "stage downloading None",
        "run download",
        "stage separating vocals",
        "run vocals",
    ]


def test_lyrics_duration_falls_back_to_the_audio_length(
    tmp_path: Path, fake_tools, synthetic_mix
):
    fake_tools.mix = synthetic_mix(seconds=2.0)

    build(fake_tools, tmp_path)

    (lyrics_call,) = [call for call in fake_tools.calls if call[0] == "lyrics"]
    assert lyrics_call[2]["duration"] == 2


def test_given_duration_and_album_reach_the_lyrics_lookup(tmp_path: Path, fake_tools):
    build(fake_tools, tmp_path, album="Album Name", duration_s=245.3)

    (lyrics_call,) = [call for call in fake_tools.calls if call[0] == "lyrics"]
    assert lyrics_call[2]["duration"] == 245
    assert lyrics_call[2]["album_name"] == "Album Name"


def test_failure_leaves_nothing_in_the_library(tmp_path: Path, fake_tools):
    fake_tools.fail["download"] = OSError("HTTP Error 403: Forbidden")

    with pytest.raises(OSError, match="403"):
        build(fake_tools, tmp_path)

    assert list(tmp_path.iterdir()) == []
