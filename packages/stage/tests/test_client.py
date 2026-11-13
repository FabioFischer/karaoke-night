from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from core.studio_api import SongSummary
from stage.client import StudioClient, StudioError

SONG = "song-title--artist-name--a1b2c3"
LEAD = "stems/lead.flac"


def test_songs_are_parsed_into_summaries(studio) -> None:
    studio.add(SONG, created_at="2026-11-04T20:00:00Z")

    with studio.client() as client:
        songs = client.songs()

    assert songs == [
        SongSummary(
            id=SONG,
            title=f"Title of {SONG}",
            artist="Artist Name",
            duration_s=245.3,
            pipeline_version=1,
            created_at=datetime(2026, 11, 4, 20, 0, tzinfo=UTC),
        )
    ]
    (request,) = studio.requests
    assert request.headers["authorization"] == "Bearer test-token"


def test_manifest_is_the_stored_bytes(studio) -> None:
    studio.add(SONG, extra={"melody": {"notes": []}})

    with studio.client() as client:
        raw = client.manifest(SONG)

    assert raw == studio.file(SONG, "manifest.json").read_bytes()


def test_download_writes_the_whole_file(studio, tmp_path: Path) -> None:
    studio.add(SONG)
    target = tmp_path / "lead.flac"

    with studio.client() as client:
        received = client.download(SONG, LEAD, target)

    whole = studio.file(SONG, LEAD).read_bytes()
    assert target.read_bytes() == whole
    assert received == len(whole)
    assert studio.file_requests() == [(f"/songs/{SONG}/files/{LEAD}", None)]


def test_download_of_a_partial_file_asks_for_the_rest(studio, tmp_path: Path) -> None:
    studio.add(SONG)
    whole = studio.file(SONG, LEAD).read_bytes()
    target = tmp_path / "lead.flac"
    target.write_bytes(whole[:1000])

    with studio.client() as client:
        received = client.download(SONG, LEAD, target)

    assert target.read_bytes() == whole
    assert received == len(whole) - 1000
    assert studio.file_requests() == [(f"/songs/{SONG}/files/{LEAD}", "bytes=1000-")]
    assert studio.responses[-1].status_code == 206


def ignoring_range(studio) -> httpx2.Client:
    """A client to a server that answers every Range request with the whole file."""
    inner = studio.http()

    def handler(request: httpx2.Request) -> httpx2.Response:
        request.headers.pop("range", None)
        response = inner.send(request)
        return httpx2.Response(
            response.status_code, headers=response.headers, content=response.content
        )

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_a_200_to_a_range_request_restarts_the_file(studio, tmp_path: Path) -> None:
    studio.add(SONG)
    whole = studio.file(SONG, LEAD).read_bytes()
    target = tmp_path / "lead.flac"
    target.write_bytes(b"x" * 1000)

    with studio.client(http=ignoring_range(studio)) as client:
        received = client.download(SONG, LEAD, target)

    assert target.read_bytes() == whole
    assert received == len(whole)
    assert studio.responses[-1].status_code == 200


def test_a_partial_file_as_long_as_the_whole_restarts(studio, tmp_path: Path) -> None:
    studio.add(SONG)
    whole = studio.file(SONG, LEAD).read_bytes()
    target = tmp_path / "lead.flac"
    target.write_bytes(b"x" * len(whole))

    with studio.client() as client:
        received = client.download(SONG, LEAD, target)

    assert target.read_bytes() == whole
    assert received == len(whole)
    assert [response.status_code for response in studio.responses] == [416, 200]
    assert studio.file_requests() == [
        (f"/songs/{SONG}/files/{LEAD}", f"bytes={len(whole)}-"),
        (f"/songs/{SONG}/files/{LEAD}", None),
    ]


class _CutStream(httpx2.SyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self):
        yield self.data
        raise httpx2.ReadError("connection lost")


def test_bytes_received_before_a_lost_connection_stay_on_disk(
    studio, tmp_path: Path
) -> None:
    studio.add(SONG)
    whole = studio.file(SONG, LEAD).read_bytes()
    inner = studio.http()

    def handler(request: httpx2.Request) -> httpx2.Response:
        response = inner.send(request)
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=_CutStream(response.content[:5000]),
        )

    target = tmp_path / "lead.flac"
    http = httpx2.Client(transport=httpx2.MockTransport(handler))

    with studio.client(http=http) as client, pytest.raises(StudioError) as caught:
        client.download(SONG, LEAD, target)

    assert target.read_bytes() == whole[:5000]
    assert "connection lost" in str(caught.value)


def test_an_error_answer_is_a_studio_error(studio, tmp_path: Path) -> None:
    studio.add(SONG)

    with studio.client() as client:
        with pytest.raises(StudioError) as missing:
            client.download(SONG, "stems/missing.flac", tmp_path / "missing.flac")
        with pytest.raises(StudioError) as unknown:
            client.manifest("unknown-song--artist-name--a1b2c3")

    assert str(missing.value) == "the Studio answered 404: unknown file"
    assert str(unknown.value) == "the Studio answered 404: unknown song"
    assert not (tmp_path / "missing.flac").exists()


def test_a_wrong_token_is_a_studio_error(studio) -> None:
    with (
        studio.client(token="wrong-token") as client,
        pytest.raises(StudioError) as caught,
    ):
        client.songs()

    assert str(caught.value) == "the Studio answered 401: missing or wrong token"


def test_an_unreachable_studio_is_a_studio_error() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    http = httpx2.Client(transport=httpx2.MockTransport(refuse))

    client = StudioClient("http://studio.invalid:8790", "token", http=http)
    with client, pytest.raises(StudioError) as caught:
        client.songs()

    assert str(caught.value) == "could not reach the Studio (connection refused)"


def test_an_invalid_listing_is_a_studio_error() -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=[{"id": "only-an-id"}])

    http = httpx2.Client(transport=httpx2.MockTransport(answer))

    client = StudioClient("http://studio.invalid:8790", "token", http=http)
    with client, pytest.raises(StudioError) as caught:
        client.songs()

    assert str(caught.value).startswith("the Studio sent an invalid song list (")


def test_the_base_url_may_end_in_a_slash(studio) -> None:
    studio.add(SONG)

    with StudioClient("http://testserver/", "test-token", http=studio.http()) as client:
        assert [song.id for song in client.songs()] == [SONG]

    assert studio.requests[0].url.path == "/songs"
