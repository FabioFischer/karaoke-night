"""The player behind the Stage, and REAPER as one (docs/PLAN.md sections 4 and 5.4).

ReaperPlayer drives REAPER through its built-in web interface. Transport,
tracks, mute and volume go straight to it; load, transpose and ping go to the Lua
listener (reaper/scripts/karaoke_listener.lua) through ExtState: the command is
written to karaoke/cmd and the reply read back from karaoke/ack, matched by id.
The protocol is the top comment of main.js in REAPER's reaper_www_root
folder.
"""

import itertools
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol, Self, runtime_checkable
from urllib.parse import quote

import httpx2

# REAPER's web interface listens on IPv4 only. On Windows "localhost" tries
# IPv6 first and every request then waits about 2 s.
DEFAULT_URL = "http://127.0.0.1:8080"
_SECTION = "karaoke"
_PLAY, _STOP, _GO_TO_START = "1007", "1016", "40042"
# Toggles: pauses a moving playback, resumes a paused one, and starts a paused
# playback when stopped.
_PAUSE = "1008"
# TRANSPORT playstate: 0 stopped, 1 playing, 2 paused, 5 recording, 6 record
# paused.
_STOPPED = 0
_MOVING = (1, 5)
_PAUSED = (2, 6)
# REAPER shows -inf dB below about -150 dB, and JSON has no -inf.
SILENT_DB = -150.0
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\"}
_HTTP_TIMEOUT = httpx2.Timeout(2.0)
_UNREACHABLE = (httpx2.HTTPError, httpx2.InvalidURL)


@runtime_checkable
class Player(Protocol):
    """What the Stage needs from a player. Stems are named as in the manifest."""

    def load(self, manifest: Path) -> None:
        """Stop playback, then replace the loaded song."""

    def play(self) -> None:
        """Play the loaded song from its start."""

    def stop(self) -> None: ...

    def set_mute(self, stem: str, muted: bool) -> None: ...

    def transpose(self, semitones: int) -> None:
        """Absolute, every stem except drums. Sent before play."""

    def position(self) -> float:
        """Seconds from the start of the song."""

    def ended(self) -> bool:
        """Whether the song played to its end, as of the last position()."""

    def pause(self) -> None:
        """Pause where the song is; never resumes it."""

    def resume(self) -> None:
        """Play on from where the song paused; never pauses it, never from the start."""

    def set_volume_db(self, track: str, db: float) -> None:
        """The volume of the track named so, in dB."""

    def volume_db(self, track: str) -> float:
        """The current volume of the track named so, in dB."""


class ReaperError(Exception):
    """REAPER could not be reached, or did not do what it was asked."""


class ReaperUnreachable(ReaperError):
    """REAPER's web interface did not answer."""


class ListenerSilent(ReaperError):
    """The karaoke listener did not acknowledge a command in time."""


class Track(NamedTuple):
    index: int
    name: str
    # A gain: 0 is -inf, 1 is 0 dB.
    volume: float


@dataclass(frozen=True)
class Transport:
    playstate: int
    position_s: float

    @property
    def stopped(self) -> bool:
        return self.playstate == _STOPPED


def song_ended(before: Transport, now: Transport, *, stop_requested: bool) -> bool:
    """The end of song rule: playback stopped without the Stage asking to stop.

    Not yet confirmed against a live REAPER, which stops at the end of the
    project when "Stop/repeat playback at end of project" is on. A stop made by
    hand in REAPER, paused or not, also counts as the end; a pause does not.
    """
    return not before.stopped and now.stopped and not stop_requested


def unescape(value: str) -> str:
    """Decode the \\n, \\t and \\\\ escapes REAPER puts in EXTSTATE replies.

    One pass, left to right: replacing one escape at a time would read the
    encoded backslash in "\\\\n" as the start of a newline.
    """
    out = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value) and value[i + 1] in _ESCAPES:
            out.append(_ESCAPES[value[i + 1]])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def _fields(reply: str, command: str) -> list[list[str]]:
    lines = (line.split("\t") for line in reply.split("\n"))
    return [fields for fields in lines if fields[0] == command]


def parse_tracks(reply: str) -> list[Track]:
    return [
        Track(int(fields[1]), fields[2], float(fields[4]))
        for fields in _fields(reply, "TRACK")
    ]


def db_to_gain(db: float) -> float:
    return 10 ** (db / 20)


def gain_to_db(gain: float) -> float:
    """Rounded to hundredths, as REAPER shows a volume."""
    return round(20 * math.log10(max(gain, db_to_gain(SILENT_DB))), 2)


def volume_command(index: int, db: float) -> str:
    # Absolute and plain: a value starting with + or - is relative in dB, and
    # an "e" in it ends the touch state, so no sign and no exponent.
    return f"SET/TRACK/{index}/VOL/{db_to_gain(db):.6f}"


def track_index_map(tracks: list[Track]) -> dict[str, int]:
    """Lowercase track name to web interface index, as the listener matches
    names. Index 0 is the master; the first of two tracks with one name wins.
    """
    mapping: dict[str, int] = {}
    for track in tracks:
        if track.index != 0:
            mapping.setdefault(track.name.lower(), track.index)
    return mapping


def parse_transport(reply: str) -> Transport | None:
    for fields in _fields(reply, "TRANSPORT"):
        return Transport(int(fields[1]), float(fields[2]))
    return None


