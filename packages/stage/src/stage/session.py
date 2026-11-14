"""The session around the current song (docs/PLAN.md section 5.8).

setup (stems to mute, key) -> playing -> results, then the next song either
at once or through its setup. A song ends when REAPER stops on its own
(player.ended(), section 5.4); a stop the Stage asks for is not an end, so
the session itself moves to the results.

The REAPER listener has single slot cmd and ack, so every Player call goes
through one lock: actions and the position polls never overlap.
"""

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from stage import library as lib
from stage.player import ListenerSilent, Player, ReaperError, ReaperUnreachable
from stage.playlist import Entry, Playlist

POLL_S = 0.25
DEFAULT_MUTED = ("lead",)

SessionState = Literal["setup", "playing", "results"]


class SessionError(Exception):
    """An action that could not be done: code for the screen, detail for the logs."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SessionView:
    """What the screen shows of the session.

    entry_id is the song of the session: the first entry in setup, the one
    played in playing and results. error and detail are the last failure,
    cleared by the next action or poll that works.
    """

    state: SessionState
    entry_id: str | None
    muted: tuple[str, ...]
    key: int
    position_s: float | None
    duration_s: float | None
    error: str | None
    detail: str | None


def reaper_code(error: ReaperError) -> str:
    if isinstance(error, ReaperUnreachable):
        return "reaper_unreachable"
    if isinstance(error, ListenerSilent):
        return "listener_silent"
    return "reaper_failed"


class Session:
    """follow=False leaves the position polls to poll(), for tests."""

    def __init__(
        self,
        playlist: Playlist,
        library: Path,
        player: Player,
        *,
        poll_s: float = POLL_S,
        follow: bool = True,
    ) -> None:
        self._playlist = playlist
        self._library = library
        self._player = player
        self._poll_s = poll_s
        self._follow = follow
        # Held for every action and every Player call. Taken before _fields.
        self._lock = threading.Lock()
        self._fields = threading.Lock()
        self._state: SessionState = "setup"
        self._entry_id: str | None = None
        self._muted: tuple[str, ...] = DEFAULT_MUTED
        self._key = 0
        self._position_s: float | None = None
        self._duration_s: float | None = None
        self._error: str | None = None
        self._detail: str | None = None
        # A new song or close() ends the follower of the previous one.
        self._generation = 0

    def view(self) -> SessionView:
        with self._fields:
            entry_id, duration_s = self._entry_id, self._duration_s
            if self._state == "setup":
                first = self._playlist.first()
                entry_id = None if first is None else first.id
                duration_s = None if first is None else first.song.duration_s
            return SessionView(
                self._state,
                entry_id,
                self._muted,
                self._key,
                self._position_s,
                duration_s,
                self._error,
                self._detail,
            )

    def configure(self, muted: list[str], key: int) -> None:
        with self._lock:
            self._require("setup")
            self._set(muted=tuple(sorted(set(muted))), key=key)

    def start(self) -> None:
        with self._lock:
            self._require("setup")
            entry = self._playlist.first()
            self._play(entry, self._package(entry))

    def stop(self) -> None:
        """Always ends in the results; a REAPER error is still raised."""
        with self._lock:
            self._require("playing")
            self._generation += 1
            try:
                self._player.stop()
            except ReaperError as error:
                self._set(state="results")
                self._raise(error)
            self._set(state="results", error=None, detail=None)

    def play_next(self) -> None:
        """Remove the song played and play the next with its mutes, at key 0.

        A refusal (no_song, not_ready) changes nothing.
        """
        with self._lock:
            self._require("results")
            following = self._following()
            if following is None:
                raise SessionError("no_song", "no song after this one in the playlist")
            song = self._package(following)
            self._playlist.remove(self._entry_id)
            self._set(state="setup", entry_id=None, key=0, position_s=None)
            self._play(following, song)

    def setup_next(self) -> None:
        with self._lock:
            self._require("results")
            self._playlist.remove(self._entry_id)
            self._reset()

    def skip(self) -> None:
        """Remove the current song from the setup and set up the next one."""
        with self._lock:
            self._require("setup")
            first = self._playlist.first()
            if first is None:
                raise SessionError("no_song", "the playlist is empty")
            self._playlist.remove(first.id)
            self._reset()

    def remove(self, entry_id: str) -> None:
        with self._lock:
            if self._state == "playing" and entry_id == self._entry_id:
                raise SessionError(
                    "entry_playing", "the song playing cannot be removed; stop it"
                )
            if not self._playlist.remove(entry_id):
                raise SessionError("unknown_entry", f"no entry {entry_id}")

    def poll(self) -> None:
        """Read the position once; moves to the results when the song ended."""
        with self._lock:
            self._poll(self._generation)

    def close(self) -> None:
        """End the follower, if one runs."""
        with self._lock:
            self._generation += 1

    def _package(self, entry: Entry | None) -> lib.Song:
        """The package entry plays, or the refusal: no_song or not_ready."""
        if entry is None:
            raise SessionError("no_song", "the playlist is empty")
        _require_ready(entry)
        song = lib.scan(self._library).get(entry.song.song_id)
        if song is None:
            raise SessionError(
                "not_ready", f"{entry.song.song_id} is no longer in the library"
            )
        return song

    def _play(self, entry: Entry, song: lib.Song) -> None:
        try:
            self._player.load((song.folder / "manifest.json").resolve())
            # Every stem is set, so the mutes of the previous song do not stay.
            for stem in song.manifest.stems:
                self._player.set_mute(stem, stem in self._muted)
            self._player.transpose(self._key)
            self._player.play()
        except ReaperError as error:
            self._raise(error)
        self._generation += 1
        self._set(
            state="playing",
            entry_id=entry.id,
            position_s=0.0,
            duration_s=song.manifest.duration_s,
            error=None,
            detail=None,
        )
        if self._follow:
            threading.Thread(
                target=self._follow_song,
                args=(self._generation,),
                name="stage-session",
                daemon=True,
            ).start()

    def _follow_song(self, generation: int) -> None:
        while True:
            time.sleep(self._poll_s)
            with self._lock:
                if not self._poll(generation):
                    return

    def _poll(self, generation: int) -> bool:
        """Whether the song of generation still plays. The caller holds _lock."""
        if generation != self._generation or self._state != "playing":
            return False
        try:
            position = self._player.position()
            ended = self._player.ended()
        except ReaperError as error:
            self._set(error=reaper_code(error), detail=str(error))
            return True
        if ended:
            self._set(state="results", position_s=position, error=None, detail=None)
            return False
        self._set(position_s=position, error=None, detail=None)
        return True

    def _following(self) -> Entry | None:
        for entry in self._playlist.entries():
            if entry.id != self._entry_id:
                return entry
        return None

    def _reset(self) -> None:
        self._set(
            state="setup",
            entry_id=None,
            muted=DEFAULT_MUTED,
            key=0,
            position_s=None,
            duration_s=None,
            error=None,
            detail=None,
        )

    def _require(self, state: SessionState) -> None:
        if self._state != state:
            raise SessionError(
                "wrong_state", f"the session is in {self._state}, not in {state}"
            )

    def _raise(self, error: ReaperError) -> None:
        code = reaper_code(error)
        self._set(error=code, detail=str(error))
        raise SessionError(code, str(error)) from None

    def _set(self, **fields) -> None:
        with self._fields:
            for name, value in fields.items():
                setattr(self, f"_{name}", value)


def _require_ready(entry: Entry) -> None:
    if entry.state != "ready":
        raise SessionError("not_ready", f"{entry.song.title} is {entry.state}")
