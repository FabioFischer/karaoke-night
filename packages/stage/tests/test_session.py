import inspect
import shutil
import threading
import time

import pytest
from stage.player import ListenerSilent, ReaperError, ReaperUnreachable
from stage.playlist import Playlist
from stage.search import Result
from stage.session import Session, SessionError

FIRST = "first-song--artist-name--a1b2c3"
SECOND = "second-song--artist-name--d4e5f6"
STEMS = ("lead", "backing", "drums", "bass", "guitar", "piano", "other")


def library_song(song_id: str) -> Result:
    return Result(
        title="Song Title", artist="Artist Name", origin="library", song_id=song_id
    )


def new_song() -> Result:
    return Result(
        title="New Song", artist="Artist Name", origin="ytmusic", video_id="YYYYYYYYYYY"
    )


@pytest.fixture
def playlist(packages) -> Playlist:
    packages.add(FIRST, duration_s=200.0)
    packages.add(SECOND, duration_s=180.0)
    return Playlist(packages.path, None)


@pytest.fixture
def session(playlist, packages, player) -> Session:
    return Session(playlist, packages.path, player, follow=False)


def mutes(player) -> dict[str, bool]:
    return {call[1]: call[2] for call in player.calls if call[0] == "set_mute"}


def refused(action, code: str) -> SessionError:
    with pytest.raises(SessionError) as caught:
        action()
    assert caught.value.code == code
    return caught.value


def test_a_new_session_is_in_setup_with_the_lead_muted(session) -> None:
    view = session.view()

    assert view.state == "setup"
    assert view.muted == ("lead",)
    assert view.key == 0
    assert view.entry_id is None
    assert view.error is None


def test_setup_shows_the_first_entry(session, playlist) -> None:
    entry = playlist.add(library_song(FIRST))

    view = session.view()

    assert view.entry_id == entry.id
    assert view.duration_s == 200.0
    assert view.position_s is None


def test_start_loads_sets_every_stem_transposes_then_plays(
    session, playlist, packages, player
) -> None:
    entry = playlist.add(library_song(FIRST))
    session.configure(["lead", "backing"], -2)

    session.start()

    manifest = (packages.path / FIRST / "manifest.json").resolve()
    assert player.calls[0] == ("load", manifest)
    assert player.names()[1:] == ["set_mute"] * 7 + ["transpose", "play"]
    assert mutes(player) == {stem: stem in ("lead", "backing") for stem in STEMS}
    assert player.calls[-2] == ("transpose", -2)
    view = session.view()
    assert (view.state, view.entry_id, view.position_s, view.duration_s) == (
        "playing",
        entry.id,
        0.0,
        200.0,
    )


def test_start_with_an_empty_playlist_is_refused(session, player) -> None:
    refused(session.start, "no_song")

    assert player.calls == []


def test_start_of_a_song_not_ready_is_refused(session, playlist, player) -> None:
    playlist.add(new_song())

    refused(session.start, "not_ready")

    assert player.calls == []
    assert session.view().state == "setup"


def test_settings_change_only_in_setup(session, playlist) -> None:
    playlist.add(library_song(FIRST))
    session.start()

    refused(lambda: session.configure([], 1), "wrong_state")
    assert session.view().muted == ("lead",)


