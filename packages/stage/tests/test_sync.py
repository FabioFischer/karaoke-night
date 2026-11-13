import logging
import shutil
import sys
from pathlib import Path

import httpx2
import pytest
from stage.client import StudioClient, StudioError
from stage.sync import Outcome, sync

FIRST = "first-song--first-artist--a1b2c3"
SECOND = "second-song--second-artist--d4e5f6"
LEAD = "stems/lead.flac"
REPROCESSED_AT = "2026-11-13T20:00:00Z"


def run(library: Path, studio, http: httpx2.Client | None = None):
    with studio.client(http=http) as client:
        return sync(library, client)


def package_files(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def assert_mirrors(library: Path, studio, *song_ids: str) -> None:
    """The Stage library holds exactly these packages, byte for byte as on the Studio."""
    assert sorted(path.name for path in library.iterdir()) == sorted(song_ids)
    for song_id in song_ids:
        assert package_files(library / song_id) == package_files(
            studio.library / song_id
        )


def test_first_sync_downloads_every_song(studio, library: Path) -> None:
    studio.add(FIRST, extra={"melody": {"notes": [60, 62]}})
    studio.add(SECOND, lyrics=False)

    summary = run(library, studio)

    assert summary.outcomes == [Outcome(FIRST, "added"), Outcome(SECOND, "added")]
    assert summary.unchanged == 0
    assert_mirrors(library, studio, FIRST, SECOND)
    expected = sum(
        len(data)
        for song in (FIRST, SECOND)
        for data in package_files(studio.library / song).values()
    )
    assert summary.bytes_downloaded == expected


def test_second_sync_does_nothing(studio, library: Path) -> None:
    studio.add(FIRST)
    studio.add(SECOND)
    run(library, studio)
    studio.requests.clear()

    summary = run(library, studio)

    assert summary.outcomes == []
    assert summary.unchanged == 2
    assert summary.bytes_downloaded == 0
    assert [request.url.path for request in studio.requests] == ["/songs"]
    assert_mirrors(library, studio, FIRST, SECOND)


class _CutStream(httpx2.SyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self):
        yield self.data
        raise httpx2.ReadError("connection lost")


def cutting(studio, path: str, keep: int) -> httpx2.Client:
    """A client whose connection drops after keep bytes of the file at path."""
    inner = studio.http()

    def handler(request: httpx2.Request) -> httpx2.Response:
        response = inner.send(request)
        if not request.url.path.endswith(path):
            return httpx2.Response(
                response.status_code, headers=response.headers, content=response.content
            )
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=_CutStream(response.content[:keep]),
        )

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_an_interrupted_download_resumes_with_a_range(studio, library: Path) -> None:
    studio.add(FIRST)

    first = run(library, studio, http=cutting(studio, LEAD, keep=5000))

    assert first.outcomes == [
        Outcome(
            FIRST,
            "failed",
            "stems/lead.flac: could not reach the Studio (connection lost)",
        )
    ]
    partial = library / f".{FIRST}.partial"
    assert (partial / LEAD).read_bytes() == studio.file(FIRST, LEAD).read_bytes()[:5000]
    studio.requests.clear()
    studio.responses.clear()

    second = run(library, studio)

    assert second.outcomes == [Outcome(FIRST, "added")]
    assert_mirrors(library, studio, FIRST)
    lead_url = f"/songs/{FIRST}/files/{LEAD}"
    assert (lead_url, "bytes=5000-") in studio.file_requests()
    # The manifest kept in the partial is reused, and the stem already whole is not fetched.
    paths = [request.url.path for request in studio.requests]
    assert f"/songs/{FIRST}/manifest" not in paths
    assert f"/songs/{FIRST}/files/stems/other.flac" not in paths
    whole = len(studio.file(FIRST, LEAD).read_bytes())
    lyrics = len(studio.file(FIRST, "lyrics.lrc").read_bytes())
    assert second.bytes_downloaded == whole - 5000 + lyrics


def test_a_200_instead_of_206_restarts_the_file(studio, library: Path) -> None:
    studio.add(FIRST)
    run(library, studio, http=cutting(studio, LEAD, keep=5000))
    inner = studio.http()

    def ignore_range(request: httpx2.Request) -> httpx2.Response:
        request.headers.pop("range", None)
        response = inner.send(request)
        return httpx2.Response(
            response.status_code, headers=response.headers, content=response.content
        )

    summary = run(
        library,
        studio,
        http=httpx2.Client(transport=httpx2.MockTransport(ignore_range)),
    )

    assert summary.outcomes == [Outcome(FIRST, "added")]
    assert_mirrors(library, studio, FIRST)