def parse_extstate(reply: str, section: str, key: str) -> str | None:
    for line in reply.split("\n"):
        fields = line.split("\t", 3)
        if len(fields) == 4 and fields[:3] == ["EXTSTATE", section, key]:
            return unescape(fields[3])
    return None


def encode_command(command: dict) -> str:
    # One request carries several commands separated by ";", and "/" splits
    # the path, so every special character of the value is percent-encoded.
    value = quote(json.dumps(command, separators=(",", ":")), safe="")
    return f"SET/EXTSTATE/{_SECTION}/cmd/{value}"


class ReaperPlayer:
    """A Player on REAPER, through its web interface and the karaoke listener.

    connect() must come first. http is the client to send through; without
    one, the player makes its own and closes it on close().
    """

    def __init__(
        self,
        http: httpx2.Client | None = None,
        *,
        ack_timeout_s: float = 5.0,
        poll_s: float = 0.01,
    ) -> None:
        self.base_url = DEFAULT_URL
        self._owned = http is None
        self._http = httpx2.Client(timeout=_HTTP_TIMEOUT) if http is None else http
        self._ack_timeout_s = ack_timeout_s
        self._poll_s = poll_s
        # Time based, so a new run never takes an ack an earlier run left.
        self._ids = itertools.count(int(time.time() * 1000) % 1_000_000_000)
        self._tracks: list[Track] = []
        self._last: Transport | None = None
        self._stop_requested = False
        self._ended = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def connect(self) -> None:
        """Read the track list, by which stems are found."""
        self._tracks = parse_tracks(self._request("TRACK"))

    def load(self, manifest: Path) -> None:
        # Stems loaded under a moving cursor would play at once, with the
        # previous song's mutes and key.
        self.stop()
        self._command("load", manifest=str(manifest))
        # The listener creates the stem tracks the project lacks.
        self.connect()

    def play(self) -> None:
        self._last = None
        self._stop_requested = False
        self._ended = False
        self._request(_GO_TO_START)
        self._request(_PLAY)

    def stop(self) -> None:
        self._stop_requested = True
        self._request(_STOP)

    def set_mute(self, stem: str, muted: bool) -> None:
        self._request(f"SET/TRACK/{self._index(stem)}/MUTE/{int(muted)}")

    def pause(self) -> None:
        # The pause action toggles, so it is sent only while playback moves.
        if self._transport().playstate in _MOVING:
            self._request(_PAUSE)

    def resume(self) -> None:
        # Never play(): it goes to the start first.
        if self._transport().playstate in _PAUSED:
            self._request(_PAUSE)

    def set_volume_db(self, track: str, db: float) -> None:
        # Volumes can be set before any load, which reads the tracks.
        if track.lower() not in track_index_map(self._tracks):
            self.connect()
        self._request(volume_command(self._index(track), db))

    def volume_db(self, track: str) -> float:
        self.connect()
        index = self._index(track)
        return gain_to_db(next(t.volume for t in self._tracks if t.index == index))

    def transpose(self, semitones: int) -> None:
        self._command("transpose", semitones=semitones)

    def ping(self) -> None:
        self._command("ping")

    def position(self) -> float:
        now = self._transport()
        if self._last is not None and song_ended(
            self._last, now, stop_requested=self._stop_requested
        ):
            self._ended = True
        self._last = now
        return now.position_s

    def ended(self) -> bool:
        return self._ended

    def _transport(self) -> Transport:
        """Read the transport; only position() follows it for the end of song."""
        now = parse_transport(self._request("TRANSPORT"))
        if now is None:
            raise ReaperError("REAPER's web interface sent no TRANSPORT line")
        return now

    def _index(self, name: str) -> int:
        """The index of the track named so, as of the last track list read."""
        index = track_index_map(self._tracks).get(name.lower())
        if index is None:
            names = ", ".join(track.name for track in self._tracks if track.index)
            raise ReaperError(
                f"REAPER has no track named {name!r}; its tracks are: {names}"
            )
        return index

    def _command(self, op: str, **fields) -> dict:
        """Send a listener command and return its ack, once one has its id.

        cmd and ack are single slots, so this returns only after the ack.
        """
        command = {"id": next(self._ids), "op": op, **fields}
        self._request(encode_command(command))
        deadline = time.monotonic() + self._ack_timeout_s
        while True:
            reply = self._request(f"GET/EXTSTATE/{_SECTION}/ack")
            raw = parse_extstate(reply, _SECTION, "ack")
            ack = json.loads(raw) if raw else None
            if isinstance(ack, dict) and ack.get("id") == command["id"]:
                break
            if time.monotonic() >= deadline:
                raise ListenerSilent(
                    "the karaoke listener in REAPER did not answer the "
                    f"{op} command within {self._ack_timeout_s:g} s; restart it: "
                    "run reaper/scripts/karaoke_listener.lua from REAPER's "
                    "action list, or restart REAPER"
                )
            time.sleep(self._poll_s)
        if not ack.get("ok"):
            raise ReaperError(
                f"the karaoke listener in REAPER could not {op}: {ack.get('error')}"
            )
        return ack

    def _request(self, commands: str) -> str:
        try:
            response = self._http.get(f"{self.base_url}/_/{commands}")
        except _UNREACHABLE as error:
            raise ReaperUnreachable(
                f"could not reach REAPER's web interface at {self.base_url} "
                f"({error}); is REAPER open, with the web interface on port 8080 "
                "(reaper/README.md)?"
            ) from None
        if not response.is_success:
            raise ReaperError(
                f"REAPER's web interface answered {response.status_code} to {commands}"
            )
        return response.text