def test_polls_follow_the_position_until_the_song_ends(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    player.positions = [0.5, 1.0, 200.0]
    player.end = True
    session.start()

    session.poll()
    assert (session.view().state, session.view().position_s) == ("playing", 0.5)
    session.poll()
    session.poll()

    assert (session.view().state, session.view().position_s) == ("results", 200.0)


def test_stop_asks_reaper_to_stop_and_shows_the_results(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    session.start()

    session.stop()

    assert player.calls[-1] == ("stop",)
    assert session.view().state == "results"
    refused(session.stop, "wrong_state")


def test_play_next_keeps_the_mutes_and_resets_the_key(
    session, playlist, packages, player
) -> None:
    finished = playlist.add(library_song(FIRST))
    following = playlist.add(library_song(SECOND))
    session.configure(["drums"], 3)
    session.start()
    session.stop()
    player.calls.clear()

    session.play_next()

    assert [entry.id for entry in playlist.entries()] == [following.id]
    assert player.calls[0] == (
        "load",
        (packages.path / SECOND / "manifest.json").resolve(),
    )
    assert mutes(player) == {stem: stem == "drums" for stem in STEMS}
    assert ("transpose", 0) in player.calls
    view = session.view()
    assert (view.state, view.entry_id, view.muted, view.key) == (
        "playing",
        following.id,
        ("drums",),
        0,
    )
    assert finished.id not in [entry.id for entry in playlist.entries()]


def test_play_next_of_a_song_not_ready_changes_nothing(
    session, playlist, player
) -> None:
    finished = playlist.add(library_song(FIRST))
    playlist.add(new_song())
    session.start()
    session.stop()

    refused(session.play_next, "not_ready")

    assert playlist.first().id == finished.id
    assert session.view().state == "results"


def test_play_next_of_a_song_gone_from_the_library_changes_nothing(
    session, playlist, packages, player
) -> None:
    finished = playlist.add(library_song(FIRST))
    following = playlist.add(library_song(SECOND))
    session.configure(["drums"], 3)
    session.start()
    session.stop()
    shutil.rmtree(packages.path / SECOND)
    calls = len(player.calls)

    refused(session.play_next, "not_ready")

    assert [entry.id for entry in playlist.entries()] == [finished.id, following.id]
    view = session.view()
    assert (view.state, view.entry_id, view.muted, view.key) == (
        "results",
        finished.id,
        ("drums",),
        3,
    )
    assert len(player.calls) == calls


def test_play_next_without_a_next_song_is_refused(session, playlist) -> None:
    playlist.add(library_song(FIRST))
    session.start()
    session.stop()

    refused(session.play_next, "no_song")


def test_setup_next_removes_the_song_and_resets_the_settings(session, playlist) -> None:
    playlist.add(library_song(FIRST))
    following = playlist.add(library_song(SECOND))
    session.configure(["drums"], 3)
    session.start()
    session.stop()

    session.setup_next()

    view = session.view()
    assert (view.state, view.entry_id, view.muted, view.key) == (
        "setup",
        following.id,
        ("lead",),
        0,
    )
    assert [entry.id for entry in playlist.entries()] == [following.id]
    assert session.view().position_s is None


def test_actions_from_results_only_from_results(session, playlist) -> None:
    playlist.add(library_song(FIRST))

    refused(session.play_next, "wrong_state")
    refused(session.setup_next, "wrong_state")


def test_skip_removes_the_current_song_and_resets_the_settings(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    following = playlist.add(library_song(SECOND))
    session.configure([], 2)

    session.skip()

    view = session.view()
    assert (view.state, view.entry_id, view.muted, view.key) == (
        "setup",
        following.id,
        ("lead",),
        0,
    )
    assert player.calls == []


def test_skip_needs_a_song_and_the_setup(session, playlist) -> None:
    refused(session.skip, "no_song")
    playlist.add(library_song(FIRST))
    session.start()
    refused(session.skip, "wrong_state")


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ReaperUnreachable("could not reach REAPER"), "reaper_unreachable"),
        (ListenerSilent("the listener did not answer"), "listener_silent"),
        (ReaperError("REAPER has no track named 'lead'"), "reaper_failed"),
    ],
)
def test_a_reaper_error_at_start_stays_in_setup(
    session, playlist, player, error, code
) -> None:
    playlist.add(library_song(FIRST))
    player.failures["load"] = error

    caught = refused(session.start, code)

    assert caught.detail == str(error)
    view = session.view()
    assert (view.state, view.error, view.detail) == ("setup", code, str(error))


def test_a_successful_start_clears_the_last_error(session, playlist, player) -> None:
    playlist.add(library_song(FIRST))
    player.failures["load"] = ReaperUnreachable("could not reach REAPER")
    refused(session.start, "reaper_unreachable")
    del player.failures["load"]

    session.start()

    assert session.view().error is None


