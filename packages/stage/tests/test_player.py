import json
import time
from pathlib import Path
from urllib.parse import unquote

import httpx2
import pytest
from stage.player import (
    ListenerSilent,
    Player,
    ReaperError,
    ReaperPlayer,
    ReaperUnreachable,
    Track,
    Transport,
    encode_command,
    parse_extstate,
    parse_tracks,
    parse_transport,
    song_ended,
    track_index_map,
    unescape,
)

TRACK_REPLY = (
    "TRACK\t0\tMASTER\t0\t1.000000\t0.000000\t-1500\t-1500\t1.000000\t0\t0\t0\t1\t0\n"
    "TRACK\t1\tMic 1\t192\t1.000000\t0.000000\t-1500\t-1500\t1.000000\t0\t0\t0\t1\t0\n"
    "TRACK\t2\tLead\t8\t1.000000\t0.000000\t-1500\t-1500\t1.000000\t0\t0\t0\t1\t0\n"
    "TRACK\t3\tdrums\t0\t1.000000\t0.000000\t-1500\t-1500\t1.000000\t0\t0\t0\t1\t0\n"
)
PLAYING, PAUSED, STOPPED = 1, 2, 0
RECORDING, RECORD_PAUSED = 5, 6
MANIFEST = Path("C:/Songs/a;b/song a/manifest.json")


def ack_of(command: dict, **fields) -> str:
    return json.dumps({"id": command["id"], "op": command["op"], **fields})


def connected(reaper, **options) -> ReaperPlayer:
    player = ReaperPlayer(http=reaper.http(), poll_s=0, **options)
    player.connect()
    return player


# Parsing and encoding, ported from spike S3.


def test_parse_tracks_reads_every_track_line() -> None:
    assert parse_tracks(TRACK_REPLY) == [
        Track(0, "MASTER", 1.0),
        Track(1, "Mic 1", 1.0),
        Track(2, "Lead", 1.0),
        Track(3, "drums", 1.0),
    ]


def test_parse_tracks_reads_the_volume_as_a_gain() -> None:
    reply = "TRACK\t1\tMusic\t1\t0.398107\t0.000000\t-1500\t-1500\t1\t0\t0\t0\t1\t0\n"

    assert parse_tracks(reply) == [Track(1, "Music", 0.398107)]


def test_parse_tracks_ignores_other_lines() -> None:
    reply = "TRANSPORT\t0\t0.000000\t0\t0:00.000\t1.1.00\n" + TRACK_REPLY + "\n"

    assert len(parse_tracks(reply)) == 4


def test_track_index_map_is_lowercase_and_skips_the_master() -> None:
    assert track_index_map(parse_tracks(TRACK_REPLY)) == {
        "mic 1": 1,
        "lead": 2,
        "drums": 3,
    }


def test_track_index_map_keeps_the_first_of_two_tracks_with_one_name() -> None:
    reply = TRACK_REPLY + "TRACK\t4\tLEAD\t0\t1\t0\t0\t0\t1\t0\t0\t0\t1\t0\n"

    assert track_index_map(parse_tracks(reply))["lead"] == 2


def test_parse_transport_reads_playstate_and_position() -> None:
    reply = "TRANSPORT\t1\t12.345678\t0\t0:12.345\t7.1.00\n"

    assert parse_transport(reply) == Transport(playstate=1, position_s=12.345678)


def test_parse_transport_without_a_transport_line_is_none() -> None:
    assert parse_transport(TRACK_REPLY) is None


@pytest.mark.parametrize(
    ("playstate", "stopped"),
    [(0, True), (1, False), (2, False), (5, False), (6, False)],
)
def test_transport_is_stopped_only_on_playstate_0(playstate, stopped) -> None:
    assert Transport(playstate, 0.0).stopped is stopped


def test_unescape_decodes_newline_tab_and_backslash() -> None:
    assert unescape(r"a\nb\tc\\d") == "a\nb\tc\\d"


