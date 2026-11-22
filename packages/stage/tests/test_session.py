import inspect
import json
import shutil
import threading
import time

import pytest
from stage.player import ListenerSilent, ReaperError, ReaperPlayer, ReaperUnreachable
from stage.playlist import Playlist
from stage.search import Result
from stage.session import Session, SessionError, Volumes

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
    # The volumes are still unknown, so they are read once the song plays.
    assert player.names()[1:] == ["set_mute"] * 7 + [
        "transpose",
        "play",
        "volume_db",
        "volume_db",
    ]
    assert mutes(player) == {stem: stem in ("lead", "backing") for stem in STEMS}
    assert player.calls[8] == ("transpose", -2)
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


# Pause.


def test_pause_pauses_reaper_and_the_song_keeps_playing(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    session.start()

    session.pause()

    assert player.names()[-4:] == ["pause", "position", "ended", "paused"]
    view = session.view()
    assert (view.state, view.paused) == ("playing", True)


def test_pause_shows_paused_and_where_it_paused_at_once(
    session, playlist, player
) -> None:
    """The publisher reads the view without the lock: a paused view with the
    position of the last poll would go out as the phone's held position."""
    playlist.add(library_song(FIRST))
    player.positions = [5.0, 5.4]
    session.start()
    session.poll()
    seen = []

    def look(name: str) -> None:
        if name in ("position", "ended", "paused"):
            view = session.view()
            seen.append((view.paused, view.position_s))

    player.on_call = look

    session.pause()

    assert seen == [(False, 5.0)] * 3
    assert (session.view().paused, session.view().position_s) == (True, 5.4)


def test_a_pause_made_in_reaper_shows_on_the_next_poll(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    session.start()
    player.reaper_paused = True

    session.poll()

    assert session.view().paused is True
    refused(session.pause, "wrong_state")
    session.resume()
    assert player.names().count("resume") == 1
    assert session.view().paused is False


def test_a_resume_made_in_reaper_shows_on_the_next_poll(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    session.start()
    session.pause()
    player.reaper_paused = False

    session.poll()

    assert session.view().paused is False
    refused(session.resume, "wrong_state")
    session.pause()
    assert player.names().count("pause") == 2
    assert session.view().paused is True


def test_pause_reads_where_reaper_paused(playlist, packages, player) -> None:
    session = Session(
        playlist,
        packages.path,
        player,
        follow=False,
        clock=ticking(10.0, 20.0, 20.4, 21.0, 21.2),
    )
    playlist.add(library_song(FIRST))
    player.positions = [9.5, 10.3]
    session.start()
    session.poll()

    session.pause()

    view = session.view()
    assert (view.paused, view.position_s) == (True, 10.3)
    assert view.position_at_s == pytest.approx(21.1)


def test_a_new_session_is_not_paused(session) -> None:
    assert session.view().paused is False


def test_pause_only_while_playing_and_not_paused(session, playlist, player) -> None:
    playlist.add(library_song(FIRST))
    refused(session.pause, "wrong_state")
    session.start()
    session.pause()

    refused(session.pause, "wrong_state")
    session.stop()
    refused(session.pause, "wrong_state")

    assert player.names().count("pause") == 1


def test_resume_plays_on_and_never_from_the_start(playlist, packages, player) -> None:
    session = Session(
        playlist,
        packages.path,
        player,
        follow=False,
        clock=ticking(10.0, 20.0, 20.4, 25.0, 25.2, 31.0),
    )
    playlist.add(library_song(FIRST))
    player.positions = [9.5]
    session.start()
    session.poll()
    session.pause()
    calls = len(player.calls)

    session.resume()

    assert player.names()[calls:] == ["resume"]
    view = session.view()
    assert (view.state, view.paused, view.position_s) == ("playing", False, 9.5)
    # Playback goes on from position_s when REAPER's resume returned.
    assert view.position_at_s == 31.0


def test_resume_only_while_paused(session, playlist, player) -> None:
    playlist.add(library_song(FIRST))
    refused(session.resume, "wrong_state")
    session.start()

    refused(session.resume, "wrong_state")

    assert "resume" not in player.names()


def test_polls_go_on_during_a_pause_without_ending_the_song(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    player.positions = [3.0, 3.0, 3.0]
    session.start()
    session.pause()

    session.poll()
    session.poll()

    view = session.view()
    assert (view.state, view.paused, view.position_s) == ("playing", True, 3.0)
    # One read when it paused, then the two polls.
    assert player.names().count("position") == 3


def test_stop_while_paused_shows_the_results(session, playlist, player) -> None:
    playlist.add(library_song(FIRST))
    session.start()
    session.pause()

    session.stop()

    assert player.calls[-1] == ("stop",)
    view = session.view()
    assert (view.state, view.paused) == ("results", False)


def test_a_song_ended_during_a_pause_shows_the_results(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    # The read of pause, then a poll that sees the song ended (a stop by hand).
    player.positions = [30.0, 30.0]
    player.end = True
    session.start()
    session.pause()
    assert (session.view().state, session.view().paused) == ("playing", True)

    session.poll()

    view = session.view()
    assert (view.state, view.paused) == ("results", False)


def test_a_reaper_error_on_pause_or_resume_changes_nothing(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    session.start()
    player.failures["pause"] = ReaperUnreachable("could not reach REAPER")

    refused(session.pause, "reaper_unreachable")
    assert (session.view().paused, session.view().error) == (
        False,
        "reaper_unreachable",
    )
    del player.failures["pause"]
    session.pause()
    player.failures["resume"] = ListenerSilent("the listener did not answer")
    refused(session.resume, "listener_silent")

    assert session.view().paused is True


def test_the_next_song_starts_unpaused(session, playlist, player) -> None:
    playlist.add(library_song(FIRST))
    playlist.add(library_song(SECOND))
    session.start()
    session.pause()
    session.stop()

    session.play_next()

    assert session.view().paused is False


def test_a_pause_on_reaper_is_not_an_end_of_song(playlist, packages, reaper) -> None:
    reaper.tracks = ["Mic 1", "Mic 2", "Music", *(stem.title() for stem in STEMS)]
    # The reads of poll, pause (its playstate, then where it paused), poll,
    # poll, resume and poll.
    reaper.transport = [
        (1, 1.0),
        (1, 1.1),
        (2, 1.1),
        (2, 1.1),
        (2, 1.1),
        (2, 1.1),
        (1, 1.3),
    ]
    session = Session(
        playlist,
        packages.path,
        ReaperPlayer(http=reaper.http(), poll_s=0),
        follow=False,
    )
    playlist.add(library_song(FIRST))
    session.start()
    session.poll()

    session.pause()
    session.poll()
    session.poll()
    paused = session.view()
    session.resume()
    session.poll()

    assert (paused.state, paused.paused, paused.position_s) == ("playing", True, 1.1)
    view = session.view()
    assert (view.state, view.paused, view.position_s) == ("playing", False, 1.3)
    after_play = reaper.commands[reaper.commands.index("1007") + 1 :]
    assert after_play.count("1008") == 2
    assert "40042" not in after_play
    assert "1007" not in after_play


# Volumes.


def test_volumes_are_unknown_until_read(session) -> None:
    assert session.view().volumes is None


def test_read_volumes_reads_the_music_folder_and_the_first_mic(session, player) -> None:
    player.volumes_db = {"Music": -8.0, "Mic 1": -2.5, "Mic 2": 0.0}

    session.read_volumes()

    assert player.calls == [("volume_db", "Music"), ("volume_db", "Mic 1")]
    assert session.view().volumes == Volumes(music_db=-8.0, voice_db=-2.5)


def test_a_failed_read_of_the_volumes_leaves_them_unknown(session, player) -> None:
    player.failures["volume_db"] = ReaperUnreachable("could not reach REAPER")

    refused(session.read_volumes, "reaper_unreachable")

    view = session.view()
    assert (view.volumes, view.error) == (None, None)


def test_start_reads_the_volumes_only_while_unknown(session, playlist, player) -> None:
    playlist.add(library_song(FIRST))
    playlist.add(library_song(SECOND))
    session.start()
    assert session.view().volumes == Volumes(music_db=-8.0, voice_db=0.0)
    session.stop()
    player.calls.clear()

    session.play_next()

    assert "volume_db" not in player.names()


def test_a_failed_read_of_the_volumes_does_not_fail_the_start(
    session, playlist, player
) -> None:
    playlist.add(library_song(FIRST))
    player.failures["volume_db"] = ReaperError("REAPER has no track named 'Music'")

    session.start()

    view = session.view()
    assert (view.state, view.volumes, view.error) == ("playing", None, None)


def test_set_volumes_sets_the_music_and_both_mics(session, player) -> None:
    session.set_volumes(-12.0, 3.5)

    assert player.calls == [
        ("set_volume_db", "Music", -12.0),
        ("set_volume_db", "Mic 1", 3.5),
        ("set_volume_db", "Mic 2", 3.5),
    ]
    assert session.view().volumes == Volumes(music_db=-12.0, voice_db=3.5)


def test_volumes_are_set_in_any_state(session, playlist) -> None:
    playlist.add(library_song(FIRST))
    session.set_volumes(-1.0, -1.0)
    session.start()
    session.set_volumes(-2.0, -2.0)
    session.pause()
    session.set_volumes(-3.0, -3.0)
    session.stop()

    session.set_volumes(-4.0, -4.0)

    assert session.view().volumes == Volumes(music_db=-4.0, voice_db=-4.0)


def test_a_reaper_error_on_set_volumes_keeps_the_volumes_shown(session, player) -> None:
    session.set_volumes(-6.0, 0.0)
    player.failures["set_volume_db"] = ListenerSilent("the listener did not answer")

    refused(lambda: session.set_volumes(-1.0, -1.0), "listener_silent")

    view = session.view()
    assert (view.volumes, view.error) == (
        Volumes(music_db=-6.0, voice_db=0.0),
        "listener_silent",
    )
    del player.failures["set_volume_db"]
    session.set_volumes(-1.0, -1.0)
    assert session.view().error is None


def test_volumes_belong_to_the_night_not_to_a_song(session, playlist) -> None:
    for _ in range(4):
        playlist.add(library_song(FIRST))
    session.set_volumes(-5.0, 2.0)
    kept = Volumes(music_db=-5.0, voice_db=2.0)

    session.skip()
    assert session.view().volumes == kept
    session.start()
    assert session.view().volumes == kept
    session.stop()
    session.play_next()
    assert session.view().volumes == kept
    session.stop()
    session.setup_next()
    assert session.view().volumes == kept


# Reordering.


def ids(playlist) -> list[str]:
    return [entry.id for entry in playlist.entries()]


def test_move_shifts_an_entry_one_place(session, playlist) -> None:
    first, second, third = (playlist.add(library_song(FIRST)) for _ in range(3))

    session.move(third.id, -1)
    assert ids(playlist) == [first.id, third.id, second.id]
    session.move(third.id, 1)
    assert ids(playlist) == [first.id, second.id, third.id]


def test_the_first_entry_never_moves_in_setup(session, playlist) -> None:
    first, second, third = (playlist.add(library_song(FIRST)) for _ in range(3))

    refused(lambda: session.move(first.id, 1), "entry_current")
    refused(lambda: session.move(first.id, -1), "entry_current")
    session.move(second.id, -1)

    assert ids(playlist) == [first.id, second.id, third.id]


@pytest.mark.parametrize("finished", [False, True])
def test_the_song_played_never_moves(session, playlist, finished) -> None:
    played, second, third = (playlist.add(library_song(FIRST)) for _ in range(3))
    session.start()
    if finished:
        session.stop()

    refused(lambda: session.move(played.id, 1), "entry_current")
    session.move(second.id, -1)
    session.move(second.id, 1)

    assert ids(playlist) == [played.id, third.id, second.id]


def test_moving_the_last_entry_down_changes_nothing(session, playlist) -> None:
    first, second, third = (playlist.add(library_song(FIRST)) for _ in range(3))

    session.move(third.id, 1)

    assert ids(playlist) == [first.id, second.id, third.id]


def test_move_of_an_unknown_entry_is_refused(session, playlist) -> None:
    playlist.add(library_song(FIRST))

    refused(lambda: session.move("unknown", 1), "unknown_entry")


def test_a_late_move_of_the_song_played_and_removed_is_an_unknown_entry(
    session, playlist
) -> None:
    played = playlist.add(library_song(FIRST))
    following = playlist.add(library_song(SECOND))
    session.start()
    session.stop()
    session.remove(played.id)

    # Still the session's own entry, but no longer in the playlist.
    assert session.view().entry_id == played.id
    refused(lambda: session.move(played.id, 1), "unknown_entry")
    assert ids(playlist) == [following.id]


@pytest.mark.parametrize("by", [0, 2, -2, True, 1.0])
def test_move_takes_only_one_place_up_or_down(session, playlist, by) -> None:
    first, second, third = (playlist.add(library_song(FIRST)) for _ in range(3))

    refused(lambda: session.move(third.id, by), "invalid_request")

    assert ids(playlist) == [first.id, second.id, third.id]


def test_moves_never_overlap_a_player_call(playlist, packages, player) -> None:
    """A move waits for the REAPER lock, as a remove does, and takes no other."""
    session = Session(playlist, packages.path, player, poll_s=0.001)
    entries = [playlist.add(library_song(FIRST)) for _ in range(4)]
    player.delay_s = 0.002
    session.start()

    for _ in range(20):
        session.move(entries[2].id, -1)
        session.move(entries[2].id, 1)
    session.close()

    assert ids(playlist) == [entry.id for entry in entries]
    assert player.overlaps == 0


# The excerpt of the pre-performance.

SYNCED = "synced-song--artist-name--0a1b2c"
# A blank first stamp, as LRC files often open with, is no sung line.
SYNCED_LYRICS = "[00:00.00]\r\n[00:12.50] Line one\r\n[00:15.00] Line two\r\n"
# From 3 s before the first sung line, for 20 s.
EXCERPT_START = 9.5
EXCERPT_END = 29.5


@pytest.fixture
def excerpt(playlist, packages):
    """The entry of the setup: a song whose first sung line is at 12.5 s."""
    packages.add(SYNCED, duration_s=200.0, lyrics=SYNCED_LYRICS)
    return playlist.add(library_song(SYNCED))


def test_a_new_session_plays_no_excerpt(session) -> None:
    view = session.view()

    assert (view.previewing, view.position_s, view.position_at_s) == (
        False,
        None,
        None,
    )


def test_preview_plays_the_song_from_3_s_before_its_first_sung_line(
    session, excerpt, packages, player
) -> None:
    session.configure(["lead", "backing"], -2)

    session.preview()

    manifest = (packages.path / SYNCED / "manifest.json").resolve()
    assert player.calls[0] == ("load", manifest)
    # The volumes are still unknown, so they are read once the excerpt plays.
    assert player.names()[1:] == ["set_mute"] * 7 + [
        "transpose",
        "play",
        "seek",
        "volume_db",
        "volume_db",
    ]
    assert mutes(player) == {stem: stem in ("lead", "backing") for stem in STEMS}
    assert ("transpose", -2) in player.calls
    assert ("seek", EXCERPT_START) in player.calls
    view = session.view()
    assert (view.state, view.entry_id, view.previewing, view.position_s) == (
        "setup",
        excerpt.id,
        True,
        EXCERPT_START,
    )
    assert view.volumes == Volumes(music_db=-8.0, voice_db=0.0)


def test_an_excerpt_never_starts_before_the_song(
    session, playlist, packages, player
) -> None:
    packages.add(SYNCED, lyrics="[00:01.00] Line one\r\n")
    playlist.add(library_song(SYNCED))

    session.preview()

    assert ("seek", 0.0) in player.calls


@pytest.mark.parametrize("lyrics", ["none", "unsynced"])
def test_an_excerpt_without_synced_lyrics_starts_at_0(
    session, playlist, packages, player, lyrics
) -> None:
    packages.add(SYNCED, lyrics=SYNCED_LYRICS)
    manifest_path = packages.path / SYNCED / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if lyrics == "none":
        del manifest["lyrics"]
    else:
        manifest["lyrics"]["synced"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    playlist.add(library_song(SYNCED))

    session.preview()

    assert ("seek", 0.0) in player.calls
    assert session.view().position_s == 0.0


def test_preview_takes_the_time_of_its_start_after_the_seek(
    playlist, packages, player, excerpt
) -> None:
    session = Session(
        playlist, packages.path, player, follow=False, clock=ticking(10.0)
    )

    session.preview()

    view = session.view()
    assert (view.position_s, view.position_at_s) == (EXCERPT_START, 10.0)


def test_preview_is_refused_as_start_is(session, playlist, player) -> None:
    refused(session.preview, "no_song")
    playlist.add(new_song())
    refused(session.preview, "not_ready")
    assert player.calls == []

    session.skip()
    playlist.add(library_song(FIRST))
    session.start()

    refused(session.preview, "wrong_state")
    assert "seek" not in player.names()


def test_the_excerpt_ends_20_s_after_its_start_and_stays_in_setup(
    session, excerpt, player
) -> None:
    player.positions = [10.0, EXCERPT_END - 0.1, EXCERPT_END]
    session.preview()

    session.poll()
    session.poll()
    assert (session.view().previewing, "stop" in player.names()) == (True, False)
    session.poll()

    assert player.calls[-1] == ("stop",)
    view = session.view()
    assert (view.state, view.entry_id, view.previewing) == ("setup", excerpt.id, False)
    assert (view.position_s, view.position_at_s) == (None, None)


def test_an_excerpt_reaper_does_not_stop_at_its_end_still_ends(
    session, excerpt, player
) -> None:
    player.positions = [EXCERPT_END]
    session.preview()
    player.failures["stop"] = ReaperUnreachable("could not reach REAPER")

    session.poll()

    view = session.view()
    assert (view.state, view.previewing, view.error) == (
        "setup",
        False,
        "reaper_unreachable",
    )


def test_the_polls_of_an_excerpt_follow_its_position(session, excerpt, player) -> None:
    player.positions = [12.0]
    session.preview()

    session.poll()

    view = session.view()
    assert (view.state, view.previewing, view.position_s) == ("setup", True, 12.0)


def test_an_excerpt_reaper_ends_on_its_own_never_shows_the_results(
    session, excerpt, player
) -> None:
    """A song shorter than the excerpt: REAPER stops at the end of the project."""
    player.positions = [20.0]
    player.end = True
    session.preview()

    session.poll()

    assert "stop" not in player.names()
    view = session.view()
    assert (view.state, view.previewing, view.position_s) == ("setup", False, None)


def test_preview_again_plays_the_excerpt_from_its_start(
    session, excerpt, player
) -> None:
    player.positions = [20.0]
    session.preview()
    session.poll()
    player.calls.clear()

    session.preview()

    assert player.names() == ["load"] + ["set_mute"] * 7 + [
        "transpose",
        "play",
        "seek",
    ]
    view = session.view()
    assert (view.previewing, view.position_s) == (True, EXCERPT_START)


def test_preview_stop_stops_reaper_and_ends_the_excerpt(
    session, excerpt, player
) -> None:
    session.preview()

    session.preview_stop()

    assert player.calls[-1] == ("stop",)
    view = session.view()
    assert (view.state, view.previewing, view.position_s) == ("setup", False, None)
    refused(session.preview_stop, "wrong_state")
    assert player.names().count("stop") == 1


def test_preview_stop_only_during_an_excerpt(session, excerpt, player) -> None:
    refused(session.preview_stop, "wrong_state")
    session.start()

    refused(session.preview_stop, "wrong_state")

    assert "stop" not in player.names()


def test_a_reaper_error_on_preview_stop_still_ends_the_excerpt(
    session, excerpt, player
) -> None:
    session.preview()
    player.failures["stop"] = ReaperUnreachable("could not reach REAPER")

    refused(session.preview_stop, "reaper_unreachable")

    view = session.view()
    assert (view.previewing, view.error) == (False, "reaper_unreachable")


def test_settings_during_an_excerpt_reach_reaper_at_once(
    session, excerpt, player
) -> None:
    session.preview()
    player.calls.clear()

    session.configure(["drums"], 3)

    assert player.names() == ["set_mute"] * 7 + ["transpose"]
    assert mutes(player) == {stem: stem == "drums" for stem in STEMS}
    assert player.calls[-1] == ("transpose", 3)
    view = session.view()
    assert (view.muted, view.key, view.previewing) == (("drums",), 3, True)


def test_settings_outside_an_excerpt_reach_no_reaper(session, excerpt, player) -> None:
    session.configure(["drums"], 3)
    session.preview()
    session.preview_stop()
    player.calls.clear()

    session.configure([], 1)

    assert player.calls == []


def test_a_reaper_error_on_settings_during_an_excerpt_keeps_the_settings(
    session, excerpt, player
) -> None:
    """The settings are the session's: start sends them all again."""
    session.preview()
    player.failures["transpose"] = ListenerSilent("the listener did not answer")

    refused(lambda: session.configure(["drums"], 3), "listener_silent")

    view = session.view()
    assert (view.muted, view.key, view.previewing, view.error) == (
        ("drums",),
        3,
        True,
        "listener_silent",
    )


def test_start_during_an_excerpt_plays_the_song_from_its_start(
    session, excerpt, player
) -> None:
    session.preview()
    player.calls.clear()

    session.start()

    assert player.names() == ["load"] + ["set_mute"] * 7 + ["transpose", "play"]
    view = session.view()
    assert (view.state, view.entry_id, view.previewing, view.position_s) == (
        "playing",
        excerpt.id,
        False,
        0.0,
    )


def test_a_reaper_error_on_start_during_an_excerpt_ends_the_excerpt(
    session, excerpt, player
) -> None:
    session.preview()
    player.failures["load"] = ListenerSilent("the listener did not answer")

    refused(session.start, "listener_silent")

    view = session.view()
    assert (view.state, view.previewing, view.position_s) == ("setup", False, None)


def test_skip_during_an_excerpt_stops_it_first(
    session, playlist, excerpt, player
) -> None:
    following = playlist.add(library_song(SECOND))
    session.preview()
    player.calls.clear()

    session.skip()

    assert player.calls == [("stop",)]
    view = session.view()
    assert (view.state, view.entry_id, view.previewing, view.position_s) == (
        "setup",
        following.id,
        False,
        None,
    )


def test_removing_the_song_of_an_excerpt_stops_it_first(
    session, playlist, excerpt, player
) -> None:
    other = playlist.add(library_song(SECOND))
    session.preview()
    player.calls.clear()

    session.remove(other.id)
    assert (player.calls, session.view().previewing) == ([], True)
    session.remove(excerpt.id)

    assert player.calls == [("stop",)]
    view = session.view()
    assert (view.state, view.entry_id, view.previewing) == ("setup", None, False)


def test_a_reaper_error_on_preview_stays_in_setup_without_an_excerpt(
    session, excerpt, player
) -> None:
    player.failures["seek"] = ReaperError("REAPER failed")

    refused(session.preview, "reaper_failed")

    view = session.view()
    assert (view.state, view.previewing, view.position_s, view.error) == (
        "setup",
        False,
        None,
        "reaper_failed",
    )


def test_a_failed_poll_during_an_excerpt_keeps_it_and_shows_the_error(
    session, excerpt, player
) -> None:
    session.preview()
    player.failures["position"] = ReaperUnreachable("could not reach REAPER")

    session.poll()

    view = session.view()
    assert (view.previewing, view.position_s, view.error) == (
        True,
        EXCERPT_START,
        "reaper_unreachable",
    )
    del player.failures["position"]
    session.poll()
    assert session.view().error is None


def test_the_excerpt_follower_ends_it_alone(
    playlist, packages, player, excerpt
) -> None:
    session = Session(playlist, packages.path, player, poll_s=0.001)
    player.positions = [10.0, 15.0, EXCERPT_END]

    session.preview()
    deadline = time.monotonic() + 5
    while session.view().previewing and time.monotonic() < deadline:
        time.sleep(0.001)
    session.close()

    view = session.view()
    assert (view.state, view.previewing) == ("setup", False)
    assert player.names().count("stop") == 1


def test_a_start_ends_the_follower_of_the_excerpt(
    playlist, packages, player, excerpt
) -> None:
    """Past the excerpt's end, its follower would stop the song started."""
    session = Session(playlist, packages.path, player, poll_s=0.001)
    session.preview()
    player.positions = [EXCERPT_END + 10]

    session.start()
    time.sleep(0.05)
    session.close()

    view = session.view()
    assert (view.state, view.previewing, view.position_s) == (
        "playing",
        False,
        EXCERPT_END + 10,
    )
    assert "stop" not in player.names()


def test_reaper_ending_an_excerpt_is_not_the_end_of_the_song_after_it(
    playlist, packages, reaper, excerpt
) -> None:
    reaper.tracks = ["Mic 1", "Mic 2", "Music", *(stem.title() for stem in STEMS)]
    # The excerpt's polls see REAPER stop on its own; then the song plays.
    reaper.transport = [(1, 9.5), (0, 0.0), (1, 0.5)]
    session = Session(
        playlist,
        packages.path,
        ReaperPlayer(http=reaper.http(), poll_s=0),
        follow=False,
    )
    session.preview()
    session.poll()
    session.poll()
    ended = session.view()

    session.start()
    session.poll()

    assert (ended.state, ended.previewing) == ("setup", False)
    assert session.view().state == "playing"
    steps = reaper.ops()
    excerpt_steps = steps[: steps.index("SET/POS/9.500") + 1]
    assert excerpt_steps[-3:] == ["40042", "1007", "SET/POS/9.500"]