def test_stop_with_reaper_gone_still_shows_the_results(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    session.start()
    player.failures["stop"] = ReaperUnreachable("could not reach REAPER")

    refused(session.stop, "reaper_unreachable")

    assert session.view().state == "results"
    assert session.view().error == "reaper_unreachable"


def test_a_reaper_error_in_play_next_leaves_the_setup_of_the_next_song(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    following = playlist.add(library_song(SECOND))
    session.configure(["drums"], 3)
    session.start()
    session.stop()
    player.failures["load"] = ListenerSilent("the listener did not answer")

    refused(session.play_next, "listener_silent")

    assert [entry.id for entry in playlist.entries()] == [following.id]
    view = session.view()
    assert (view.state, view.entry_id, view.muted, view.key, view.error) == (
        "setup",
        following.id,
        ("drums",),
        0,
        "listener_silent",
    )


def test_a_failed_poll_keeps_playing_and_shows_the_error(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    session.start()
    player.failures["position"] = ReaperUnreachable("could not reach REAPER")

    session.poll()

    assert (session.view().state, session.view().error) == (
        "playing",
        "reaper_unreachable",
    )
    del player.failures["position"]
    session.poll()
    assert session.view().error is None


def test_the_song_playing_cannot_be_removed(session, playlist) -> None:
    playing = playlist.add(library_song(FIRST))
    other = playlist.add(library_song(SECOND))
    session.start()

    refused(lambda: session.remove(playing.id), "entry_playing")
    session.remove(other.id)
    refused(lambda: session.remove("unknown"), "unknown_entry")

    assert [entry.id for entry in playlist.entries()] == [playing.id]


def test_the_follower_thread_moves_to_the_results(playlist, packages, player) -> None:
    session = Session(playlist, packages.path, player, poll_s=0.001)
    playlist.add(library_song(FIRST))
    player.positions = [0.5, 1.0, 1.5]
    player.end = True

    session.start()
    deadline = time.monotonic() + 5
    while session.view().state == "playing" and time.monotonic() < deadline:
        time.sleep(0.001)

    assert session.view().state == "results"
    assert session.view().position_s == 1.5


def test_the_follower_thread_outlives_a_failed_read(playlist, packages, player) -> None:
    session = Session(playlist, packages.path, player, poll_s=0.001)
    playlist.add(library_song(FIRST))
    player.failures["position"] = ReaperUnreachable("could not reach REAPER")
    player.positions = [2.5]
    player.end = True

    session.start()
    deadline = time.monotonic() + 5
    while session.view().error is None and time.monotonic() < deadline:
        time.sleep(0.001)
    assert (session.view().state, session.view().error) == (
        "playing",
        "reaper_unreachable",
    )
    del player.failures["position"]
    while session.view().state == "playing" and time.monotonic() < deadline:
        time.sleep(0.001)
    session.close()

    assert (session.view().state, session.view().position_s) == ("results", 2.5)
    assert session.view().error is None


def test_player_calls_never_overlap(playlist, packages, player) -> None:
    """A stop sent while the follower reads the position would overlap it."""
    session = Session(playlist, packages.path, player, poll_s=0.001)
    for _ in range(5):
        playlist.add(library_song(FIRST))
    player.delay_s = 0.002

    for _ in range(5):
        session.start()
        time.sleep(0.03)
        session.stop()
        session.setup_next()
    session.close()

    assert player.names().count("position") >= 5
    assert player.names().count("stop") == 5
    assert player.overlaps == 0


def test_the_fake_player_sees_two_calls_at_once(player) -> None:
    player.delay_s = 0.05
    threads = [threading.Thread(target=player.stop) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert player.overlaps == 1


def ticking(*times: float):
    """A clock that returns times in order, one per call."""
    values = iter(times)
    return lambda: next(values)


def test_start_takes_the_time_of_position_zero_after_play(
    playlist, packages, player
) -> None:
    session = Session(
        playlist, packages.path, player, follow=False, clock=ticking(10.0)
    )
    playlist.add(library_song(FIRST))

    session.start()

    view = session.view()
    assert (view.position_s, view.position_at_s) == (0.0, 10.0)


def test_a_poll_takes_the_time_of_its_read_halfway_through_it(
    playlist, packages, player
) -> None:
    session = Session(
        playlist, packages.path, player, follow=False, clock=ticking(10.0, 20.0, 20.4)
    )
    playlist.add(library_song(FIRST))
    player.positions = [9.5]
    session.start()

    session.poll()

    view = session.view()
    assert view.position_s == 9.5
    assert view.position_at_s == pytest.approx(20.2)


def test_a_failed_poll_keeps_the_time_of_the_last_read(
    playlist, packages, player
) -> None:
    session = Session(
        playlist, packages.path, player, follow=False, clock=ticking(10.0, 20.0, 20.4)
    )
    playlist.add(library_song(FIRST))
    session.start()
    player.failures["position"] = ReaperUnreachable("could not reach REAPER")

    session.poll()

    assert (session.view().position_s, session.view().position_at_s) == (0.0, 10.0)


def test_no_position_time_outside_a_song(session, playlist) -> None:
    playlist.add(library_song(FIRST))
    assert session.view().position_at_s is None
    session.start()
    session.stop()

    session.setup_next()

    assert session.view().position_at_s is None


def test_positions_are_timed_with_the_finest_clock() -> None:
    # On Windows time.monotonic ticks every 15.6 ms.
    default = inspect.signature(Session).parameters["clock"].default

    assert default is time.perf_counter