def test_unescape_reads_an_encoded_backslash_before_n_as_a_backslash() -> None:
    # The JSON text {"e":"C:\\new"} arrives with each backslash doubled.
    wire = r'{"e":"C:\\\\new"}'

    assert unescape(wire) == r'{"e":"C:\\new"}'
    assert json.loads(unescape(wire))["e"] == "C:\\new"


def test_unescape_keeps_an_unknown_escape() -> None:
    assert unescape(r"a\xb") == r"a\xb"


def test_parse_extstate_returns_the_decoded_value() -> None:
    ack = {"id": 3, "op": "load", "ok": False, "error": "C:\\Songs\\x\nline 2"}
    value = json.dumps(ack).replace("\\", "\\\\")
    reply = f"EXTSTATE\tkaraoke\tack\t{value}\n"

    assert json.loads(parse_extstate(reply, "karaoke", "ack")) == ack


def test_parse_extstate_of_an_empty_value() -> None:
    assert parse_extstate("EXTSTATE\tkaraoke\tack\t\n", "karaoke", "ack") == ""


def test_parse_extstate_without_the_line_is_none() -> None:
    assert parse_extstate(TRACK_REPLY, "karaoke", "ack") is None


def test_parse_extstate_ignores_other_keys() -> None:
    reply = "EXTSTATE\tkaraoke\tcmd\tx\nEXTSTATE\tkaraoke\tack\ty\n"

    assert parse_extstate(reply, "karaoke", "ack") == "y"


COMMAND = {"id": 42, "op": "load", "manifest": "C:\\Songs\\a;b/song a\\manifest.json"}
PREFIX = "SET/EXTSTATE/karaoke/cmd/"


def test_encode_command_round_trips() -> None:
    request = encode_command(COMMAND)

    assert request.startswith(PREFIX)
    assert json.loads(unquote(request[len(PREFIX) :])) == COMMAND


def test_encode_command_leaves_no_separator_or_special_character() -> None:
    value = encode_command(COMMAND)[len(PREFIX) :]

    for char in ';/\\ "{}:':
        assert char not in value
    assert "%3B" in value
    assert "%2F" in value
    assert "%5C" in value


def test_encode_command_of_a_small_command() -> None:
    assert encode_command({"id": 1, "op": "ping"}) == (
        "SET/EXTSTATE/karaoke/cmd/%7B%22id%22%3A1%2C%22op%22%3A%22ping%22%7D"
    )


# End of song.


def test_song_ended_when_playback_stops_on_its_own() -> None:
    assert song_ended(
        Transport(PLAYING, 244.9), Transport(STOPPED, 245.3), stop_requested=False
    )


def test_song_not_ended_when_the_stop_was_asked_for() -> None:
    assert not song_ended(
        Transport(PLAYING, 12.0), Transport(STOPPED, 12.0), stop_requested=True
    )


def test_song_not_ended_while_it_plays() -> None:
    assert not song_ended(
        Transport(PLAYING, 1.0), Transport(PLAYING, 1.2), stop_requested=False
    )


def test_song_not_ended_before_playback_started() -> None:
    assert not song_ended(
        Transport(STOPPED, 0.0), Transport(STOPPED, 0.0), stop_requested=False
    )


@pytest.mark.parametrize("paused", [PAUSED, RECORD_PAUSED])
def test_song_not_ended_on_a_pause_from_reaper(paused) -> None:
    assert not song_ended(
        Transport(PLAYING, 30.0), Transport(paused, 30.0), stop_requested=False
    )


def test_song_not_ended_when_a_pause_resumes() -> None:
    assert not song_ended(
        Transport(PAUSED, 30.0), Transport(PLAYING, 30.1), stop_requested=False
    )


def test_song_ended_on_a_stop_by_hand_during_a_pause() -> None:
    assert song_ended(
        Transport(PAUSED, 30.0), Transport(STOPPED, 30.0), stop_requested=False
    )


