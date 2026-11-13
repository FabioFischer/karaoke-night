import io
import re
import shutil
import sys
from pathlib import Path

import httpx2
import pytest
from stage import cli

FIRST = "first-song--first-artist--a1b2c3"
SECOND = "second-song--second-artist--d4e5f6"
TOKEN = "test-token"


@pytest.fixture
def token(monkeypatch) -> str:
    monkeypatch.setenv("STUDIO_TOKEN", TOKEN)
    return TOKEN


def no_network() -> httpx2.Client:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"no request expected, got {request.url}")

    return httpx2.Client(transport=httpx2.MockTransport(fail))


def stage_sync(studio, library: Path, http=None) -> int:
    return cli.main(
        ["sync", "--studio", "http://testserver", "--library", str(library)],
        http=http or studio.http(),
    )


SUMMARY = re.compile(
    r"^(\d+) added, (\d+) updated, (\d+) removed, (\d+) busy, (\d+) failed, "
    r"(\d+) unchanged, (\d+) skipped; ([\d.]+) MB downloaded in ([\d.]+) s$"
)


def test_sync_prints_a_line_per_song_and_a_summary(
    studio, library: Path, token, capsys
) -> None:
    studio.add(FIRST)
    studio.add(SECOND)
    assert stage_sync(studio, library) == 0
    capsys.readouterr()
    shutil.rmtree(studio.library / SECOND)
    studio.add(FIRST, created_at="2026-11-13T20:00:00Z")
    studio.add("..", folder="bad-package")

    assert stage_sync(studio, library) == 0

    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[:2] == [f"removed {SECOND}", f"updated {FIRST}"]
    counts = SUMMARY.match(lines[2])
    assert counts is not None, lines[2]
    assert counts.groups()[:7] == ("0", "1", "1", "0", "0", "0", "1")
    assert float(counts[8]) > 0
    assert len(lines) == 3
    assert err == (
        "warning: skipped a song the Studio lists under an id the Stage does not "
        "accept: '..'\n"
    )


def test_sync_reports_busy_and_failed_songs_and_exits_1_on_a_failure(
    studio, library: Path, token, capsys, refuse_rename
) -> None:
    studio.add(FIRST)
    studio.add(SECOND)
    stage_sync(studio, library)
    capsys.readouterr()
    shutil.rmtree(studio.library / FIRST)
    studio.add(SECOND, created_at="2026-11-13T20:00:00Z")
    studio.file(SECOND, "stems/lead.flac").write_bytes(b"damaged on the way")
    refuse_rename(FIRST)

    assert stage_sync(studio, library) == 1

    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == [
        f"busy    {FIRST}: a file of the song is open; it stays until a later sync",
        (
            f"failed  {SECOND}: stems/lead.flac does not match the sha256 of the "
            "manifest; it is downloaded again on the next sync"
        ),
    ]
    assert SUMMARY.match(lines[2]).groups()[:7] == ("0", "0", "0", "1", "1", "0", "0")


def test_sync_with_only_busy_songs_exits_0(
    studio, library: Path, token, capsys, refuse_rename
) -> None:
    studio.add(FIRST)
    stage_sync(studio, library)
    capsys.readouterr()
    shutil.rmtree(studio.library / FIRST)
    refuse_rename(FIRST)

    assert stage_sync(studio, library) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        f"busy    {FIRST}: a file of the song is open; it stays until a later sync"
    )
    assert SUMMARY.match(lines[1]).groups()[:7] == ("0", "0", "0", "1", "0", "0", "0")


def test_sync_lines_are_aligned(studio, library: Path, token, capsys) -> None:
    studio.add(FIRST)

    stage_sync(studio, library)

    assert capsys.readouterr().out.splitlines()[0] == f"added   {FIRST}"


