"""The session around the current song (docs/PLAN.md section 5.8).

setup (stems to mute, key) -> playing -> results, then the next song either
at once or through its setup. A song ends when REAPER stops on its own
(player.ended(), section 5.4); a stop the Stage asks for is not an end, so
the session itself moves to the results. A pause keeps the session playing.

The excerpt is a state inside setup, as a pause is inside playing: a short
part of the song plays so the singer can try the key, the mutes and the
volumes. It ends in the setup, never in the results, whoever stops it.

The REAPER listener has single slot cmd and ack, so every Player call goes
through one lock: actions and the position polls never overlap. Removing and
moving entries take it too, so the session's own entry cannot change under
them; the playlist's lock is only ever taken inside it, never around it.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from stage import library as lib
from stage.lyrics import song_lyrics
from stage.player import ListenerSilent, Player, ReaperError, ReaperUnreachable
from stage.playlist import Entry, Playlist

log = logging.getLogger(__name__)

POLL_S = 0.25
DEFAULT_MUTED = ("lead",)
# The excerpt starts this long before the first sung line, and plays this long.
EXCERPT_LEAD_S = 3.0
EXCERPT_S = 20.0
# The template's tracks (docs/PLAN.md section 5.4): the folder of every stem,
# and the mics, which the voice volume sets together.
MUSIC_TRACK = "Music"
VOICE_TRACKS = ("Mic 1", "Mic 2")

SessionState = Literal["setup", "playing", "results"]


class SessionError(Exception):
    """An action that could not be done: code for the screen, detail for the logs."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Volumes:
    """The night's volumes in dB: the Music folder, and the mics together."""

    music_db: float
    voice_db: float


@dataclass(frozen=True)
class SessionView:
    """What the screen shows of the session.

    entry_id is the song of the session: the first entry in setup, the one
    played in playing and results. paused is only ever true while playing,
    previewing only in setup, while its excerpt plays. position_s is None in
    setup but during an excerpt. volumes is None until read from REAPER or
    set. position_at_s is when REAPER reported position_s, in the session's
    clock (time.perf_counter); on a resume, when REAPER's resume returned.
    error and detail are the last failure, cleared by the next action or poll
    that works.
    """

    state: SessionState
    entry_id: str | None
    muted: tuple[str, ...]
    key: int
    position_s: float | None
    duration_s: float | None
    error: str | None
    detail: str | None
    paused: bool = False
    volumes: Volumes | None = None
    position_at_s: float | None = None
    previewing: bool = False