# ReaperPlayer against the fake web interface.


def test_reaper_player_is_a_player(reaper) -> None:
    assert isinstance(ReaperPlayer(http=reaper.http()), Player)


def test_connect_reads_the_tracks(reaper) -> None:
    connected(reaper)

    assert reaper.commands == ["TRACK"]


def test_set_mute_finds_the_track_by_name_whatever_the_case(reaper) -> None:
    reaper.tracks = ["Mic 1", "LEAD", "backing"]
    player = connected(reaper)

    player.set_mute("Lead", True)
    player.set_mute("BACKING", False)

    assert reaper.commands[1:] == ["SET/TRACK/2/MUTE/1", "SET/TRACK/3/MUTE/0"]


def test_set_mute_of_a_missing_track_names_the_tracks(reaper) -> None:
    reaper.tracks = ["Mic 1", "Lead"]
    player = connected(reaper)

    with pytest.raises(ReaperError) as error:
        player.set_mute("drums", True)

    assert str(error.value) == (
        "REAPER has no track named 'drums'; its tracks are: Mic 1, Lead"
    )


def test_load_stops_playback_before_the_listener_loads(reaper) -> None:
    player = connected(reaper)

    player.load(MANIFEST)

    assert reaper.ops() == ["1016", "load"]


def test_load_sends_the_manifest_path_and_reads_the_tracks_again(reaper) -> None:
    player = connected(reaper)
    reaper.tracks = [*reaper.tracks, "Extra"]

    player.load(MANIFEST)
    player.set_mute("extra", True)

    (command,) = reaper.sent
    assert command["op"] == "load"
    assert command["manifest"] == str(MANIFEST)
    assert reaper.commands[-2:] == ["TRACK", "SET/TRACK/10/MUTE/1"]


def test_commands_get_a_new_id_each(reaper) -> None:
    player = connected(reaper)

    player.ping()
    player.ping()

    first, second = reaper.sent
    assert first["op"] == second["op"] == "ping"
    assert first["id"] != second["id"]


def test_transpose_sends_the_semitones(reaper) -> None:
    player = connected(reaper)

    player.transpose(-3)

    assert [(c["op"], c["semitones"]) for c in reaper.sent] == [("transpose", -3)]


def test_a_stale_ack_and_empty_polls_are_skipped(reaper) -> None:
    reaper.acks = lambda command: [
        ack_of({**command, "id": command["id"] - 1}, ok=False, error="stale"),
        "",
        ack_of(command, ok=True, ms=12.5),
    ]
    player = connected(reaper)

    player.load(MANIFEST)

    polls = [c for c in reaper.commands if c == "GET/EXTSTATE/karaoke/ack"]
    assert len(polls) == 3


def test_a_new_player_skips_the_ack_an_earlier_run_left(reaper, monkeypatch) -> None:
    earlier = connected(reaper)
    earlier.ping()
    (left,) = reaper.sent
    # The earlier run's ack stays in the slot until the listener answers.
    stale = ack_of(left, ok=False, error="left by an earlier run")
    reaper.acks = lambda command: [stale, stale, ack_of(command, ok=True)]
    started = time.time()
    monkeypatch.setattr(time, "time", lambda: started + 60.0)
    player = connected(reaper)

    player.ping()

    polls = [c for c in reaper.commands if c == "GET/EXTSTATE/karaoke/ack"]
    assert len(polls) == 1 + 3


def test_no_ack_with_the_right_id_is_a_timeout_that_says_to_restart(reaper) -> None:
    reaper.acks = lambda command: [ack_of({**command, "id": command["id"] - 1})]
    player = connected(reaper, ack_timeout_s=0.05)

    with pytest.raises(ReaperError) as error:
        player.load(MANIFEST)

    assert str(error.value) == (
        "the karaoke listener in REAPER did not answer the load command within "
        "0.05 s; restart it: run reaper/scripts/karaoke_listener.lua from REAPER's "
        "action list, or restart REAPER"
    )