def test_sync_refuses_to_run_without_the_token(
    studio, library: Path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("STUDIO_TOKEN", raising=False)

    assert stage_sync(studio, library, http=no_network()) == 1

    assert capsys.readouterr().err == (
        "stage sync needs the STUDIO_TOKEN environment variable: the token of the "
        "Studio API, sent as 'Authorization: Bearer <token>'\n"
    )
    assert not library.exists()


def test_sync_with_an_empty_token_refuses_too(
    studio, library: Path, monkeypatch
) -> None:
    monkeypatch.setenv("STUDIO_TOKEN", "")

    assert stage_sync(studio, library, http=no_network()) == 1


def test_sync_against_an_unreachable_studio_exits_1_and_changes_nothing(
    studio, library: Path, token, capsys
) -> None:
    studio.add(FIRST)
    stage_sync(studio, library)
    capsys.readouterr()

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    http = httpx2.Client(transport=httpx2.MockTransport(refuse))

    assert stage_sync(studio, library, http=http) == 1

    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        "stage sync could not get the song list from http://testserver: could not "
        "reach the Studio (connection refused); nothing in the library changed\n"
    )
    assert [path.name for path in library.iterdir()] == [FIRST]


def test_sync_with_a_wrong_token_exits_1(
    studio, library: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("STUDIO_TOKEN", "wrong-token")

    assert stage_sync(studio, library) == 1

    assert "the Studio answered 401: missing or wrong token" in capsys.readouterr().err


def test_list_works_without_the_network_or_the_token(
    studio, library: Path, token, monkeypatch, capsys
) -> None:
    studio.add(FIRST)
    studio.add(SECOND)
    stage_sync(studio, library)
    capsys.readouterr()
    monkeypatch.delenv("STUDIO_TOKEN")

    assert cli.main(["list", "--library", str(library)], http=no_network()) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"{FIRST}  Title of {FIRST}  Artist Name  4:05",
        f"{SECOND}  Title of {SECOND}  Artist Name  4:05",
    ]


NOT_IN_CP1252 = "歌の題名"


def cp1252(monkeypatch, name: str) -> io.TextIOWrapper:
    """Replace sys.<name> with a stream encoded like a redirected one on Windows."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, name, stream)
    return stream


def written(stream: io.TextIOWrapper) -> list[str]:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8").splitlines()


def test_list_prints_a_title_outside_cp1252_as_utf8(
    studio, library: Path, token, monkeypatch
) -> None:
    studio.add(FIRST, extra={"title": NOT_IN_CP1252})
    stage_sync(studio, library)
    stdout = cp1252(monkeypatch, "stdout")

    assert cli.main(["list", "--library", str(library)]) == 0

    assert written(stdout) == [f"{FIRST}  {NOT_IN_CP1252}  Artist Name  4:05"]


def test_sync_warnings_outside_cp1252_are_written_as_utf8(
    studio, library: Path, token, monkeypatch
) -> None:
    bad_id = f"{NOT_IN_CP1252}--artist--a1b2c3"
    studio.add(bad_id, folder="bad-package")
    stdout = cp1252(monkeypatch, "stdout")
    stderr = cp1252(monkeypatch, "stderr")

    assert stage_sync(studio, library) == 0

    assert written(stderr) == [
        (
            "warning: skipped a song the Studio lists under an id the Stage does "
            f"not accept: {bad_id!r}"
        )
    ]
    assert SUMMARY.match(written(stdout)[0]) is not None


def test_list_of_an_empty_library(tmp_path: Path, capsys) -> None:
    assert cli.main(["list", "--library", str(tmp_path / "stage")]) == 0

    assert capsys.readouterr().out == f"no songs in {tmp_path / 'stage'}\n"


def test_the_default_library_is_library_in_the_current_folder(
    studio, tmp_path: Path, token, monkeypatch, capsys
) -> None:
    studio.add(FIRST)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["sync", "--studio", "http://testserver"], http=studio.http()) == 0

    assert (tmp_path / "library" / FIRST / "manifest.json").is_file()
    capsys.readouterr()
    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out.startswith(f"{FIRST}  ")


def test_sync_needs_the_studio_url(capsys) -> None:
    with pytest.raises(SystemExit) as exit_:
        cli.main(["sync"])

    assert exit_.value.code == 2
    assert "--studio" in capsys.readouterr().err