def test_a_stem_that_fails_its_hash_is_not_installed_and_comes_again(
    studio, library: Path
) -> None:
    studio.add(FIRST)
    studio.add(SECOND)
    good = studio.file(SECOND, LEAD).read_bytes()
    studio.file(SECOND, LEAD).write_bytes(b"damaged on the way" * 100)

    first = run(library, studio)

    assert first.outcomes == [
        Outcome(FIRST, "added"),
        Outcome(
            SECOND,
            "failed",
            "stems/lead.flac does not match the sha256 of the manifest; "
            "it is downloaded again on the next sync",
        ),
    ]
    assert sorted(path.name for path in library.iterdir()) == [
        f".{SECOND}.partial",
        FIRST,
    ]
    assert not (library / f".{SECOND}.partial" / LEAD).exists()
    studio.file(SECOND, LEAD).write_bytes(good)
    studio.requests.clear()

    second = run(library, studio)

    assert second.outcomes == [Outcome(SECOND, "added")]
    assert_mirrors(library, studio, FIRST, SECOND)
    assert (f"/songs/{SECOND}/files/{LEAD}", None) in studio.file_requests()


def test_lyrics_are_not_hash_checked(studio, library: Path) -> None:
    studio.add(FIRST)
    studio.file(FIRST, "lyrics.lrc").write_bytes(b"[00:02.00] Another line\n")

    summary = run(library, studio)

    assert summary.outcomes == [Outcome(FIRST, "added")]
    assert_mirrors(library, studio, FIRST)


@pytest.mark.parametrize(
    ("created_at", "pipeline_version"),
    [(REPROCESSED_AT, 1), ("2026-11-04T20:00:00Z", 2)],
    ids=["created_at", "pipeline_version"],
)
def test_a_reprocessed_song_is_downloaded_again(
    studio, library: Path, created_at: str, pipeline_version: int
) -> None:
    studio.add(FIRST)
    run(library, studio)
    studio.add(FIRST, created_at=created_at, pipeline_version=pipeline_version)

    summary = run(library, studio)

    assert summary.outcomes == [Outcome(FIRST, "updated")]
    assert_mirrors(library, studio, FIRST)


def test_an_update_replaces_the_old_song_only_once_complete(
    studio, library: Path
) -> None:
    studio.add(FIRST)
    run(library, studio)
    old = package_files(library / FIRST)
    studio.add(FIRST, created_at=REPROCESSED_AT)
    good = studio.file(FIRST, LEAD).read_bytes()
    studio.file(FIRST, LEAD).write_bytes(b"damaged on the way" * 100)

    failed = run(library, studio)

    assert [outcome.action for outcome in failed.outcomes] == ["failed"]
    assert package_files(library / FIRST) == old
    studio.file(FIRST, LEAD).write_bytes(good)

    updated = run(library, studio)

    assert updated.outcomes == [Outcome(FIRST, "updated")]
    assert_mirrors(library, studio, FIRST)


def test_a_partial_of_an_older_version_is_discarded(studio, library: Path) -> None:
    studio.add(FIRST)
    run(library, studio, http=cutting(studio, LEAD, keep=5000))
    studio.add(FIRST, created_at=REPROCESSED_AT)
    studio.requests.clear()

    summary = run(library, studio)

    assert summary.outcomes == [Outcome(FIRST, "added")]
    assert_mirrors(library, studio, FIRST)
    assert all(range_ is None for _, range_ in studio.file_requests())
    assert f"/songs/{FIRST}/manifest" in [r.url.path for r in studio.requests]


def test_a_song_removed_on_the_studio_is_removed_locally(studio, library: Path) -> None:
    studio.add(FIRST)
    studio.add(SECOND)
    run(library, studio)
    shutil.rmtree(studio.library / SECOND)

    summary = run(library, studio)

    assert summary.outcomes == [Outcome(SECOND, "removed")]
    assert summary.unchanged == 1
    assert_mirrors(library, studio, FIRST)


def test_an_empty_listing_removes_everything(studio, library: Path) -> None:
    studio.add(FIRST)
    run(library, studio)
    shutil.rmtree(studio.library / FIRST)

    summary = run(library, studio)

    assert summary.outcomes == [Outcome(FIRST, "removed")]
    assert list(library.iterdir()) == []


