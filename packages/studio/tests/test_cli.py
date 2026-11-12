import hashlib
import re
import urllib.error
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from core.manifest import FileSource, Manifest, YouTubeMusicSource
from studio import cli
from studio.audio import SAMPLE_RATE
from studio.package import sha256_file, song_id

VIDEO_ID = "XXXXXXXXXXX"
INFO = {
    "id": VIDEO_ID,
    "title": "Song Title (Official Audio)",
    "track": "Song Title",
    "artists": ["Artist Name"],
    "album": "Album Name",
    "duration": 1,
}
SYNCED = {"syncedLyrics": "[00:01.00] Line one", "plainLyrics": "Line one"}


class Recorder:
    """Fakes for every outside tool the command uses, with what each was called with."""

    def __init__(self, mix: np.ndarray, separator, lyrics_routes=None):
        self.mix = mix
        self.separator = separator
        self.lyrics_routes = lyrics_routes or {"/api/get": (200, SYNCED)}
        self.calls: list[tuple] = []

    def find_spec(self, name: str):
        return object()

    def fetch_info(self, video_id: str) -> dict:
        self.calls.append(("fetch_info", video_id))
        return INFO

    def download(self, video_id: str, out_dir: Path) -> Path:
        self.calls.append(("download", video_id))
        path = out_dir / "download.webm"
        path.write_bytes(b"compressed audio")
        return path

    def convert(self, source: Path, target: Path) -> None:
        self.calls.append(("convert", source.name))
        sf.write(target, self.mix, SAMPLE_RATE, subtype="PCM_16")

    def make_runner(self, model_dir, work_dir: Path):
        self.calls.append(("make_runner", model_dir))
        return self.separator

    def lyrics_get(self, path: str, params: dict):
        self.calls.append(("lyrics", path))
        answer = self.lyrics_routes[path]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def tools(self) -> cli.Tools:
        return cli.Tools(
            find_spec=self.find_spec,
            fetch_info=self.fetch_info,
            download=self.download,
            convert=self.convert,
            make_runner=self.make_runner,
            lyrics_get=self.lyrics_get,
        )


@pytest.fixture
def recorder(mix, fake_separator) -> Recorder:
    return Recorder(mix, fake_separator())


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "input" / "song.m4a"
    path.parent.mkdir()
    path.write_bytes(b"placeholder audio bytes")
    return path


def run(recorder: Recorder, *args: str) -> int:
    return cli.main(["process", *args], tools=recorder.tools())


def test_youtube_video_becomes_a_package(
    tmp_path: Path, recorder: Recorder, capsys
) -> None:
    library = tmp_path / "library"

    code = run(
        recorder,
        f"https://music.youtube.com/watch?v={VIDEO_ID}&list=X",
        "--library",
        str(library),
    )

    assert code == 0
    expected_id = song_id("Song Title", "Artist Name", VIDEO_ID)
    assert [p.name for p in library.iterdir()] == [expected_id]
    manifest = Manifest.load(library / expected_id / "manifest.json")
    assert manifest.id == expected_id
    assert (manifest.title, manifest.artist) == ("Song Title", "Artist Name")
    assert manifest.source == YouTubeMusicSource(
        type="youtube_music", video_id=VIDEO_ID
    )
    for stem in manifest.stems.values():
        assert stem.sha256 == sha256_file(library / expected_id / stem.file)
    assert ("download", VIDEO_ID) in recorder.calls
    assert ("convert", "download.webm") in recorder.calls
    assert sorted(p.name for p in (library / expected_id).iterdir()) == [
        "lyrics.lrc",
        "manifest.json",
        "stems",
    ]
    out = capsys.readouterr().out
    assert "peak VRAM" in out
    assert "null test: max abs error" in out
    assert "residual RMS" in out
    assert str(library / expected_id / "manifest.json") in out


