"""The playlist of the night (docs/PLAN.md sections 5.7 and 5.8).

It lives in the Stage's memory while `stage serve` runs, and a restart starts
with an empty one. The first entry is the current song. A song already in the
library is ready at once; any other one becomes a Studio job, which a worker
thread follows until the package is downloaded to the Stage.
"""

import logging
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from core.manifest import YouTubeMusicSource
from core.studio_api import Job, JobRequest
from pydantic import ValidationError

from stage import library as lib
from stage.client import StudioClient, StudioError, UnknownJob
from stage.search import Result, from_library
from stage.sync import Progress, fetch

log = logging.getLogger(__name__)

POLL_S = 2.0

EntryState = Literal[
    "queued",
    "starting",
    "downloading",
    "separating",
    "lyrics",
    "packaging",
    "fetching",
    "ready",
    "failed",
]
FailureCode = Literal[
    "no_studio", "studio_error", "job_failed", "fetch_failed", "internal_error"
]
FINISHED = ("ready", "failed")

NO_STUDIO = (
    "stage serve runs without --studio or the STUDIO_TOKEN environment variable, "
    "so only songs in the library can be added"
)


class NotAddable(Exception):
    """The song is not in the library and cannot become a Studio job."""


@dataclass
class Entry:
    """One song of the playlist and how far it is from being playable.

    step is the separation step while separating, progress the download to
    the Stage while fetching, error a FailureCode once failed. detail is an
    English line for the logs: why it failed, or the last error of a job that
    waits for a retry.
    """

    id: str
    song: Result
    state: EntryState
    step: str | None = None
    progress: Progress | None = None
    error: FailureCode | None = None
    detail: str | None = None
    job_id: str | None = None
    request: JobRequest | None = None


def job_state(job: Job) -> EntryState:
    """The entry state a Studio job shows, before it is done or failed.

    A job queued after its first attempt started, with no retry pending, is
    the Studio importing its models: A4 saw it last about 36 s.
    """
    if job.status == "queued" and job.attempt >= 1 and job.retry_at is None:
        return "starting"
    return job.status


class Playlist:
    """The entries in order. Every method is safe to call from any thread.

    studio is None when the Stage has no Studio to send new songs to.
    """

    def __init__(
        self, library: Path, studio: StudioClient | None, *, poll_s: float = POLL_S
    ) -> None:
        self._library = library
        self._studio = studio
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._entries: list[Entry] = []
        self._wake = threading.Event()
        self._stopping = False
        self._thread: threading.Thread | None = None

    def add(self, song: Result) -> Entry:
        """Append song: ready when its song_id is in the library, else queued."""
        held = lib.scan(self._library).get(song.song_id) if song.song_id else None
        if held is not None:
            entry = Entry(_new_id(), from_library(song.song_id, held), "ready")
        else:
            entry = Entry(_new_id(), song, "queued", request=_job_request(song))
        with self._lock:
            self._entries.append(entry)
        self._wake.set()
        return replace(entry)

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            for entry in self._entries:
                if entry.id == entry_id:
                    self._entries.remove(entry)
                    return True
        return False

    def entries(self) -> list[Entry]:
        with self._lock:
            return [replace(entry) for entry in self._entries]

    def first(self) -> Entry | None:
        with self._lock:
            return replace(self._entries[0]) if self._entries else None

    def get(self, entry_id: str) -> Entry | None:
        with self._lock:
            for entry in self._entries:
                if entry.id == entry_id:
                    return replace(entry)
        return None

    def advance(self) -> None:
        """Take every entry that is not ready or failed one step further."""
        with self._lock:
            pending = [e for e in self._entries if e.state not in FINISHED]
        for entry in pending:
            try:
                self._advance(entry)
            except Exception as error:
                # The worker must outlive a bug, or every later entry waits forever.
                log.exception("entry %s of %s failed", entry.id, entry.song.title)
                self._fail(entry, "internal_error", str(error) or type(error).__name__)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._work, name="stage-playlist", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Ask the worker to end after its current step; it does not wait."""
        self._stopping = True
        self._wake.set()

    def _work(self) -> None:
        while True:
            self._wake.wait(self._poll_s)
            self._wake.clear()
            if self._stopping:
                return
            self.advance()

    def _advance(self, entry: Entry) -> None:
        if self._studio is None:
            self._fail(entry, "no_studio", NO_STUDIO)
            return
        try:
            if entry.job_id is None:
                self._submit(entry)
            try:
                job = self._studio.job(entry.job_id)
            except UnknownJob:
                # The Studio forgets its jobs when it restarts. Submitted again,
                # it finds the package if the job had finished.
                log.warning(
                    "the Studio forgot the job of %s; submitting it again",
                    entry.song.title,
                )
                self._submit(entry)
                job = self._studio.job(entry.job_id)
        except StudioError as error:
            self._fail(entry, "studio_error", str(error))
            return
        if job.status == "failed":
            self._fail(entry, "job_failed", job.error or "the Studio job failed")
        elif job.status == "done":
            self._fetch(entry)
        else:
            self._set(entry, state=job_state(job), step=job.step, detail=job.error)

    def _submit(self, entry: Entry) -> None:
        created = self._studio.create_job(entry.request)
        self._set(
            entry,
            job_id=created.job_id,
            song=replace(entry.song, song_id=created.song_id),
        )

    def _fetch(self, entry: Entry) -> None:
        song_id = entry.song.song_id
        self._set(entry, state="fetching", step=None, detail=None)
        try:
            outcome = fetch(
                self._library,
                self._studio,
                song_id,
                lambda progress: self._set(entry, progress=progress),
            )
        except StudioError as error:
            self._fail(entry, "studio_error", str(error))
            return
        # busy: the library keeps the version it holds, which plays.
        if outcome is None or outcome.action in ("added", "updated", "busy"):
            self._set(entry, state="ready")
        else:
            self._fail(entry, "fetch_failed", outcome.reason)

    def _set(self, entry: Entry, **fields) -> None:
        with self._lock:
            for name, value in fields.items():
                setattr(entry, name, value)

    def _fail(self, entry: Entry, code: FailureCode, detail: str) -> None:
        log.warning("%s failed (%s): %s", entry.song.title, code, detail)
        self._set(entry, state="failed", step=None, error=code, detail=detail)


def _new_id() -> str:
    return uuid.uuid4().hex


def _job_request(song: Result) -> JobRequest:
    if song.video_id is None:
        raise NotAddable(f"{song.title!r} is not in the library and has no video_id")
    try:
        return JobRequest(
            source=YouTubeMusicSource(type="youtube_music", video_id=song.video_id),
            title=song.title,
            artist=song.artist,
            album=song.album,
            duration_s=song.duration_s,
        )
    except ValidationError as error:
        raise NotAddable(
            f"not a valid song for a Studio job ({lib.describe(error)})"
        ) from None
