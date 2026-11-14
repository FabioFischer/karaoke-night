import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from core.studio_api import Job, JobCreated, JobRequest
from stage.client import StudioClient, StudioError, UnknownJob
from stage.playlist import NotAddable, Playlist
from stage.search import Result

SONG = "song-title--artist-name--a1b2c3"
VIDEO_ID = "XXXXXXXXXXX"
NEW_VIDEO_ID = "YYYYYYYYYYY"


def new_song(video_id: str = NEW_VIDEO_ID) -> Result:
    return Result(
        title="Song Title",
        artist="Artist Name",
        origin="ytmusic",
        video_id=video_id,
        album="Album Name",
        duration_s=245.3,
        thumbnail_url="https://example.invalid/thumbnail.jpg",
    )


def job(status: str, **fields) -> Job:
    return Job(
        job_id="job-1",
        song_id=SONG,
        status=status,
        attempt=fields.pop("attempt", 1),
        **fields,
    )


class ScriptedStudio:
    """A Studio client whose jobs answer from a script.

    Each job answers the statuses of script in order, the last one repeating.
    Song listings, manifests and files come from the real Studio of conftest.
    forget() makes the Studio forget every job, as a restart does.
    """

    def __init__(self, real: StudioClient, script: list[Job]) -> None:
        self.real = real
        self.script = script
        self.created: list[JobRequest] = []
        self.reads: dict[str, int] = {}
        self.forgotten: set[str] = set()
        self.unreachable = False

    def create_job(self, request: JobRequest) -> JobCreated:
        if self.unreachable:
            raise StudioError("could not reach the Studio (connection refused)")
        self.created.append(request)
        return JobCreated(job_id=f"job-{len(self.created)}", song_id=SONG)

    def job(self, job_id: str) -> Job:
        if self.unreachable:
            raise StudioError("could not reach the Studio (connection refused)")
        if job_id in self.forgotten:
            raise UnknownJob("the Studio answered 404: unknown job", 404)
        read = self.reads.get(job_id, 0)
        self.reads[job_id] = read + 1
        answer = self.script[min(read, len(self.script) - 1)]
        return answer.model_copy(update={"job_id": job_id})

    def forget(self) -> None:
        self.forgotten.update(f"job-{n}" for n in range(1, len(self.created) + 1))

    def songs(self):
        return self.real.songs()

    def manifest(self, song_id: str) -> bytes:
        return self.real.manifest(song_id)

    def download(self, *args, **kwargs) -> int:
        return self.real.download(*args, **kwargs)


@pytest.fixture
def scripted(studio):
    def make(*script: Job) -> ScriptedStudio:
        return ScriptedStudio(studio.client(), list(script))

    return make


def states(playlist: Playlist) -> list[tuple]:
    return [(entry.state, entry.step) for entry in playlist.entries()]


def test_a_library_song_is_ready_at_once(studio, local_library) -> None:
    local_library.add(SONG, "Song Title", "Artist Name", video_id=VIDEO_ID)
    playlist = Playlist(local_library.path, None)

    entry = playlist.add(
        Result(title="Song Title", artist="Artist Name", origin="library", song_id=SONG)
    )
    playlist.advance()

    assert entry.state == "ready"
    assert entry.song.song_id == SONG
    assert entry.song.video_id == VIDEO_ID
    assert entry.song.duration_s == 245.3
    assert states(playlist) == [("ready", None)]


def test_a_new_song_goes_through_every_studio_state_then_is_fetched(
    studio, library: Path, scripted
) -> None:
    studio.add(SONG)
    fake = scripted(
        job("queued", attempt=0),
        job("queued"),
        job("downloading"),
        job("separating", step="vocals"),
        job("separating", step="karaoke"),
        job("lyrics"),
        job("packaging"),
        job("done"),
    )
    playlist = Playlist(library, fake)
    entry = playlist.add(new_song())
    assert entry.state == "queued"

    seen = []
    for _ in range(8):
        playlist.advance()
        seen.extend(states(playlist))

    assert seen == [
        ("queued", None),
        ("starting", None),
        ("downloading", None),
        ("separating", "vocals"),
        ("separating", "karaoke"),
        ("lyrics", None),
        ("packaging", None),
        ("ready", None),
    ]
    (request,) = fake.created
    assert request.model_dump(mode="json") == {
        "source": {"type": "youtube_music", "video_id": NEW_VIDEO_ID},
        "title": "Song Title",
        "artist": "Artist Name",
        "album": "Album Name",
        "duration_s": 245.3,
    }
    (ready,) = playlist.entries()
    assert ready.song.song_id == SONG
    assert (ready.progress.files_done, ready.progress.files_total) == (3, 3)
    assert (library / SONG / "manifest.json").is_file()


def test_the_download_to_the_stage_shows_its_progress(
    studio, library: Path, scripted
) -> None:
    studio.add(SONG)
    fake = scripted(job("done"))
    playlist = Playlist(library, fake)
    entry = playlist.add(new_song())
    seen: list[tuple] = []
    download = fake.download

    def watched(*args, **kwargs) -> int:
        (current,) = playlist.entries()
        seen.append((current.state, current.progress))
        return download(*args, **kwargs)

    fake.download = watched
    playlist.advance()

    assert [state for state, _ in seen] == ["fetching"] * 3
    assert [progress.files_done for _, progress in seen] == [0, 1, 2]
    assert playlist.get(entry.id).state == "ready"


