"""Requests and responses of the Studio API, described in docs/PLAN.md section 5.3."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel

from core.manifest import YouTubeMusicSource

JobStatus = Literal[
    "queued", "downloading", "separating", "lyrics", "packaging", "done", "failed"
]
SeparationStep = Literal["vocals", "karaoke", "instruments"]


class JobRequest(BaseModel):
    """POST /jobs. Local files stay CLI only, so the source is always YouTube Music."""

    source: YouTubeMusicSource
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
    tried again leaves the job queued with retry_at set and the last error.
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