BUSY_REMOVE = "a file of the song is open; it stays until a later sync"
BUSY_UPDATE = (
    "a file of the song is open; the old version stays and the new one "
    "is installed on a later sync"
)


def test_a_busy_song_is_kept_on_remove(studio, library: Path, refuse_rename) -> None:
    studio.add(FIRST)
    run(library, studio)
    kept = package_files(library / FIRST)
    shutil.rmtree(studio.library / FIRST)
    refuse_rename(FIRST)

    summary = run(library, studio)

    assert summary.outcomes == [Outcome(FIRST, "busy", BUSY_REMOVE)]
    assert [path.name for path in library.iterdir()] == [FIRST]
    assert package_files(library / FIRST) == kept


def test_a_busy_song_keeps_its_old_version_on_update(
    studio, library: Path, refuse_rename
) -> None:
    studio.add(FIRST)
    run(library, studio)
    old = package_files(library / FIRST)
    studio.add(FIRST, created_at=REPROCESSED_AT)
    refuse_rename(FIRST)

    busy = run(library, studio)

    assert busy.outcomes == [Outcome(FIRST, "busy", BUSY_UPDATE)]
    assert package_files(library / FIRST) == old
    partial = library / f".{FIRST}.partial"
    assert package_files(partial) == package_files(studio.library / FIRST)
    refuse_rename()
    studio.requests.clear()

    updated = run(library, studio)

    assert updated.outcomes == [Outcome(FIRST, "updated")]
    assert_mirrors(library, studio, FIRST)
    stem_paths = [path for path, _ in studio.file_requests() if "/stems/" in path]
    assert stem_paths == []


def test_an_update_whose_new_version_cannot_move_in_keeps_the_old_one(
    studio, library: Path, refuse_rename
) -> None:
    studio.add(FIRST)
    run(library, studio)
    old = package_files(library / FIRST)
    studio.add(FIRST, created_at=REPROCESSED_AT)
    partial = library / f".{FIRST}.partial"
    refuse_rename(partial.name)

    failed = run(library, studio)

    assert failed.outcomes == [
        Outcome(
            FIRST,
            "failed",
            f"[Errno 5] Access is denied: {str(partial)!r}; the old version stays "
            "and the new one is installed on a later sync",
        )
    ]
    assert sorted(path.name for path in library.iterdir()) == [partial.name, FIRST]
    assert package_files(library / FIRST) == old
    assert package_files(partial) == package_files(studio.library / FIRST)
    refuse_rename()
    studio.requests.clear()

    updated = run(library, studio)

    assert updated.outcomes == [Outcome(FIRST, "updated")]
    assert_mirrors(library, studio, FIRST)
    stem_paths = [path for path, _ in studio.file_requests() if "/stems/" in path]
    assert stem_paths == []


def test_an_update_that_cannot_put_the_old_version_back_installs_on_a_later_sync(
    studio, library: Path, monkeypatch
) -> None:
    studio.add(FIRST)
    run(library, studio)
    studio.add(FIRST, created_at=REPROCESSED_AT)
    rename = Path.rename

    def refuse_partial_and_trash(self, target):
        if self.name.startswith(f".{FIRST}."):
            raise PermissionError(5, "Access is denied", self.name)
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", refuse_partial_and_trash)

    failed = run(library, studio)

    [outcome] = failed.outcomes
    assert (outcome.song_id, outcome.action) == (FIRST, "failed")
    assert outcome.reason.startswith(
        f"[Errno 5] Access is denied: '.{FIRST}.partial'; the old version could "
        f"not be put back ([Errno 5] Access is denied: '.{FIRST}."
    )
    assert outcome.reason.endswith(
        ".deleting'), so the song is missing until a later sync installs the new one"
    )
    names = sorted(path.name for path in library.iterdir())
    assert names[1] == f".{FIRST}.partial"
    assert names[0].endswith(".deleting")
    assert len(names) == 2
    monkeypatch.undo()

    added = run(library, studio)

    assert added.outcomes == [Outcome(FIRST, "added")]
    assert_mirrors(library, studio, FIRST)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks open files")
def test_a_song_with_a_really_open_file_is_busy(studio, library: Path) -> None:
    studio.add(FIRST)
    studio.add(SECOND)
    run(library, studio)
    shutil.rmtree(studio.library / FIRST)
    studio.add(SECOND, created_at=REPROCESSED_AT)

    with (
        (library / FIRST / LEAD).open("rb"),
        (library / SECOND / LEAD).open("rb"),
    ):
        summary = run(library, studio)

    assert summary.outcomes == [
        Outcome(FIRST, "busy", BUSY_REMOVE),
        Outcome(SECOND, "busy", BUSY_UPDATE),
    ]
    assert (library / FIRST / "manifest.json").is_file()
    assert (library / SECOND / "manifest.json").is_file()