def excerpt_start_s(song: lib.Song) -> float:
    """EXCERPT_LEAD_S before the first sung line, or the start of a song
    without synced lyrics. A stamp without text is no sung line.
    """
    lyrics = song_lyrics(song)
    if lyrics.status != "synced":
        return 0.0
    for line in lyrics.lines:
        if line.text:
            return max(0.0, line.time_s - EXCERPT_LEAD_S)
    return 0.0


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
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._playlist = playlist
        self._library = library
        self._player = player
        self._poll_s = poll_s
        self._follow = follow
        self._clock = clock
        # Held for every action and every Player call. Taken before _fields.
        self._lock = threading.Lock()
        self._fields = threading.Lock()
        self._state: SessionState = "setup"
        self._entry_id: str | None = None
        self._muted: tuple[str, ...] = DEFAULT_MUTED
        self._key = 0
        self._position_s: float | None = None
        self._position_at_s: float | None = None
        self._duration_s: float | None = None
        self._error: str | None = None
        self._detail: str | None = None
        self._paused = False
        self._volumes: Volumes | None = None
        self._previewing = False
        # The excerpt's end, and the stems of the song it loaded.
        self._excerpt_end_s = 0.0
        self._excerpt_stems: tuple[str, ...] = ()
        # A new song, a new excerpt or close() ends the follower of the
        # previous one.
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
                self._paused,
                self._volumes,
                self._position_at_s,
                self._previewing,
            )

    def configure(self, muted: list[str], key: int) -> None:
        """During an excerpt, REAPER takes the settings at once. On a REAPER
        error the settings are kept all the same: start sends them again.
        """
        with self._lock:
            self._require("setup")
            self._set(muted=tuple(sorted(set(muted))), key=key)
            if not self._previewing:
                return
            try:
                for stem in self._excerpt_stems:
                    self._player.set_mute(stem, stem in self._muted)
                self._player.transpose(self._key)
            except ReaperError as error:
                self._raise(error)
            self._set(error=None, detail=None)

    def start(self) -> None:
        with self._lock:
            self._require("setup")
            entry = self._playlist.first()
            song = self._package(entry)
            # Loading the song stops the excerpt in REAPER.
            self._forget_excerpt()
            self._play(entry, song)

    def preview(self) -> None:
        """Play the excerpt of the setup's song with its mutes and key; again
        from its start when one plays already. A REAPER error ends it.
        """
        with self._lock:
            self._require("setup")
            entry = self._playlist.first()
            song = self._package(entry)
            start_s = excerpt_start_s(song)
            self._forget_excerpt()
            try:
                self._player.load((song.folder / "manifest.json").resolve())
                for stem in song.manifest.stems:
                    self._player.set_mute(stem, stem in self._muted)
                self._player.transpose(self._key)
                # play() goes to the start, so the seek comes after it.
                self._player.play()
                self._player.seek(start_s)
            except ReaperError as error:
                self._raise(error)
            self._generation += 1
            self._excerpt_end_s = start_s + EXCERPT_S
            self._excerpt_stems = tuple(song.manifest.stems)
            self._set(
                previewing=True,
                position_s=start_s,
                position_at_s=self._clock(),
                error=None,
                detail=None,
            )
            self._read_volumes_if_unknown()
            self._follow_with(self._poll_excerpt)

    def preview_stop(self) -> None:
        with self._lock:
            if self._state != "setup" or not self._previewing:
                raise SessionError("wrong_state", "no excerpt plays")
            self._stop_excerpt()

    def stop(self) -> None:
        """Always ends in the results; a REAPER error is still raised."""
        with self._lock:
            self._require("playing")
            self._generation += 1
            try:
                self._player.stop()
            except ReaperError as error:
                self._set(state="results", paused=False)
                self._raise(error)
            self._set(state="results", paused=False, error=None, detail=None)

    def pause(self) -> None:
        """Pause REAPER, then read it at once.

        The read shows paused and where REAPER paused in one change, since the
        phone holds that position while paused. When it fails, the error shows
        and the next poll reads the pause.
        """
        with self._lock:
            self._require("playing")
            if self._paused:
                raise SessionError("wrong_state", "the song is paused already")
            try:
                self._player.pause()
            except ReaperError as error:
                self._raise(error)
            self._poll(self._generation)

    def resume(self) -> None:
        """Play on from where the song paused.

        REAPER plays on from position_s, the last read, as of when its resume
        returned: the polls keep reading through a pause.
        """
        with self._lock:
            self._require("playing")
            if not self._paused:
                raise SessionError("wrong_state", "the song is not paused")
            try:
                self._player.resume()
            except ReaperError as error:
                self._raise(error)
            self._set(
                paused=False, position_at_s=self._clock(), error=None, detail=None
            )

    def read_volumes(self) -> None:
        """Read the volumes from REAPER. A failure is raised, not shown."""
        with self._lock:
            try:
                self._read_volumes()
            except ReaperError as error:
                raise SessionError(reaper_code(error), str(error)) from None

    def set_volumes(self, music_db: float, voice_db: float) -> None:
        """In any state. On a REAPER error the volumes shown stay as they were."""
        with self._lock:
            try:
                self._player.set_volume_db(MUSIC_TRACK, music_db)
                for track in VOICE_TRACKS:
                    self._player.set_volume_db(track, voice_db)
            except ReaperError as error:
                self._raise(error)
            self._set(volumes=Volumes(music_db, voice_db), error=None, detail=None)

    def play_next(self) -> None:
        """Remove the song played and play the next with its mutes, at key 0.

        A refusal (no_song, not_ready) changes nothing. A REAPER error comes
        after the song played was removed: the session stays in the setup of
        the next song, with the mutes kept, key 0 and the error.
        """
        with self._lock:
            self._require("results")
            following = self._following()
            if following is None:
                raise SessionError("no_song", "no song after this one in the playlist")
            song = self._package(following)
            self._playlist.remove(self._entry_id)
            self._set(
                state="setup",
                entry_id=None,
                key=0,
                position_s=None,
                position_at_s=None,
            )
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
            self._stop_excerpt()
            self._playlist.remove(first.id)
            self._reset()

    def remove(self, entry_id: str) -> None:
        with self._lock:
            if self._state == "playing" and entry_id == self._entry_id:
                raise SessionError(
                    "entry_playing", "the song playing cannot be removed; stop it"
                )
            first = self._playlist.first()
            if first is not None and entry_id == first.id:
                self._stop_excerpt()
            if not self._playlist.remove(entry_id):
                raise SessionError("unknown_entry", f"no entry {entry_id}")

    def move(self, entry_id: str, by: int) -> None:
        """Shift an entry one place up (-1) or down (1) behind the session's own
        entry, which never moves. A move past either end changes nothing.
        """
        # bool is an int, and True == 1.
        if type(by) is not int or by not in (-1, 1):
            raise SessionError("invalid_request", f"by must be -1 or 1, not {by!r}")
        with self._lock:
            if self._playlist.get(entry_id) is None:
                raise SessionError("unknown_entry", f"no entry {entry_id}")
            own = self.view().entry_id
            if entry_id == own:
                raise SessionError(
                    "entry_current", "the entry of the session cannot move"
                )
            self._playlist.move(entry_id, by, fixed=own)

    def poll(self) -> None:
        """Read the position once; moves to the results when the song ended,
        or ends the excerpt that plays."""
        with self._lock:
            if self._previewing:
                self._poll_excerpt(self._generation)
            else:
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
            position_at_s=self._clock(),
            duration_s=song.manifest.duration_s,
            paused=False,
            error=None,
            detail=None,
        )
        self._read_volumes_if_unknown()
        self._follow_with(self._poll)

    def _read_volumes_if_unknown(self) -> None:
        if self._volumes is None:
            # REAPER may not have answered when stage serve started.
            try:
                self._read_volumes()
            except ReaperError as error:
                log.warning("could not read the volumes from REAPER: %s", error)

    def _follow_with(self, poll: Callable[[int], bool]) -> None:
        """Poll in a thread of its own until poll says the current generation
        is over."""
        if self._follow:
            threading.Thread(
                target=self._follow_loop,
                args=(poll, self._generation),
                name="stage-session",
                daemon=True,
            ).start()

    def _follow_loop(self, poll: Callable[[int], bool], generation: int) -> None:
        while True:
            time.sleep(self._poll_s)
            with self._lock:
                if not poll(generation):
                    return

    def _poll_excerpt(self, generation: int) -> bool:
        """Whether the excerpt of generation still plays. The caller holds _lock.

        It ends at its end, or when REAPER stopped on its own (a song shorter
        than the excerpt); it never moves the session to the results.
        """
        if generation != self._generation or not self._previewing:
            return False
        try:
            asked = self._clock()
            position = self._player.position()
            position_at_s = (asked + self._clock()) / 2
            ended = self._player.ended()
        except ReaperError as error:
            self._set(error=reaper_code(error), detail=str(error))
            return True
        if ended or position >= self._excerpt_end_s:
            self._forget_excerpt()
            if not ended:
                try:
                    self._player.stop()
                except ReaperError as error:
                    self._set(error=reaper_code(error), detail=str(error))
                    return False
            self._set(error=None, detail=None)
            return False
        self._set(
            position_s=position,
            position_at_s=position_at_s,
            error=None,
            detail=None,
        )
        return True

    def _stop_excerpt(self) -> None:
        """Stop the excerpt, if one plays. It ends even when REAPER does not
        stop, and the error is then raised. The caller holds _lock.
        """
        if not self._previewing:
            return
        self._forget_excerpt()
        try:
            self._player.stop()
        except ReaperError as error:
            self._raise(error)
        self._set(error=None, detail=None)

    def _forget_excerpt(self) -> None:
        """End the excerpt and its follower, leaving REAPER as it is."""
        if self._previewing:
            self._generation += 1
            self._set(previewing=False, position_s=None, position_at_s=None)

    def _poll(self, generation: int) -> bool:
        """Whether the song of generation still plays. The caller holds _lock.

        paused follows REAPER, so a pause or a resume made in REAPER shows too.
        """
        if generation != self._generation or self._state != "playing":
            return False
        try:
            asked = self._clock()
            position = self._player.position()
            # REAPER read the position somewhere within the request.
            position_at_s = (asked + self._clock()) / 2
            ended = self._player.ended()
            paused = self._player.paused()
        except ReaperError as error:
            self._set(error=reaper_code(error), detail=str(error))
            return True
        if ended:
            self._set(
                state="results",
                position_s=position,
                position_at_s=position_at_s,
                paused=False,
                error=None,
                detail=None,
            )
            return False
        self._set(
            position_s=position,
            position_at_s=position_at_s,
            paused=paused,
            error=None,
            detail=None,
        )
        return True

    def _read_volumes(self) -> None:
        volumes = Volumes(
            self._player.volume_db(MUSIC_TRACK),
            self._player.volume_db(VOICE_TRACKS[0]),
        )
        self._set(volumes=volumes)

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
            position_at_s=None,
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
