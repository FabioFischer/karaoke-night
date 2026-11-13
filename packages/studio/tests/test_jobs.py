import threading
from datetime import UTC, datetime, timedelta

from core.studio_api import JobRequest
from studio.jobs import RETRY_DELAYS, JobQueue

START = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class Builds:
    """A fake build: plans maps a video id to what each of its runs does.

    A plan entry is None for a run that finishes, or (stage, error) for one
    that raises error once stage is reached.
    """

    def __init__(self, **plans: list) -> None:
        self.plans = plans
        self.runs: list[str] = []

    def __call__(self, request: JobRequest, on_stage) -> None:
        video_id = request.source.video_id
        self.runs.append(video_id)
        plan = self.plans.get(video_id, [])
        outcome = plan.pop(0) if plan else None
        for stage in ("downloading", "separating", "lyrics", "packaging"):
            on_stage(stage, "vocals" if stage == "separating" else None)
            if outcome is not None and outcome[0] == stage:
                raise outcome[1]


def request(video_id: str) -> JobRequest:
    return JobRequest.model_validate(
        {
            "source": {"type": "youtube_music", "video_id": video_id},
            "title": "Song Title",
            "artist": "Artist Name",
        }
    )


def forbidden() -> tuple[str, Exception]:
    return ("downloading", OSError("HTTP Error 403: Forbidden"))


def test_retry_delays_are_2_10_and_30_minutes() -> None:
    assert RETRY_DELAYS == (
        timedelta(minutes=2),
        timedelta(minutes=10),
        timedelta(minutes=30),
    )


def test_new_job_is_queued_with_no_attempt_yet() -> None:
    queue = JobQueue(Builds(), clock=Clock())

    job = queue.submit(request("AAAAAAAAAAA"), "song-a")

    assert queue.get(job.job_id) == job
    assert (job.song_id, job.status, job.step, job.attempt) == (
        "song-a",
        "queued",
        None,
        0,
    )
    assert (job.retry_at, job.error) == (None, None)


def test_job_runs_to_done() -> None:
    builds = Builds()
    queue = JobQueue(builds, clock=Clock())
    job = queue.submit(request("AAAAAAAAAAA"), "song-a")

    assert queue.run_ready() is True

    done = queue.get(job.job_id)
    assert (done.status, done.step, done.attempt, done.error) == ("done", None, 1, None)
    assert queue.run_ready() is False


def test_status_and_step_follow_the_stages() -> None:
    seen: list[tuple] = []
    queue: JobQueue

    def build(request, on_stage):
        for status, step in [
            ("downloading", None),
            ("separating", "vocals"),
            ("separating", "karaoke"),
            ("lyrics", None),
        ]:
            on_stage(status, step)
            job = queue.get(job_id)
            seen.append((job.status, job.step, job.attempt))

    queue = JobQueue(build, clock=Clock())
    job_id = queue.submit(request("AAAAAAAAAAA"), "song-a").job_id
    queue.run_ready()

    assert seen == [
        ("downloading", None, 1),
        ("separating", "vocals", 1),
        ("separating", "karaoke", 1),
        ("lyrics", None, 1),
    ]


def test_download_failure_is_retried_then_succeeds() -> None:
    clock = Clock()
    builds = Builds(AAAAAAAAAAA=[forbidden()])
    queue = JobQueue(builds, clock=clock)
    job = queue.submit(request("AAAAAAAAAAA"), "song-a")

    queue.run_ready()

    waiting = queue.get(job.job_id)
    assert (waiting.status, waiting.step, waiting.attempt) == ("queued", None, 1)
    assert waiting.retry_at == START + timedelta(minutes=2)
    assert waiting.error == "HTTP Error 403: Forbidden"
    clock.advance(timedelta(minutes=2) - timedelta(seconds=1))
    assert queue.run_ready() is False

    clock.advance(timedelta(seconds=1))
    assert queue.run_ready() is True

    done = queue.get(job.job_id)
    assert (done.status, done.attempt, done.retry_at, done.error) == (
        "done",
        2,
        None,
        None,
    )
    assert builds.runs == ["AAAAAAAAAAA", "AAAAAAAAAAA"]


def test_three_retries_then_failed() -> None:
    clock = Clock()
    queue = JobQueue(Builds(AAAAAAAAAAA=[forbidden()] * 4), clock=clock)
    job = queue.submit(request("AAAAAAAAAAA"), "song-a")
    retry_ats = []

    queue.run_ready()
    for delay in RETRY_DELAYS:
        waiting = queue.get(job.job_id)
        assert waiting.status == "queued"
        retry_ats.append(waiting.retry_at - clock.now)
        clock.advance(delay)
        assert queue.run_ready() is True

    failed = queue.get(job.job_id)
    assert retry_ats == list(RETRY_DELAYS)
    assert (failed.status, failed.attempt, failed.retry_at) == ("failed", 4, None)
    assert failed.error == "HTTP Error 403: Forbidden"
    clock.advance(timedelta(hours=1))
    assert queue.run_ready() is False


