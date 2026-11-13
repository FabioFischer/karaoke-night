import logging
import os
from pathlib import Path

import pytest
from studio import library as lib
from studio.package import build_atomically, song_id

SONG = song_id("Song Title", "Artist Name", "XXXXXXXXXXX")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    path = tmp_path / "library"
    path.mkdir()
    return path


def build_leftover(library: Path) -> str:
    """A folder named as build_atomically names its build, left on disk."""
    names = []

    def build(out: Path) -> None:
        names.append(out.name)
        raise RuntimeError("stopped mid build")

    with pytest.raises(RuntimeError):
        build_atomically(library, SONG, build)
    (library / names[0]).mkdir()
    return names[0]


def delete_leftover(library: Path, monkeypatch) -> str:
    """A folder named as delete names its trash, left on disk."""
    folder = library / SONG
    folder.mkdir()
    with monkeypatch.context() as patch:
        patch.setattr("studio.library.shutil.rmtree", lambda *a, **k: None)
        lib.delete(lib.Package(folder, b"", None))
    (trash,) = [path.name for path in library.iterdir()]
    return trash


def test_leftovers_of_a_build_and_a_deletion_are_removed(
    library: Path, monkeypatch
) -> None:
    trash = delete_leftover(library, monkeypatch)
    build = build_leftover(library)
    (library / build / "stems").mkdir()
    (library / build / "stems" / "lead.flac").write_bytes(b"partial stem")

    lib.remove_leftovers(library)

    assert trash.endswith(".deleting")
    assert build.endswith(".tmp")
    assert list(library.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    [
        ".vscode.backup.tmp",
        ".config.old.tmp",
        ".git.rebase.deleting",
        f".{SONG}.abcd1234.tmp.bak",
        f".{SONG}.abcd1234.deleting.bak",
        f".{SONG}.abcd123.tmp",
        f".{SONG}.abcd12345.tmp",
        f".{SONG}.ABCD1234.tmp",
        f".{SONG}.abcd-123.tmp",
    ],
    ids=[
        "tool-backup",
        "config",
        "git",
        "tmp-suffixed",
        "deleting-suffixed",
        "random-short",
        "random-long",
        "random-uppercase",
        "random-hyphen",
    ],
)
def test_folders_neither_generator_names_are_kept(library: Path, name: str) -> None:
    (library / name).mkdir()

    lib.remove_leftovers(library)

    assert [path.name for path in library.iterdir()] == [name]


def test_each_removed_leftover_is_logged(library: Path, monkeypatch, caplog) -> None:
    names = sorted([delete_leftover(library, monkeypatch), build_leftover(library)])
    caplog.set_level(logging.INFO, logger="studio")

    lib.remove_leftovers(library)

    records = sorted(
        (r for r in caplog.records if r.name == "studio.library"),
        key=lambda r: r.getMessage(),
    )
    assert [(r.levelname, r.getMessage()) for r in records] == [
        ("INFO", f"removed the leftover folder {name}") for name in names
    ]


def test_a_leftover_that_cannot_be_fully_removed_is_logged(
    library: Path, monkeypatch, caplog
) -> None:
    name = build_leftover(library)
    (library / name / "locked.bin").write_bytes(b"held by another process")
    (library / name / "free.bin").write_bytes(b"removable")
    unlink = os.unlink

    def unlink_all_but_locked(path, *, dir_fd=None):
        if os.path.basename(path) == "locked.bin":
            raise PermissionError(13, "in use", str(path))
        unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink_all_but_locked)

    lib.remove_leftovers(library)

    assert (library / name / "locked.bin").is_file()
    assert not (library / name / "free.bin").exists()
    (record,) = [r for r in caplog.records if r.name == "studio.library"]
    assert record.levelname == "WARNING"
    message = record.getMessage()
    assert message.startswith(f"could not fully remove the leftover folder {name} (")
    assert "locked.bin" in message