def test_a_job_waiting_for_a_retry_is_queued_with_the_last_error(
    library: Path, scripted
) -> None:
    retry_at = datetime(2026, 11, 14, 21, 0, tzinfo=UTC)
    fake = scripted(job("queued", retry_at=retry_at, error="HTTP Error 403: Forbidden"))
    playlist = Playlist(library, fake)
    playlist.add(new_song())

    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.detail) == ("queued", "HTTP Error 403: Forbidden")


def test_a_failed_job_fails_the_entry_with_the_studio_error(
    library: Path, scripted
) -> None:
    fake = scripted(
        job("separating", step="vocals"), job("failed", error="out of memory")
    )
    playlist = Playlist(library, fake)
    playlist.add(new_song())

    playlist.advance()
    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.error, entry.detail) == (
        "failed",
        "job_failed",
        "out of memory",
    )


def test_a_job_the_studio_forgot_is_submitted_again(
    studio, library: Path, scripted
) -> None:
    studio.add(SONG)
    fake = scripted(job("downloading"), job("done"))
    playlist = Playlist(library, fake)
    playlist.add(new_song())
    playlist.advance()
    fake.forget()

    playlist.advance()

    assert len(fake.created) == 2
    assert fake.created[0] == fake.created[1]
    assert states(playlist) == [("downloading", None)]
    playlist.advance()
    assert states(playlist) == [("ready", None)]


def test_an_unreachable_studio_fails_the_entry(library: Path, scripted) -> None:
    fake = scripted(job("downloading"))
    fake.unreachable = True
    playlist = Playlist(library, fake)
    playlist.add(new_song())

    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.error, entry.detail) == (
        "failed",
        "studio_error",
        "could not reach the Studio (connection refused)",
    )


def test_a_studio_lost_mid_job_fails_the_entry(library: Path, scripted) -> None:
    fake = scripted(job("downloading"))
    playlist = Playlist(library, fake)
    playlist.add(new_song())
    playlist.advance()
    fake.unreachable = True

    playlist.advance()

    assert playlist.entries()[0].error == "studio_error"


def test_without_a_studio_a_new_song_fails(library: Path) -> None:
    playlist = Playlist(library, None)
    playlist.add(new_song())

    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.error) == ("failed", "no_studio")
    assert entry.detail == (
        "stage serve runs without --studio or the STUDIO_TOKEN environment "
        "variable, so only songs in the library can be added"
    )


def test_a_package_that_cannot_be_fetched_fails_the_entry(
    library: Path, scripted
) -> None:
    fake = scripted(job("done"))
    playlist = Playlist(library, fake)
    playlist.add(new_song())

    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.error, entry.detail) == (
        "failed",
        "fetch_failed",
        "the Studio does not list the song",
    )


def test_the_real_studio_hands_a_song_it_holds_to_the_stage(
    studio, library: Path
) -> None:
    studio.add(SONG)
    playlist = Playlist(library, studio.client())
    playlist.add(new_song(VIDEO_ID))

    playlist.advance()

    assert states(playlist) == [("ready", None)]
    assert playlist.entries()[0].song.song_id == SONG
    assert (library / SONG / "stems" / "lead.flac").is_file()


def test_a_song_that_is_neither_in_the_library_nor_on_youtube_is_refused(
    local_library,
) -> None:
    playlist = Playlist(local_library.path, None)

    with pytest.raises(NotAddable) as missing:
        playlist.add(Result(title="T", artist="A", origin="library", song_id=SONG))
    with pytest.raises(NotAddable) as invalid:
        playlist.add(new_song("not a video id"))

    assert str(missing.value) == "'T' is not in the library and has no video_id"
    assert str(invalid.value).startswith("not a valid song for a Studio job (")
    assert playlist.entries() == []


def test_entries_keep_their_order_and_can_be_removed(local_library) -> None:
    local_library.add(SONG, "Song Title", "Artist Name")
    playlist = Playlist(local_library.path, None)
    library_song = Result(
        title="Song Title", artist="A", origin="library", song_id=SONG
    )
    first = playlist.add(library_song)
    second = playlist.add(library_song)
    third = playlist.add(library_song)

    assert playlist.remove(second.id) is True
    assert playlist.remove("unknown") is False
    assert [entry.id for entry in playlist.entries()] == [first.id, third.id]
    assert playlist.first().id == first.id
    assert len({first.id, second.id, third.id}) == 3


def test_the_worker_thread_drives_the_jobs(studio, library: Path, scripted) -> None:
    studio.add(SONG)
    fake = scripted(job("downloading"), job("done"))
    playlist = Playlist(library, fake, poll_s=0.01)
    playlist.start()
    try:
        playlist.add(new_song())
        deadline = time.monotonic() + 5
        while states(playlist) != [("ready", None)] and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        playlist.stop()

    assert states(playlist) == [("ready", None)]