def test_other_jobs_run_while_one_waits_for_its_retry() -> None:
    builds = Builds(AAAAAAAAAAA=[forbidden()])
    queue = JobQueue(builds, clock=Clock())
    waiting = queue.submit(request("AAAAAAAAAAA"), "song-a")
    queue.run_ready()
    other = queue.submit(request("BBBBBBBBBBB"), "song-b")

    assert queue.run_ready() is True

    assert queue.get(other.job_id).status == "done"
    assert queue.get(waiting.job_id).status == "queued"
    assert builds.runs == ["AAAAAAAAAAA", "BBBBBBBBBBB"]


def test_failure_after_the_download_is_not_retried() -> None:
    for stage in ("separating", "lyrics", "packaging"):
        queue = JobQueue(
            Builds(AAAAAAAAAAA=[(stage, RuntimeError("model crashed"))]),
            clock=Clock(),
        )
        job = queue.submit(request("AAAAAAAAAAA"), "song-a")

        queue.run_ready()

        failed = queue.get(job.job_id)
        assert (failed.status, failed.step, failed.attempt) == ("failed", None, 1)
        assert (failed.retry_at, failed.error) == (None, "model crashed")


def test_active_finds_queued_waiting_and_running_jobs_but_not_finished_ones() -> None:
    queue = JobQueue(Builds(AAAAAAAAAAA=[forbidden()]), clock=Clock())
    waiting = queue.submit(request("AAAAAAAAAAA"), "song-a")
    queue.run_ready()
    queued = queue.submit(request("BBBBBBBBBBB"), "song-b")

    assert queue.active("AAAAAAAAAAA") == queue.get(waiting.job_id)
    assert queue.active("BBBBBBBBBBB") == queued
    assert queue.active("CCCCCCCCCCC") is None
    queue.run_ready()
    assert queue.active("BBBBBBBBBBB") is None


def test_done_job_needs_no_run() -> None:
    builds = Builds()
    queue = JobQueue(builds, clock=Clock())

    job = queue.add_done("song-a")

    assert (job.status, job.song_id, job.attempt) == ("done", "song-a", 0)
    assert queue.get(job.job_id) == job
    assert queue.run_ready() is False
    assert builds.runs == []


def test_unknown_job_is_none() -> None:
    assert JobQueue(Builds(), clock=Clock()).get("missing") is None


def test_worker_thread_runs_jobs_and_wakes_for_a_retry() -> None:
    clock = Clock()
    finished = threading.Event()
    builds = Builds(AAAAAAAAAAA=[forbidden()])

    def build(request, on_stage):
        builds(request, on_stage)
        finished.set()

    queue = JobQueue(build, clock=clock)
    queue.start()
    try:
        job = queue.submit(request("AAAAAAAAAAA"), "song-a")
        assert wait_for(lambda: queue.get(job.job_id).retry_at is not None)
        assert not finished.is_set()

        clock.advance(RETRY_DELAYS[0])
        queue.wake()

        assert finished.wait(timeout=5)
        assert wait_for(lambda: queue.get(job.job_id).status == "done")
    finally:
        queue.stop()


def wait_for(condition, timeout: float = 5.0) -> bool:
    deadline = threading.Event()
    timer = threading.Timer(timeout, deadline.set)
    timer.start()
    try:
        while not deadline.is_set():
            if condition():
                return True
            deadline.wait(0.01)
        return condition()
    finally:
        timer.cancel()


def test_worker_thread_runs_a_retry_when_its_time_comes_without_a_wake() -> None:
    finished = threading.Event()
    builds = Builds(AAAAAAAAAAA=[forbidden()])

    def build(request, on_stage):
        builds(request, on_stage)
        finished.set()

    queue = JobQueue(build, delays=(timedelta(milliseconds=50),))
    queue.start()
    try:
        job = queue.submit(request("AAAAAAAAAAA"), "song-a")

        assert finished.wait(timeout=5)
        assert wait_for(lambda: queue.get(job.job_id).status == "done")
        assert builds.runs == ["AAAAAAAAAAA", "AAAAAAAAAAA"]
    finally:
        queue.stop()


def test_next_attempt_clears_the_previous_error() -> None:
    clock = Clock()
    seen: list[tuple] = []
    queue: JobQueue
    builds = Builds(AAAAAAAAAAA=[forbidden()])

    def build(request, on_stage):
        def watching(status, step=None):
            on_stage(status, step)
            if status == "downloading":
                job = queue.get(job_id)
                seen.append((job.attempt, job.status, job.error, job.retry_at))

        builds(request, watching)

    queue = JobQueue(build, clock=clock)
    job_id = queue.submit(request("AAAAAAAAAAA"), "song-a").job_id
    queue.run_ready()
    clock.advance(RETRY_DELAYS[0])
    queue.run_ready()

    assert seen == [(1, "downloading", None, None), (2, "downloading", None, None)]
