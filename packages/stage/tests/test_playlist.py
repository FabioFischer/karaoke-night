import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from core.studio_api import Job, JobCreated, JobRequest
from stage.client import StudioClient, StudioError, StudioUnreachable, UnknownJob
from stage.playlist import NotAddable, Playlist
from stage.search import Result

UNREACHABLE = "could not reach the Studio (connection refused)"
REPROCESSED_AT = "2026-11-13T20:00:00Z"

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
    forget() makes the Studio forget every job, as a restart does. unreachable
    makes the job calls fail as a network error does, refusal as an error
    answer of the Studio does.
    """

    def __init__(self, real: StudioClient, script: list[Job]) -> None:
        self.real = real
        self.script = script
        self.created: list[JobRequest] = []
        self.reads: dict[str, int] = {}
        self.forgotten: set[str] = set()
        self.unreachable = False
        self.refusal: StudioError | None = None

    def _answer(self) -> None:
        if self.unreachable:
            raise StudioUnreachable(UNREACHABLE)
        if self.refusal is not None:
            raise self.refusal

    def create_job(self, request: JobRequest) -> JobCreated:
        self._answer()
        self.created.append(request)
        return JobCreated(job_id=f"job-{len(self.created)}", song_id=SONG)

    def job(self, job_id: str) -> Job:
        self._answer()
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


def test_an_unreachable_studio_keeps_the_entry_queued_and_retries(
    library: Path, scripted
) -> None:
    fake = scripted(job("downloading"))
    fake.unreachable = True
    playlist = Playlist(library, fake)
    playlist.add(new_song())

    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.error, entry.detail) == (
        "queued",
        "studio_unreachable",
        UNREACHABLE,
    )
    fake.unreachable = False
    playlist.advance()
    (entry,) = playlist.entries()
    assert (entry.state, entry.error, entry.detail) == ("downloading", None, None)
    assert len(fake.created) == 1


def test_a_studio_lost_mid_job_keeps_the_state_until_it_answers(
    studio, library: Path, scripted
) -> None:
    studio.add(SONG)
    fake = scripted(job("separating", step="vocals"), job("done"))
    playlist = Playlist(library, fake)
    playlist.add(new_song())
    playlist.advance()
    fake.unreachable = True

    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.step, entry.error, entry.detail) == (
        "separating",
        "vocals",
        "studio_unreachable",
        UNREACHABLE,
    )
    fake.unreachable = False
    playlist.advance()
    (entry,) = playlist.entries()
    assert (entry.state, entry.error, entry.detail) == ("ready", None, None)


def test_an_error_answer_of_the_studio_fails_the_entry(library: Path, scripted) -> None:
    fake = scripted(job("downloading"))
    playlist = Playlist(library, fake)
    playlist.add(new_song())
    playlist.advance()
    fake.refusal = StudioError("the Studio answered 401: missing or wrong token", 401)

    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.error, entry.detail) == (
        "failed",
        "studio_error",
        "the Studio answered 401: missing or wrong token",
    )


def test_a_fetch_cut_by_the_network_resumes_on_the_next_cycle(
    studio, library: Path, scripted
) -> None:
    studio.add(SONG)
    fake = scripted(job("done"))
    playlist = Playlist(library, fake)
    playlist.add(new_song())
    download = fake.download
    cut = []

    def cutting(song_id, path, target, on_chunk) -> int:
        if not cut:
            cut.append(path)
            target.write_bytes(studio.file(song_id, path).read_bytes()[:100])
            raise StudioUnreachable("could not reach the Studio (connection lost)")
        return download(song_id, path, target, on_chunk)

    fake.download = cutting
    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.error, entry.detail) == (
        "fetching",
        "studio_unreachable",
        "stems/lead.flac: could not reach the Studio (connection lost)",
    )
    assert (library / f".{SONG}.partial").is_dir()
    assert not (library / SONG).exists()
    playlist.advance()
    (entry,) = playlist.entries()
    assert (entry.state, entry.error, entry.detail) == ("ready", None, None)
    assert (library / SONG / "stems" / "lead.flac").read_bytes() == studio.file(
        SONG, "stems/lead.flac"
    ).read_bytes()


def test_a_stem_that_does_not_match_still_fails_the_entry(
    studio, library: Path, scripted
) -> None:
    studio.add(SONG)
    studio.file(SONG, "stems/lead.flac").write_bytes(b"not the stem")
    playlist = Playlist(library, scripted(job("done")))
    playlist.add(new_song())

    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.error) == ("failed", "fetch_failed")
    assert "stems/lead.flac does not match the sha256" in entry.detail


def test_an_entry_removed_while_the_worker_runs_gets_no_job(
    library: Path, scripted
) -> None:
    fake = scripted(job("downloading"))
    playlist = Playlist(library, fake)
    playlist.add(new_song())
    removed = playlist.add(new_song())
    create_job = fake.create_job

    def removing(request: JobRequest) -> JobCreated:
        playlist.remove(removed.id)
        return create_job(request)

    fake.create_job = removing
    playlist.advance()

    assert len(fake.created) == 1
    assert [entry.state for entry in playlist.entries()] == ["downloading"]


def test_an_entry_removed_during_its_fetch_stops_and_frees_the_worker(
    studio, library: Path, scripted
) -> None:
    studio.add(SONG)
    fake = scripted(job("done"))
    playlist = Playlist(library, fake)
    removed = playlist.add(new_song())
    playlist.add(new_song())
    download = fake.download
    files = []

    def removing(song_id, path, target, on_chunk) -> int:
        files.append(path)
        if len(files) == 1:
            playlist.remove(removed.id)
        return download(song_id, path, target, on_chunk)

    fake.download = removing
    playlist.advance()

    # The removed entry stopped inside its first file; the other one resumed
    # from the partial folder it left and downloaded every file.
    assert files == [
        "stems/lead.flac",
        "stems/lead.flac",
        "stems/other.flac",
        "lyrics.lrc",
    ]
    assert [entry.state for entry in playlist.entries()] == ["ready"]
    assert (library / SONG / "manifest.json").is_file()


def test_an_update_a_loaded_song_blocks_leaves_the_entry_ready(
    studio, library: Path, scripted, refuse_rename
) -> None:
    studio.add(SONG)
    installed = Playlist(library, scripted(job("done")))
    installed.add(new_song())
    installed.advance()
    studio.add(SONG, created_at=REPROCESSED_AT)
    refuse_rename(SONG)
    playlist = Playlist(library, scripted(job("done")))
    playlist.add(new_song())

    playlist.advance()

    (entry,) = playlist.entries()
    assert (entry.state, entry.error) == ("ready", None)
    assert (library / SONG / "manifest.json").read_bytes() != studio.file(
        SONG, "manifest.json"
    ).read_bytes()


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


@pytest.mark.parametrize(
    ("moved", "by", "fixed", "order"),
    [
        (1, -1, None, [1, 0, 2]),
        (1, 1, None, [0, 2, 1]),
        (0, -1, None, [0, 1, 2]),
        (2, 1, None, [0, 1, 2]),
        (1, -1, 0, [0, 1, 2]),
        (2, -1, 0, [0, 2, 1]),
        (0, 1, 0, [0, 1, 2]),
        (9, 1, None, [0, 1, 2]),
    ],
)
def test_move_shifts_one_place_never_past_an_end_or_the_fixed_entry(
    local_library, moved, by, fixed, order
) -> None:
    local_library.add(SONG, "Song Title", "Artist Name")
    playlist = Playlist(local_library.path, None)
    library_song = Result(
        title="Song Title", artist="A", origin="library", song_id=SONG
    )
    entries = [playlist.add(library_song) for _ in range(3)]
    entry_id = entries[moved].id if moved < len(entries) else "unknown"

    playlist.move(entry_id, by, fixed=None if fixed is None else entries[fixed].id)

    assert [entry.id for entry in playlist.entries()] == [
        entries[index].id for index in order
    ]


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