def test_null_test_of_the_written_package_is_rounding_only(
    tmp_path: Path, recorder: Recorder, capsys
) -> None:
    run(recorder, VIDEO_ID, "--library", str(tmp_path))

    out = capsys.readouterr().out
    match = re.search(r"residual RMS (-?\d+\.\d) dBFS", out)
    assert match is not None
    assert float(match.group(1)) < -80


def test_local_file_becomes_a_package(
    tmp_path: Path, recorder: Recorder, audio_file: Path
) -> None:
    library = tmp_path / "library"

    code = run(
        recorder,
        str(audio_file),
        "--title",
        "Song Title",
        "--artist",
        "Artist Name",
        "--library",
        str(library),
    )

    assert code == 0
    file_sha256 = hashlib.sha256(audio_file.read_bytes()).hexdigest()
    expected_id = song_id("Song Title", "Artist Name", file_sha256)
    manifest = Manifest.load(library / expected_id / "manifest.json")
    assert manifest.source == FileSource(type="file", sha256=file_sha256)
    assert ("convert", "song.m4a") in recorder.calls
    assert not any(call[0] in ("fetch_info", "download") for call in recorder.calls)
    lyrics_request = [call for call in recorder.calls if call[0] == "lyrics"]
    assert lyrics_request == [("lyrics", "/api/get")]


@pytest.mark.parametrize("missing", ["--title", "--artist"])
def test_local_file_without_title_or_artist_fails_clearly(
    tmp_path: Path, recorder: Recorder, audio_file: Path, capsys, missing: str
) -> None:
    given = {"--title": "Song Title", "--artist": "Artist Name"}
    del given[missing]
    args = [part for pair in given.items() for part in pair]

    with pytest.raises(SystemExit) as caught:
        run(recorder, str(audio_file), *args, "--library", str(tmp_path / "library"))

    assert caught.value.code == 2
    assert "a local file needs --title and --artist" in capsys.readouterr().err
    assert recorder.calls == []
    assert not (tmp_path / "library").exists()


@pytest.mark.parametrize(
    "target", ["not-a-file.flac", "https://example.com/song", "XXXXXXXXXX"]
)
def test_target_that_is_no_file_url_or_video_id_fails(
    tmp_path: Path, recorder: Recorder, capsys, target: str
) -> None:
    with pytest.raises(SystemExit) as caught:
        run(recorder, target, "--library", str(tmp_path))

    assert caught.value.code == 2
    assert "not a file, a YouTube URL or a video id" in capsys.readouterr().err


def test_already_processed_song_is_left_untouched(
    tmp_path: Path, recorder: Recorder, capsys
) -> None:
    package = tmp_path / song_id("Song Title", "Artist Name", VIDEO_ID)
    package.mkdir()
    (package / "manifest.json").write_text("existing", encoding="utf-8")

    code = run(recorder, VIDEO_ID, "--library", str(tmp_path))

    assert code == 0
    assert capsys.readouterr().out.strip().endswith(str(package / "manifest.json"))
    assert (package / "manifest.json").read_text(encoding="utf-8") == "existing"
    assert [p.name for p in tmp_path.iterdir()] == [package.name]
    assert recorder.calls == [("fetch_info", VIDEO_ID)]


def test_failure_mid_way_leaves_nothing_in_the_library(
    tmp_path: Path, recorder: Recorder
) -> None:
    separator = recorder.separator

    def crash_on_instruments(step, model_file, audio):
        if step == "instruments":
            raise RuntimeError("model crashed")
        return separator(step, model_file, audio)

    recorder.separator = crash_on_instruments
    library = tmp_path / "library"

    with pytest.raises(RuntimeError, match="model crashed"):
        run(recorder, VIDEO_ID, "--library", str(library))

    assert list(library.iterdir()) == []


def test_missing_separation_extra_prints_the_install_hint(
    tmp_path: Path, recorder: Recorder, capsys
) -> None:
    recorder.find_spec = lambda name: None

    code = run(recorder, VIDEO_ID, "--library", str(tmp_path / "library"))

    assert code == 1
    assert "uv sync --package studio --extra separation" in capsys.readouterr().err
    assert recorder.calls == []
    assert not (tmp_path / "library").exists()


