from pathlib import Path

import httpx2
import pytest
from stage.client import StudioClient, StudioUnreachable
from stage.sync import Outcome, Progress, fetch

FIRST = "first-song--first-artist--a1b2c3"
SECOND = "second-song--second-artist--d4e5f6"
REPROCESSED_AT = "2026-11-13T20:00:00Z"


def package_files(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def run(library: Path, studio, song_id: str, on_progress=None):
    with studio.client() as client:
        if on_progress is None:
            return fetch(library, client, song_id)
        return fetch(library, client, song_id, on_progress)


def test_fetch_installs_only_that_song(studio, library: Path) -> None:
    studio.add(FIRST)
    studio.add(SECOND)

    outcome = run(library, studio, FIRST)

    assert outcome == Outcome(FIRST, "added")
    assert [path.name for path in library.iterdir()] == [FIRST]
    assert package_files(library / FIRST) == package_files(studio.library / FIRST)


def test_fetch_reports_files_and_bytes_as_they_arrive(studio, library: Path) -> None:
    studio.add(FIRST)
    seen: list[Progress] = []

    run(library, studio, FIRST, seen.append)

    files = package_files(studio.library / FIRST)
    file_bytes = sum(
        len(data) for name, data in files.items() if name != "manifest.json"
    )
    # Two stems and the lyrics.
    assert seen[0] == Progress(files_done=0, files_total=3, bytes_done=0)
    assert seen[-1] == Progress(files_done=3, files_total=3, bytes_done=file_bytes)
    assert [progress.files_done for progress in seen] == sorted(
        progress.files_done for progress in seen
    )
    assert [progress.bytes_done for progress in seen] == sorted(
        progress.bytes_done for progress in seen
    )
    assert len(seen) > 4


def test_fetch_counts_a_stem_already_whole_in_the_partial(
    studio, library: Path
) -> None:
    studio.add(FIRST, lyrics=False)
    partial = library / f".{FIRST}.partial"
    (partial / "stems").mkdir(parents=True)
    (partial / "manifest.json").write_bytes(
        (studio.library / FIRST / "manifest.json").read_bytes()
    )
    lead = (studio.library / FIRST / "stems" / "lead.flac").read_bytes()
    (partial / "stems" / "lead.flac").write_bytes(lead)
    seen: list[Progress] = []

    outcome = run(library, studio, FIRST, seen.append)

    other = (studio.library / FIRST / "stems" / "other.flac").read_bytes()
    assert outcome == Outcome(FIRST, "added")
    assert seen[-1] == Progress(files_done=2, files_total=2, bytes_done=len(other))


def test_fetch_of_a_song_already_in_the_library_does_nothing(
    studio, library: Path
) -> None:
    studio.add(FIRST)
    run(library, studio, FIRST)
    studio.requests.clear()

    assert run(library, studio, FIRST) is None
    assert [request.url.path for request in studio.requests] == ["/songs"]


def test_fetch_of_a_reprocessed_song_updates_it(studio, library: Path) -> None:
    studio.add(FIRST)
    run(library, studio, FIRST)
    studio.add(FIRST, created_at=REPROCESSED_AT)

    outcome = run(library, studio, FIRST)

    assert outcome == Outcome(FIRST, "updated")
    assert package_files(library / FIRST) == package_files(studio.library / FIRST)


def test_fetch_of_a_song_the_studio_does_not_list_fails(studio, library: Path) -> None:
    studio.add(FIRST)

    outcome = run(library, studio, SECOND)

    assert outcome == Outcome(SECOND, "failed", "the Studio does not list the song")
    assert not library.exists() or not any(library.iterdir())


def test_fetch_with_a_stem_that_does_not_match_fails(studio, library: Path) -> None:
    studio.add(FIRST)
    studio.file(FIRST, "stems/lead.flac").write_bytes(b"not the stem")

    outcome = run(library, studio, FIRST)

    assert outcome.action == "failed"
    assert "stems/lead.flac does not match the sha256" in outcome.reason
    assert not (library / FIRST).exists()


def test_fetch_on_an_unreachable_studio_raises(library: Path) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    http = httpx2.Client(transport=httpx2.MockTransport(refuse))
    client = StudioClient("http://studio.invalid:8790", "token", http=http)
    with client, pytest.raises(StudioUnreachable):
        fetch(library, client, FIRST)


def test_fetch_cut_by_the_network_raises_and_keeps_the_partial(
    studio, library: Path
) -> None:
    studio.add(FIRST)
    inner = studio.http()

    def lose_the_lead(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/stems/lead.flac"):
            raise httpx2.ReadError("connection lost", request=request)
        response = inner.send(request)
        return httpx2.Response(response.status_code, content=response.content)

    http = httpx2.Client(transport=httpx2.MockTransport(lose_the_lead))
    client = StudioClient("http://testserver", "test-token", http=http)
    with client, pytest.raises(StudioUnreachable) as caught:
        fetch(library, client, FIRST)

    assert str(caught.value) == (
        "stems/lead.flac: could not reach the Studio (connection lost)"
    )
    partial = library / f".{FIRST}.partial"
    assert (partial / "stems" / "other.flac").is_file()
    assert not (library / FIRST).exists()


def test_fetch_refuses_an_id_the_stage_does_not_accept(studio, library: Path) -> None:
    bad_id = "Upper-Case--Artist--a1b2c3"
    studio.add(bad_id)

    outcome = run(library, studio, bad_id)

    assert outcome == Outcome(bad_id, "failed", "the Studio does not list the song")
    assert [request.url.path for request in studio.requests] == ["/songs"]
    assert not library.exists()