def test_a_failed_command_carries_the_listener_error(reaper) -> None:
    reaper.acks = lambda command: [
        ack_of(command, ok=False, error="cannot open C:\\Songs\\x\nline 2")
    ]
    player = connected(reaper)

    with pytest.raises(ReaperError) as error:
        player.load(MANIFEST)

    assert str(error.value) == (
        "the karaoke listener in REAPER could not load: cannot open C:\\Songs\\x\n"
        "line 2"
    )


def test_play_goes_to_the_start_then_plays(reaper) -> None:
    player = connected(reaper)

    player.play()

    assert reaper.commands[1:] == ["40042", "1007"]


def test_stop_sends_the_stop_action(reaper) -> None:
    player = connected(reaper)

    player.stop()

    assert reaper.commands[1:] == ["1016"]


def test_position_reads_the_transport(reaper) -> None:
    reaper.transport = [(PLAYING, 12.5)]
    player = connected(reaper)

    assert player.position() == 12.5


def test_ended_after_playback_stops_on_its_own(reaper) -> None:
    reaper.transport = [(PLAYING, 0.0), (PLAYING, 1.0), (STOPPED, 2.0), (STOPPED, 0)]
    player = connected(reaper)
    player.play()

    seen = []
    for _ in range(4):
        player.position()
        seen.append(player.ended())

    assert seen == [False, False, True, True]


def test_not_ended_after_a_requested_stop(reaper) -> None:
    reaper.transport = [(PLAYING, 0.0), (STOPPED, 0.5)]
    player = connected(reaper)
    player.play()
    player.position()

    player.stop()
    player.position()

    assert not player.ended()


def test_not_ended_while_reaper_still_shows_the_previous_stop(reaper) -> None:
    reaper.transport = [(STOPPED, 3.0), (PLAYING, 0.0), (PLAYING, 0.5)]
    player = connected(reaper)
    player.play()

    for _ in range(3):
        player.position()

    assert not player.ended()


def test_play_again_clears_the_end(reaper) -> None:
    reaper.transport = [(PLAYING, 0.0), (STOPPED, 1.0), (STOPPED, 1.0)]
    player = connected(reaper)
    player.play()
    player.position()
    player.position()
    assert player.ended()

    player.play()
    player.position()

    assert not player.ended()


def test_a_pause_and_its_resume_keep_the_song_going(reaper) -> None:
    reaper.transport = [
        (PLAYING, 0.0),
        (PAUSED, 1.0),
        (PAUSED, 1.0),
        (PLAYING, 1.2),
        (STOPPED, 2.0),
    ]
    player = connected(reaper)
    player.play()

    seen = []
    for _ in range(5):
        player.position()
        seen.append(player.ended())

    assert seen == [False, False, False, False, True]


def test_play_again_forgets_the_transport_of_the_previous_song(reaper) -> None:
    # REAPER can still show the stop for a moment after 1007.
    reaper.transport = [(PLAYING, 10.0), (STOPPED, 0.0), (PLAYING, 0.1)]
    player = connected(reaper)
    player.play()
    player.position()
    player.stop()

    player.play()
    player.position()

    assert not player.ended()


@pytest.mark.parametrize("moving", [PLAYING, RECORDING])
def test_pause_sends_the_pause_action_while_playback_moves(reaper, moving) -> None:
    reaper.transport = [(moving, 30.0)]
    player = connected(reaper)

    player.pause()

    assert reaper.commands[1:] == ["TRANSPORT", "1008"]


@pytest.mark.parametrize("still", [PAUSED, RECORD_PAUSED, STOPPED])
def test_pause_never_resumes_or_starts_playback(reaper, still) -> None:
    # 1008 toggles: A6 saw a second press resume. What it does while stopped
    # is not verified, so it is not sent then either.
    reaper.transport = [(still, 30.0)]
    player = connected(reaper)

    player.pause()

    assert reaper.commands[1:] == ["TRANSPORT"]