@pytest.mark.parametrize(
    ("routes", "files", "synced"),
    [
        ({"/api/get": (200, SYNCED)}, ["lyrics.lrc"], True),
        (
            {"/api/get": (200, {"plainLyrics": "Line one"}), "/api/search": (200, [])},
            ["lyrics.txt"],
            False,
        ),
        (
            {"/api/get": (200, {"instrumental": True}), "/api/search": (200, [])},
            [],
            None,
        ),
        ({"/api/get": (404, None), "/api/search": (200, [])}, [], None),
    ],
    ids=["synced", "plain", "instrumental", "none"],
)
def test_lyrics_cases_in_the_package(
    tmp_path: Path, recorder: Recorder, routes, files, synced
) -> None:
    recorder.lyrics_routes = routes

    assert run(recorder, VIDEO_ID, "--library", str(tmp_path)) == 0

    (package,) = tmp_path.iterdir()
    manifest = Manifest.load(package / "manifest.json")
    assert sorted(p.name for p in package.glob("lyrics.*")) == files
    if synced is None:
        assert manifest.lyrics is None
    else:
        assert manifest.lyrics is not None
        assert (manifest.lyrics.file, manifest.lyrics.synced) == (files[0], synced)


@pytest.mark.parametrize(
    "answer",
    [urllib.error.URLError("no route to host"), TimeoutError("timed out"), (500, None)],
    ids=["network", "timeout", "http-500"],
)
def test_lyrics_failure_gives_a_package_without_lyrics(
    tmp_path: Path, recorder: Recorder, capsys, answer
) -> None:
    recorder.lyrics_routes = {"/api/get": answer}

    assert run(recorder, VIDEO_ID, "--library", str(tmp_path)) == 0

    (package,) = tmp_path.iterdir()
    assert Manifest.load(package / "manifest.json").lyrics is None
    assert "warning: LRCLIB lookup failed" in capsys.readouterr().err


def test_null_test_failure_does_not_fail_the_command(
    tmp_path: Path, recorder: Recorder, capsys, monkeypatch
) -> None:
    def broken(manifest_path, mix_path):
        raise ValueError("unreadable")

    monkeypatch.setattr(cli, "null_test", broken)

    assert run(recorder, VIDEO_ID, "--library", str(tmp_path)) == 0
    (package,) = tmp_path.iterdir()
    assert (package / "manifest.json").is_file()
    assert "warning: null test failed" in capsys.readouterr().err


def test_title_full_of_path_characters_stays_in_the_library(
    tmp_path: Path, recorder: Recorder, audio_file: Path
) -> None:
    library = tmp_path / "library"

    code = run(
        recorder,
        str(audio_file),
        "--title",
        "../../..\\..\\C:\\outside/",
        "--artist",
        "/etc/../..",
        "--library",
        str(library),
    )

    assert code == 0
    (package,) = library.iterdir()
    assert re.fullmatch(r"[a-z0-9-]+", package.name)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["input", "library"]
    manifest = Manifest.load(package / "manifest.json")
    assert manifest.title == "../../..\\..\\C:\\outside/"


def test_model_dir_reaches_the_runner(tmp_path: Path, recorder: Recorder) -> None:
    run(
        recorder,
        VIDEO_ID,
        "--library",
        str(tmp_path / "lib"),
        "--model-dir",
        str(tmp_path / "models"),
    )

    assert ("make_runner", tmp_path / "models") in recorder.calls


def test_model_dir_defaults_to_the_s2_cache_folder(
    tmp_path: Path, recorder: Recorder
) -> None:
    run(recorder, VIDEO_ID, "--library", str(tmp_path / "lib"))

    default = Path.home() / ".cache" / "audio-separator-models"
    assert ("make_runner", default) in recorder.calls


def test_help_lists_only_the_planned_options(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(["process", "--help"])

    options = set(re.findall(r"--[a-z-]+", capsys.readouterr().out))
    assert options == {"--help", "--title", "--artist", "--library", "--model-dir"}
