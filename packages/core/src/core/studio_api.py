"""Requests and responses of the Studio API, described in docs/PLAN.md section 5.3."""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel

from core.manifest import YouTubeMusicSource

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
"""A YouTube video id, used with fullmatch. The `studio process` CLI checks the same."""

JobStatus = Literal[
    "queued", "downloading", "separating", "lyrics", "packaging", "done", "failed"
]
SeparationStep = Literal["vocals", "karaoke", "instruments"]


def _check_video_id(source: YouTubeMusicSource) -> YouTubeMusicSource:
    if not VIDEO_ID.fullmatch(source.video_id):
        raise ValueError("video_id must be 11 characters of A-Z, a-z, 0-9, _ or -")
    return source


class JobRequest(BaseModel):
    """POST /jobs. Local files stay CLI only, so the source is always YouTube Music.

    Only the request checks the video_id: the manifest model takes any, so
    packages already in the library keep loading.
    """

    source: Annotated[YouTubeMusicSource, AfterValidator(_check_video_id)]
    title: str
    artist: str
    album: str | None = None
    duration_s: float | None = None


class JobCreated(BaseModel):
    job_id: str
    song_id: str


class Job(BaseModel):
    """GET /jobs/{job_id}.

    attempt counts the runs started so far. A download failure that will be
    tried again leaves the job queued with retry_at set and the last error,
    until the next attempt starts and clears both.
    """

    job_id: str
    song_id: str
    status: JobStatus
    step: SeparationStep | None = None
    attempt: int
    retry_at: AwareDatetime | None = None
    error: str | None = None


class SongSummary(BaseModel):
    """One package of GET /songs."""

    id: str
    title: str
    artist: str
    duration_s: float
    pipeline_version: int
    created_at: AwareDatetime


class DeleteResult(BaseModel):
    """DELETE /songs: busy lists the packages left in place because a file was open."""

    deleted: list[str]
    busy: list[str]