@pytest.mark.parametrize("paused", [PAUSED, RECORD_PAUSED])
def test_resume_sends_the_pause_action_while_paused(reaper, paused) -> None:
    reaper.transport = [(paused, 30.0)]
    player = connected(reaper)

    player.resume()

    assert reaper.commands[1:] == ["TRANSPORT", "1008"]


@pytest.mark.parametrize("not_paused", [PLAYING, RECORDING, STOPPED])
def test_resume_never_pauses_or_goes_to_the_start(reaper, not_paused) -> None:
    reaper.transport = [(not_paused, 30.0)]
    player = connected(reaper)

    player.resume()

    assert reaper.commands[1:] == ["TRANSPORT"]


def test_a_pause_and_a_resume_are_not_an_end_of_song(reaper) -> None:
    # The reads of position(), pause(), position(), resume(), position().
    reaper.transport = [
        (PLAYING, 0.5),
        (PLAYING, 1.0),
        (PAUSED, 1.0),
        (PAUSED, 1.0),
        (PLAYING, 1.1),
    ]
    player = connected(reaper)
    player.play()
    player.position()

    seen = []
    player.pause()
    player.position()
    seen.append(player.ended())
    player.resume()
    player.position()
    seen.append(player.ended())

    assert seen == [False, False]
    assert reaper.commands[3:] == [
        "TRANSPORT",
        "TRANSPORT",
        "1008",
        "TRANSPORT",
        "TRANSPORT",
        "1008",
        "TRANSPORT",
    ]


def test_a_stop_by_hand_during_a_pause_still_ends_the_song(reaper) -> None:
    reaper.transport = [(PLAYING, 1.0), (PAUSED, 1.0), (STOPPED, 1.0)]
    player = connected(reaper)
    player.play()
    player.position()
    player.pause()

    player.position()

    assert player.ended()


@pytest.mark.parametrize(
    ("playstate", "paused"),
    [(PLAYING, False), (PAUSED, True), (RECORDING, False), (RECORD_PAUSED, True)],
)
def test_paused_follows_the_playstate_of_the_last_read(
    reaper, playstate, paused
) -> None:
    # A pause or a resume made by hand in REAPER shows too.
    reaper.transport = [(playstate, 3.0)]
    player = connected(reaper)
    player.play()

    player.position()

    assert player.paused() is paused


def test_not_paused_before_a_read_or_after_a_new_play(reaper) -> None:
    reaper.transport = [(PAUSED, 3.0)]
    player = connected(reaper)
    assert player.paused() is False
    player.position()
    assert player.paused() is True

    player.play()

    assert player.paused() is False


def test_pause_and_resume_do_not_change_what_paused_says(reaper) -> None:
    # Only position() reads for the session, as it does for the end of song.
    reaper.transport = [(PLAYING, 1.0), (PLAYING, 1.0)]
    player = connected(reaper)
    player.position()

    player.pause()

    assert player.paused() is False


@pytest.mark.parametrize(
    ("db", "gain"),
    [(0.0, "1.000000"), (-8.0, "0.398107"), (6.0, "1.995262"), (-30.0, "0.031623")],
)
def test_set_volume_sends_the_absolute_gain_of_the_db(reaper, db, gain) -> None:
    reaper.tracks = ["Mic 1", "Mic 2", "Music"]
    player = connected(reaper)

    player.set_volume_db("music", db)

    assert reaper.commands[1:] == [f"SET/TRACK/3/VOL/{gain}"]


def test_a_volume_far_below_is_never_a_relative_or_exponent_value(reaper) -> None:
    # main.js: a value starting with + or - is relative in dB, and an e near
    # the end ends the touch state. 1e-08 is neither a plain absolute value
    # nor free of an e.
    reaper.tracks = ["Music"]
    player = connected(reaper)

    player.set_volume_db("Music", -160.0)

    assert reaper.commands[1:] == ["SET/TRACK/1/VOL/0.000000"]


