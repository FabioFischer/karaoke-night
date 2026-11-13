"""The job queue of the Studio API: in memory, one song at a time.

Jobs are lost on restart; the library is the source of truth. Only a failure
while downloading is tried again, after each of RETRY_DELAYS, and a job waiting
for its retry does not hold up the jobs behind it.
"""

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from core.studio_api import Job, JobRequest

RETRY_DELAYS = (timedelta(minutes=2), timedelta(minutes=10), timedelta(minutes=30))
FINISHED = ("done", "failed")

Build = Callable[[JobRequest, Callable[..., None]], None]
"""build(request, on_stage) builds the song, calling on_stage(status, step=None) per stage."""


def _now() -> datetime:
    return datetime.now(UTC)


class JobQueue:
    """Runs jobs one at a time, on a worker thread after start() or through run_ready()."""

    def __init__(
        self,
        build: Build,
        *,
        clock: Callable[[], datetime] = _now,
        delays: tuple[timedelta, ...] = RETRY_DELAYS,
    ) -> None:
        self._build = build
        self._clock = clock
        self._delays = delays
        self._changed = threading.Condition()
        self._jobs: dict[str, Job] = {}
        self._requests: dict[str, JobRequest] = {}
        self._pending: list[str] = []
        self._thread: threading.Thread | None = None
        self._stopping = False

    def submit(self, request: JobRequest, song_id: str) -> Job:
        job = Job(job_id=uuid.uuid4().hex, song_id=song_id, status="queued", attempt=0)
        with self._changed:
            self._jobs[job.job_id] = job
            self._requests[job.job_id] = request
            self._pending.append(job.job_id)
            self._changed.notify_all()
            return job.model_copy()

    def add_done(self, song_id: str) -> Job:
        """A job for a song already in the library."""
        job = Job(job_id=uuid.uuid4().hex, song_id=song_id, status="done", attempt=0)
        with self._changed:
            self._jobs[job.job_id] = job
            return job.model_copy()

    def get(self, job_id: str) -> Job | None:
        with self._changed:
            job = self._jobs.get(job_id)
            return None if job is None else job.model_copy()

    def active(self, video_id: str) -> Job | None:
        """The queued, waiting or running job of video_id."""
        with self._changed:
            for job_id, request in self._requests.items():
                job = self._jobs[job_id]
                if request.source.video_id == video_id and job.status not in FINISHED:
                    return job.model_copy()
            return None

    def run_ready(self) -> bool:
        """Run the first job that is ready now, if any, and say whether one ran."""
        with self._changed:
            job_id = self._take_ready()
        if job_id is None:
            return False
        self._run(job_id)
        return True

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._work, name="studio-jobs", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Ask the worker to end once its current job, if any, is over.

        It does not wait: the worker is a daemon thread, so a process that
        exits drops the running job, as a restart does.
        """
        with self._changed:
            self._stopping = True
            self._changed.notify_all()

    def wake(self) -> None:
        """Make the worker look at the clock again."""
        with self._changed:
            self._changed.notify_all()

    def _work(self) -> None:
        while True:
            with self._changed:
                while not self._stopping and (job_id := self._take_ready()) is None:
                    self._changed.wait(self._seconds_to_next_retry())
                if self._stopping:
                    return
            self._run(job_id)

    def _take_ready(self) -> str | None:
        now = self._clock()
        for job_id in self._pending:
            job = self._jobs[job_id]
            if job.retry_at is None or job.retry_at <= now:
                self._pending.remove(job_id)
                job.retry_at, job.error = None, None
                job.attempt += 1
                return job_id
        return None

    def _seconds_to_next_retry(self) -> float | None:
        """None waits for a notify: nothing is waiting for a retry."""
        retries = [self._jobs[job_id].retry_at for job_id in self._pending]
        if not retries:
            return None
        return max(0.0, (min(retries) - self._clock()).total_seconds())

    def _run(self, job_id: str) -> None:
        job = self._jobs[job_id]

        def on_stage(status: str, step: str | None = None) -> None:
            with self._changed:
                job.status, job.step = status, step

        try:
            self._build(self._requests[job_id], on_stage)
        except Exception as error:  # noqa: BLE001
            with self._changed:
                retry = job.status == "downloading" and job.attempt <= len(self._delays)
                job.step = None
                job.error = str(error) or type(error).__name__
                if retry:
                    job.status = "queued"
                    job.retry_at = self._clock() + self._delays[job.attempt - 1]
                    self._pending.append(job_id)
                else:
                    job.status = "failed"
                self._changed.notify_all()
            return
        with self._changed:
            job.status, job.step, job.error = "done", None, None
            self._changed.notify_all()
