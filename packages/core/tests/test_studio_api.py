from datetime import UTC, datetime

import pytest
from core.manifest import YouTubeMusicSource
from core.studio_api import DeleteResult, Job, JobCreated, JobRequest, SongSummary
from pydantic import ValidationError


def test_job_request_takes_a_youtube_music_source() -> None:
    request = JobRequest.model_validate(
        {
            "source": {"type": "youtube_music", "video_id": "XXXXXXXXXXX"},
            "title": "Song Title",
            "artist": "Artist Name",
        }
    )

    assert request.source == YouTubeMusicSource(
        type="youtube_music", video_id="XXXXXXXXXXX"
    )
    assert (request.album, request.duration_s) == (None, None)


def test_job_request_keeps_album_and_duration() -> None:
    request = JobRequest.model_validate(
        {
            "source": {"type": "youtube_music", "video_id": "XXXXXXXXXXX"},
            "title": "Song Title",
            "artist": "Artist Name",
            "album": "Album Name",
            "duration_s": 245.3,
        }
    )

    assert (request.album, request.duration_s) == ("Album Name", 245.3)


def test_job_request_rejects_a_file_source() -> None:
    with pytest.raises(ValidationError):
        JobRequest.model_validate(
            {
                "source": {"type": "file", "sha256": "0" * 64},
                "title": "Song Title",
                "artist": "Artist Name",
            }
        )


@pytest.mark.parametrize("missing", ["source", "title", "artist"])
def test_job_request_needs_source_title_and_artist(missing: str) -> None:
    data = {
        "source": {"type": "youtube_music", "video_id": "XXXXXXXXXXX"},
        "title": "Song Title",
        "artist": "Artist Name",
    }
    del data[missing]

    with pytest.raises(ValidationError):
        JobRequest.model_validate(data)


def test_job_shape() -> None:
    job = Job(
        job_id="job-1",
        song_id="song-title--artist-name--a1b2c3",
        status="queued",
        attempt=1,
        retry_at=datetime(2026, 11, 13, 20, 2, tzinfo=UTC),
        error="HTTP Error 403: Forbidden",
    )

    assert job.model_dump(mode="json") == {
        "job_id": "job-1",
        "song_id": "song-title--artist-name--a1b2c3",
        "status": "queued",
        "step": None,
        "attempt": 1,
        "retry_at": "2026-11-13T20:02:00Z",
        "error": "HTTP Error 403: Forbidden",
    }


def test_job_step_is_one_of_the_separation_steps() -> None:
    job = Job(job_id="j", song_id="s", status="separating", step="karaoke", attempt=1)

    assert job.step == "karaoke"
    with pytest.raises(ValidationError):
        Job(job_id="j", song_id="s", status="separating", step="mixing", attempt=1)


def test_job_status_is_one_of_the_listed_ones() -> None:
    with pytest.raises(ValidationError):
        Job(job_id="j", song_id="s", status="paused", attempt=0)


def test_job_created_song_summary_and_delete_result_shapes() -> None:
    assert JobCreated(job_id="j", song_id="s").model_dump() == {
        "job_id": "j",
        "song_id": "s",
    }
    summary = SongSummary(
        id="s",
        title="Song Title",
        artist="Artist Name",
        duration_s=245.3,
        pipeline_version=1,
        created_at=datetime(2026, 11, 4, 20, tzinfo=UTC),
    )
    assert summary.model_dump(mode="json") == {
        "id": "s",
        "title": "Song Title",
        "artist": "Artist Name",
        "duration_s": 245.3,
        "pipeline_version": 1,
        "created_at": "2026-11-04T20:00:00Z",
    }
    assert DeleteResult(deleted=["a"], busy=["b"]).model_dump() == {
        "deleted": ["a"],
        "busy": ["b"],
    }


@pytest.mark.parametrize(
    "video_id",
    ["XXXXXXXXXX", "XXXXXXXXXXXX", "XXXXXXXXXX!", "XXXXXXXXXX\n", ""],
    ids=["short", "long", "symbol", "newline", "empty"],
)
def test_job_request_rejects_a_malformed_video_id(video_id: str) -> None:
    with pytest.raises(ValidationError):
        JobRequest.model_validate(
            {
                "source": {"type": "youtube_music", "video_id": video_id},
                "title": "Song Title",
                "artist": "Artist Name",
            }
        )


def test_manifest_source_keeps_any_video_id() -> None:
    """Packages already on disk must keep loading: only new requests are checked."""
    assert YouTubeMusicSource(type="youtube_music", video_id="not an id").video_id == (
        "not an id"
    )