@pytest.mark.parametrize(
    ("seconds", "value"),
    [(0.0, "0.000"), (9.5, "9.500"), (125.25, "125.250"), (1e-7, "0.000")],
)
def test_seek_sets_the_position_in_seconds_as_a_plain_decimal(
    reaper, seconds, value
) -> None:
    player = connected(reaper)

    player.seek(seconds)

    assert reaper.commands[1:] == [f"SET/POS/{value}"]


def test_a_seek_while_playing_is_not_an_end_of_song(reaper) -> None:
    reaper.transport = [(PLAYING, 0.0), (PLAYING, 9.5), (PLAYING, 9.75)]
    player = connected(reaper)
    player.play()
    player.position()

    player.seek(9.5)
    player.position()
    player.position()

    assert not player.ended()


def test_set_volume_reads_the_tracks_again_when_it_does_not_know_the_track(
    reaper,
) -> None:
    player = ReaperPlayer(http=reaper.http(), poll_s=0)
    reaper.tracks = ["Mic 1", "Music"]

    player.set_volume_db("Music", 0.0)

    assert reaper.commands == ["TRACK", "SET/TRACK/2/VOL/1.000000"]


def test_set_volume_of_a_missing_track_names_the_tracks(reaper) -> None:
    reaper.tracks = ["Mic 1", "Lead"]
    player = connected(reaper)

    with pytest.raises(ReaperError) as error:
        player.set_volume_db("Music", 0.0)

    assert str(error.value) == (
        "REAPER has no track named 'Music'; its tracks are: Mic 1, Lead"
    )


@pytest.mark.parametrize(
    ("gain", "db"),
    [(1.0, 0.0), (0.398107, -8.0), (1.995262, 6.0), (0.031623, -30.0), (0.0, -150.0)],
)
def test_volume_reads_the_gain_of_the_track_list_in_db(reaper, gain, db) -> None:
    reaper.tracks = ["Mic 1", "Music"]
    reaper.volumes = {2: gain}
    player = ReaperPlayer(http=reaper.http(), poll_s=0)

    assert player.volume_db("MUSIC") == db
    assert reaper.commands == ["TRACK"]


def test_volume_of_a_missing_track_names_the_tracks(reaper) -> None:
    reaper.tracks = ["Mic 1"]
    player = ReaperPlayer(http=reaper.http(), poll_s=0)

    with pytest.raises(ReaperError) as error:
        player.volume_db("Music")

    assert (
        str(error.value) == "REAPER has no track named 'Music'; its tracks are: Mic 1"
    )


def test_an_unreachable_reaper_says_where_it_was_looked_for() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    player = ReaperPlayer(http=httpx2.Client(transport=httpx2.MockTransport(refuse)))

    with pytest.raises(ReaperError) as error:
        player.connect()

    assert str(error.value) == (
        "could not reach REAPER's web interface at http://127.0.0.1:8080 "
        "(connection refused); is REAPER open, with the web interface on port "
        "8080 (reaper/README.md)?"
    )


def test_an_error_answer_from_reaper_is_an_error() -> None:
    def deny(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404)

    player = ReaperPlayer(http=httpx2.Client(transport=httpx2.MockTransport(deny)))

    with pytest.raises(ReaperError) as error:
        player.connect()

    assert str(error.value) == "REAPER's web interface answered 404 to TRACK"


def test_the_default_address_is_ipv4_loopback() -> None:
    assert ReaperPlayer().base_url == "http://127.0.0.1:8080"


def test_an_unreachable_reaper_and_a_silent_listener_are_told_apart(reaper) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    unreachable = ReaperPlayer(
        http=httpx2.Client(transport=httpx2.MockTransport(refuse))
    )
    reaper.acks = lambda command: [""]
    silent = connected(reaper, ack_timeout_s=0.05)

    with pytest.raises(ReaperUnreachable):
        unreachable.connect()
    with pytest.raises(ListenerSilent):
        silent.ping()
