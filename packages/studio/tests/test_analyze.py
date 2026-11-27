"""`studio analyze`: packages of an older pipeline version get the melody analysis."""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from core.manifest import Manifest, YouTubeMusicSource
from studio import cli
from studio import library as lib
from studio.audio import SAMPLE_RATE
from studio.lyrics import FoundLyrics
from studio.package import sha256_file, song_id, write_package
from studio.separation import MODELS, STEMS

OLD = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)
SECONDS = 0.5
BUSY = "a file of the song is open; it stays as it is"


def make_package(
    library: Path, title: str, video_id: str, pipeline_version: int = 1
) -> Path:
    """A package as an older Studio wrote it: real stems, lyrics with an offset."""
    folder = library / song_id(title, "Artist Name", video_id)
    folder.mkdir(parents=True)
    n = int(SECONDS * SAMPLE_RATE)
    stems = {
        name: np.full((n, 2), 0.01 * (index + 1), dtype=np.float32)
        for index, name in enumerate(STEMS)
    }
    manifest = write_package(
        folder,
        stems,
        title=title,
        artist="Artist Name",
        source=YouTubeMusicSource(type="youtube_music", video_id=video_id),
        gain_db=-1.5,
        lyrics=FoundLyrics(
            file="lyrics.lrc", text="[00:01.00] Line one\n", synced=True
        ),
        models=MODELS,
        melody={"notes": [], "keys": []},
    )
    update = {
        "created_at": OLD,
        "lyrics": manifest.lyrics.model_copy(update={"offset_ms": 250}),
    }
    if pipeline_version == 1:
        (folder / "melody.json").unlink()
        update |= {"pipeline_version": 1, "melody": None}
    manifest.model_copy(update=update).write(folder / "manifest.json")
    return folder


def snapshot(folder: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def library(tmp_path: Path) -> Path:
    path = tmp_path / "library"
    path.mkdir()
    return path


@pytest.fixture
def tools(fake_tools) -> cli.Tools:
    return fake_tools.tools()


def run(tools: cli.Tools, library: Path, *args: str) -> int:
    return cli.main(["analyze", *args, "--library", str(library)], tools=tools)


def refuse_rename(monkeypatch, refused) -> list[str]:
    """Path.rename of the folders refused(name) picks fails like Windows does
    for a folder with an open file. Returns the names it was asked to move."""
    rename = Path.rename
    asked: list[str] = []

    def guarded(self, target):
        asked.append(self.name)
        if refused(self.name):
            raise PermissionError(5, "Access is denied", self.name)
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", guarded)
    return asked


def test_every_package_of_an_older_version_is_analyzed(
    library: Path, fake_tools, tools, capsys, melody_of
) -> None:
    first = make_package(library, "Song Title", "XXXXXXXXXXX")
    second = make_package(library, "Other Title", "YYYYYYYYYYY")
    current = make_package(library, "Third Title", "ZZZZZZZZZZZ", pipeline_version=2)
    before = {folder.name: snapshot(folder) for folder in (first, second, current)}
    old = Manifest.load(first / "manifest.json")

    code = run(tools, library, "--model-dir", "models")

    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == [f"analyzed {second.name}", f"analyzed {first.name}"]
    assert re.fullmatch(r"2 analyzed, 0 busy, 0 failed in \d+\.\d s", lines[2])
    assert len(lines) == 3
    assert sorted(p.name for p in library.iterdir()) == sorted(before)
    assert snapshot(current) == before[current.name]
    for folder in (first, second):
        after = snapshot(folder)
        for path, data in before[folder.name].items():
            if path != "manifest.json":
                assert after[path] == data, path
        manifest = Manifest.load(folder / "manifest.json")
        assert manifest.pipeline_version == 2
        assert manifest.created_at > OLD
        assert manifest.melody is not None
        assert manifest.melody.file == "melody.json"
        assert manifest.melody.sha256 == sha256_file(folder / "melody.json")
        assert (folder / "melody.json").read_bytes().decode("utf-8").count("\r") == 0
        assert json.loads((folder / "melody.json").read_bytes()) == melody_of(SECONDS)
    manifest = Manifest.load(first / "manifest.json")
    unchanged = {"pipeline_version", "melody", "created_at"}
    assert manifest.model_dump(exclude=unchanged) == old.model_dump(exclude=unchanged)
    assert manifest.lyrics.offset_ms == 250
    tracked = [c for c in fake_tools.analysis.calls if c[0] == "track_pitch"]
    assert [call[3] for call in tracked] == [Path("models"), Path("models")]


def test_ids_given_are_analyzed_whatever_their_version(
    library: Path, tools, capsys, melody_of
) -> None:
    current = make_package(library, "Song Title", "XXXXXXXXXXX", pipeline_version=2)
    older = make_package(library, "Other Title", "YYYYYYYYYYY")
    untouched = snapshot(older)

    code = run(tools, library, current.name)

    assert code == 0
    assert capsys.readouterr().out.splitlines()[0] == f"analyzed {current.name}"
    assert json.loads((current / "melody.json").read_bytes()) == melody_of(SECONDS)
    assert Manifest.load(current / "manifest.json").created_at > OLD
    assert snapshot(older) == untouched


def test_an_unknown_id_fails_and_the_others_go_on(library: Path, tools, capsys) -> None:
    older = make_package(library, "Song Title", "XXXXXXXXXXX")

    code = run(tools, library, "missing--song--000000", older.name)

    assert code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == [
        "failed   missing--song--000000: not in the library",
        f"analyzed {older.name}",
    ]
    assert lines[2].startswith("1 analyzed, 0 busy, 1 failed in ")


def test_nothing_to_analyze_prints_only_the_summary(
    library: Path, tools, capsys
) -> None:
    make_package(library, "Song Title", "XXXXXXXXXXX", pipeline_version=2)

    code = run(tools, library)

    assert code == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert re.fullmatch(r"0 analyzed, 0 busy, 0 failed in \d+\.\d s", line)


def test_a_package_that_cannot_be_moved_is_busy_and_stays_whole(
    library: Path, tools, capsys, monkeypatch
) -> None:
    older = make_package(library, "Song Title", "XXXXXXXXXXX")
    before = snapshot(older)
    refuse_rename(monkeypatch, lambda name: name == older.name)

    code = run(tools, library)

    assert code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"busy     {older.name}: {BUSY}"
    assert lines[1].startswith("0 analyzed, 1 busy, 0 failed in ")
    assert [p.name for p in library.iterdir()] == [older.name]
    assert snapshot(older) == before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks open files")
def test_a_package_with_a_really_open_file_is_busy(
    library: Path, tools, capsys
) -> None:
    older = make_package(library, "Song Title", "XXXXXXXXXXX")
    before = snapshot(older)

    with (older / "stems" / "lead.flac").open("rb"):
        code = run(tools, library)

    assert code == 1
    assert capsys.readouterr().out.splitlines()[0] == f"busy     {older.name}: {BUSY}"
    assert [p.name for p in library.iterdir()] == [older.name]
    assert snapshot(older) == before


def test_a_failed_analysis_leaves_the_package_whole(
    library: Path, fake_tools, tools, capsys
) -> None:
    older = make_package(library, "Song Title", "XXXXXXXXXXX")
    before = snapshot(older)
    fake_tools.analysis.fail = RuntimeError("tracker crashed")

    code = run(tools, library)

    assert code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"failed   {older.name}: tracker crashed"
    assert lines[1].startswith("0 analyzed, 0 busy, 1 failed in ")
    assert [p.name for p in library.iterdir()] == [older.name]
    assert snapshot(older) == before


def test_a_failure_on_one_package_does_not_stop_the_next(
    library: Path, fake_tools, tools, capsys
) -> None:
    first = make_package(library, "Song Title", "XXXXXXXXXXX")
    second = make_package(library, "Other Title", "YYYYYYYYYYY")
    (second / "stems" / "drums.flac").unlink()

    code = run(tools, library)

    assert code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"failed   {second.name}: ")
    assert lines[1] == f"analyzed {first.name}"
    assert sorted(p.name for p in library.iterdir()) == sorted(
        [first.name, second.name]
    )
    assert Manifest.load(second / "manifest.json").pipeline_version == 1