def refusing() -> httpx2.Client:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    return httpx2.Client(transport=httpx2.MockTransport(refuse))


def test_an_unreachable_studio_changes_nothing(studio, library: Path) -> None:
    studio.add(FIRST)
    run(library, studio)
    (library / f".{SECOND}.partial").mkdir()
    before = sorted(str(path.relative_to(library)) for path in library.rglob("*"))

    with pytest.raises(StudioError):
        run(library, studio, http=refusing())
    client = StudioClient("http://testserver", "wrong-token", http=studio.http())
    with client, pytest.raises(StudioError):
        sync(library, client)

    after = sorted(str(path.relative_to(library)) for path in library.rglob("*"))
    assert after == before


def test_an_unreachable_studio_on_the_first_sync_creates_no_library(
    studio, library: Path
) -> None:
    with pytest.raises(StudioError):
        run(library, studio, http=refusing())

    assert not library.exists()


@pytest.mark.parametrize(
    "bad_id",
    ["..", "a/b", "C:x", ".hidden", "Upper-Case--Artist--a1b2c3", "a\\b"],
)
def test_a_song_with_a_bad_id_is_skipped_with_nothing_written(
    studio, library: Path, bad_id: str, caplog
) -> None:
    studio.add(FIRST)
    studio.add(bad_id, folder="bad-package")

    with caplog.at_level(logging.WARNING, logger="stage"):
        summary = run(library, studio)

    assert summary.outcomes == [Outcome(FIRST, "added")]
    assert summary.skipped == 1
    assert sorted(path.name for path in library.iterdir()) == [FIRST]
    assert not (library.parent / "b").exists()
    assert caplog.messages == [
        f"skipped a song the Studio lists under an id the Stage does not accept: {bad_id!r}"
    ]
    assert all(bad_id not in request.url.path for request in studio.requests[1:])


def test_a_local_folder_named_as_a_skipped_id_is_not_removed(
    studio, library: Path
) -> None:
    bad_id = "Upper-Case--Artist--a1b2c3"
    studio.add(bad_id)
    run(library, studio)
    shutil.copytree(studio.library / bad_id, library / bad_id)

    summary = run(library, studio)

    assert summary.outcomes == []
    assert summary.skipped == 1
    assert [path.name for path in library.iterdir()] == [bad_id]


def test_a_failure_on_one_song_does_not_stop_the_others(studio, library: Path) -> None:
    studio.add(FIRST)
    studio.add(SECOND)
    studio.file(FIRST, LEAD).unlink()

    summary = run(library, studio)

    assert summary.outcomes == [
        Outcome(
            FIRST, "failed", "stems/lead.flac: the Studio answered 404: unknown file"
        ),
        Outcome(SECOND, "added"),
    ]
    assert (library / SECOND / "manifest.json").is_file()


def test_partials_of_songs_no_longer_listed_and_old_trash_are_removed(
    studio, library: Path
) -> None:
    studio.add(FIRST)
    library.mkdir()
    gone = library / f".{SECOND}.partial"
    (gone / "stems").mkdir(parents=True)
    (gone / LEAD).write_bytes(b"part of a song")
    trash = library / f".{SECOND}.0a1b2c3d.deleting"
    (trash / "stems").mkdir(parents=True)
    kept = [
        ".settings",
        ".partial",
        f".{SECOND}.partial.old",
        f".{SECOND}.ABCDEF12.deleting",
        f".{SECOND}.0a1b2c3.deleting",
        ".Third-Song--Third-Artist--a1b2c3.partial",
        "notes",
    ]
    for name in kept:
        (library / name).mkdir()

    summary = run(library, studio)

    assert summary.outcomes == [Outcome(FIRST, "added")]
    assert sorted(path.name for path in library.iterdir()) == sorted([FIRST, *kept])


def test_a_package_stored_under_another_folder_name_on_the_studio(
    studio, library: Path
) -> None:
    studio.add(FIRST, folder="stored-elsewhere")

    summary = run(library, studio)

    assert summary.outcomes == [Outcome(FIRST, "added")]
    assert package_files(library / FIRST) == package_files(
        studio.library / "stored-elsewhere"
    )