def test_a_new_version_that_cannot_go_in_puts_the_old_one_back(
    library: Path, tools, capsys, monkeypatch
) -> None:
    older = make_package(library, "Song Title", "XXXXXXXXXXX")
    before = snapshot(older)
    asked = refuse_rename(monkeypatch, lambda name: name.endswith(".tmp"))

    code = run(tools, library)

    assert code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"failed   {older.name}: [Errno 5] Access is denied: '.")
    assert lines[0].endswith(".tmp'; the old version stays")
    assert [p.name for p in library.iterdir()] == [older.name]
    assert snapshot(older) == before
    moved, build, trash = asked
    assert moved == older.name
    assert build.endswith(".tmp")
    assert trash.endswith(".deleting")
    # Both have the shapes `studio serve` removes as leftovers.
    monkeypatch.undo()
    elsewhere = library.parent / "elsewhere"
    for name in (build, trash):
        (elsewhere / name).mkdir(parents=True)
    lib.remove_leftovers(elsewhere)
    assert list(elsewhere.iterdir()) == []


def test_an_old_version_that_cannot_be_put_back_stays_in_its_trash_folder(
    library: Path, tools, capsys, monkeypatch
) -> None:
    older = make_package(library, "Song Title", "XXXXXXXXXXX")
    before = snapshot(older)
    refuse_rename(monkeypatch, lambda name: name.startswith(f".{older.name}."))

    code = run(tools, library)

    assert code == 1
    line = capsys.readouterr().out.splitlines()[0]
    (trash,) = [p for p in library.iterdir()]
    assert trash.name.endswith(".deleting")
    assert snapshot(trash) == before
    assert line.startswith(f"failed   {older.name}: [Errno 5] Access is denied: '.")
    assert line.endswith(
        f"the old version could not be put back ([Errno 5] Access is denied: "
        f"'{trash.name}') and is kept in {trash.name}, which the next studio "
        "serve removes"
    )


def test_analyze_refuses_without_the_separation_extra(
    library: Path, fake_tools, capsys
) -> None:
    older = make_package(library, "Song Title", "XXXXXXXXXXX")
    before = snapshot(older)
    fake_tools.find_spec = lambda name: None

    code = run(fake_tools.tools(), library)

    assert code == 1
    err = capsys.readouterr().err
    assert "studio analyze needs the separation extra" in err
    assert "uv sync --package studio --extra separation" in err
    assert snapshot(older) == before
    assert fake_tools.analysis.calls == []


def test_help_lists_only_the_planned_options(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(["analyze", "--help"])

    out = capsys.readouterr().out
    assert set(re.findall(r"--[a-z-]+", out)) == {"--help", "--library", "--model-dir"}
    assert "SONG_ID" in out
