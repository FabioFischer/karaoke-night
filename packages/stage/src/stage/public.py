"""The Stage side of public access (docs/PLAN.md section 5.9).

The Publisher writes the snapshot of the playlist and the session, and the
library listing, to the public store. The RequestReader takes the requests the
public page leaves there and applies them as the Stage API would. Both run on
threads of their own, so a store that is slow or down never touches playback
or the local screens.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from stage import library as lib
from stage.lyrics import song_lyrics
from stage.playlist import NotAddable, Playlist
from stage.search import Found, Result, library_results
from stage.server import AddRequest, entry_json
from stage.session import Session, SessionError, SessionView
from stage.store import Store, StoreError

log = logging.getLogger(__name__)

SNAPSHOT_KEY = "karaoke:snapshot"
LIBRARY_KEY = "karaoke:library"
REQUESTS_KEY = "karaoke:requests"
ANSWER_PREFIX = "karaoke:answer:"
ANSWER_EXPIRY_S = 120
SNAPSHOT_VERSION = 1

# How often the publisher looks for a change: a change waits at most this long.
PUBLISH_TICK_S = 0.5
# An unchanged snapshot is published again after this, with a new anchor.
REFRESH_S = 3.0
# The library is scanned this often and published when it changed.
LIBRARY_CHECK_S = 3.0
CLOCK_REFRESH_S = 60.0
READ_TICK_S = 1.0
# Searches run here, off the reader, so an add or a remove never waits on one.
SEARCH_WORKERS = 2
# How much of a request that cannot be read goes into the log.
LOGGED_CHARS = 200

INVALID = {"ok": False, "error": "invalid_request"}


class Publisher:
    """Publishes the snapshot and the library; call cycle() or start().

    Times are in the store's clock: the offset from clock (time.monotonic, as
    the session's) is learned from Redis TIME, and nothing is published
    before it is known.
    """

    def __init__(
        self,
        store: Store,
        library: Path,
        playlist: Playlist,
        session: Session,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._library = library
        self._playlist = playlist
        self._session = session
        self._clock = clock
        self._offset_ms: float | None = None
        self._offset_at = 0.0
        self._published: dict | None = None
        self._published_at = 0.0
        self._songs: list[dict] | None = None
        self._library_checked_at: float | None = None
        self._lyrics: tuple[str, dict | None] | None = None
        self._stopping = threading.Event()

    def cycle(self) -> None:
        try:
            self._learn_clock()
            self._publish_snapshot()
            self._publish_library()
        except StoreError:
            # The store logged it; the next cycle tries again.
            return

    def start(self) -> None:
        threading.Thread(target=self._run, name="stage-publisher", daemon=True).start()

    def stop(self) -> None:
        self._stopping.set()

    def _run(self) -> None:
        while True:
            try:
                self.cycle()
            except Exception:
                # The publisher must outlive a bug, or the public page freezes.
                log.exception("publishing to the public store failed")
            if self._stopping.wait(PUBLISH_TICK_S):
                return

    def _learn_clock(self) -> None:
        if (
            self._offset_ms is not None
            and self._clock() - self._offset_at < CLOCK_REFRESH_S
        ):
            return
        asked = self._clock()
        store_ms = self._store.time_ms()
        answered = self._clock()
        # The store read its clock about halfway through the round trip.
        self._offset_ms = store_ms - (asked + answered) / 2 * 1000
        self._offset_at = answered

    def _store_ms(self, local_s: float) -> int:
        return round(local_s * 1000 + self._offset_ms)

    def _publish_snapshot(self) -> None:
        view = self._session.view()
        content = self._content(view)
        if content == self._published and (
            self._clock() - self._published_at < REFRESH_S
        ):
            return
        anchor = None
        if view.state == "playing":
            anchor = {
                "position_s": view.position_s,
                "at_ms": self._store_ms(view.position_at_s),
            }
        now = self._clock()
        snapshot = {
            "version": SNAPSHOT_VERSION,
            "published_at_ms": self._store_ms(now),
            "session": {**content["session"], "anchor": anchor},
            "playlist": content["playlist"],
            "lyrics": content["lyrics"],
        }
        self._store.set_json(SNAPSHOT_KEY, snapshot)
        self._published = content
        self._published_at = now

    def _content(self, view: SessionView) -> dict:
        """The snapshot without its times: a change in it is published at once."""
        playlist = []
        for entry in self._playlist.entries():
            published = entry_json(entry)
            # English for the logs, and an unreachable Studio's carries its address.
            del published["detail"]
            playlist.append(published)
        return {
            "session": {
                "state": view.state,
                "entry_id": view.entry_id,
                "duration_s": view.duration_s,
            },
            "playlist": playlist,
            "lyrics": self._lyrics_of(view) if view.state == "playing" else None,
        }

    def _lyrics_of(self, view: SessionView) -> dict | None:
        """The lyrics of the song playing, read once per song."""
        entry = self._playlist.get(view.entry_id)
        song_id = None if entry is None else entry.song.song_id
        if song_id is None:
            return None
        if self._lyrics is None or self._lyrics[0] != song_id:
            self._lyrics = (song_id, self._read_lyrics(song_id))
        return self._lyrics[1]

    def _read_lyrics(self, song_id: str) -> dict | None:
        song = lib.scan(self._library).get(song_id)
        if song is None:
            return None
        try:
            lyrics = song_lyrics(song)
        except OSError as error:
            log.warning("the lyrics of %s are not published: %s", song_id, error)
            return None
        return {
            "song_id": song_id,
            "status": lyrics.status,
            "lines": [asdict(line) for line in lyrics.lines],
        }

    def _publish_library(self) -> None:
        now = self._clock()
        checked = self._library_checked_at
        if checked is not None and now - checked < LIBRARY_CHECK_S:
            return
        self._library_checked_at = now
        songs = [asdict(result) for result in library_results(self._library)]
        if songs == self._songs:
            return
        self._store.set_json(
            LIBRARY_KEY, {"published_at_ms": self._store_ms(now), "songs": songs}
        )
        self._songs = songs


class RequestReader:
    """Applies the public requests; call cycle() or start().

    The first cycle that reaches the store deletes the requests left from
    before, and only the cycles after it read. Each request is taken from the
    head of the list and answered under its id; a search is handed to a pool
    of its own, so the requests behind it, and the publisher, never wait on
    YouTube.
    """

    def __init__(
        self,
        store: Store,
        playlist: Playlist,
        session: Session,
        find: Callable[[str], Found],
    ) -> None:
        self._store = store
        self._playlist = playlist
        self._session = session
        self._find = find
        self._cleared = False
        self._searches = ThreadPoolExecutor(
            SEARCH_WORKERS, thread_name_prefix="stage-public-search"
        )
        self._stopping = threading.Event()

    def cycle(self) -> None:
        try:
            if not self._cleared:
                self._store.delete(REQUESTS_KEY)
                self._cleared = True
                return
            while (raw := self._store.pop(REQUESTS_KEY)) is not None:
                self._take(raw)
        except StoreError:
            # The store logged it; the next cycle tries again.
            return

    def start(self) -> None:
        threading.Thread(target=self._run, name="stage-requests", daemon=True).start()

    def stop(self) -> None:
        """End the reader; a search running finishes on its own."""
        self._stopping.set()
        self._searches.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        """Wait for the searches started, for tests."""
        self._searches.shutdown(wait=True)

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                self.cycle()
            except Exception:
                log.exception("reading the public requests failed")
            if self._stopping.wait(READ_TICK_S):
                return

    def _take(self, raw: str) -> None:
        try:
            request = json.loads(raw)
        except ValueError:
            request = None
        request_id = request.get("id") if isinstance(request, dict) else None
        if not isinstance(request_id, str) or not request_id:
            log.warning(
                "dropped a public request without an id: %s", raw[:LOGGED_CHARS]
            )
            return
        if request.get("op") == "search" and isinstance(request.get("q"), str):
            try:
                self._searches.submit(self._search, request_id, request["q"])
            except RuntimeError:
                # stop() shut the pool while this cycle ran.
                pass
            return
        try:
            answer = self._apply(request)
        except Exception:
            log.exception("public request %s failed", request_id)
            return
        if answer is INVALID:
            log.warning(
                "public request %s is invalid: %s", request_id, raw[:LOGGED_CHARS]
            )
        self._answer(request_id, answer)

    def _apply(self, request: dict) -> dict:
        """The answer to an add or a remove, as the Stage API decides it."""
        op = request.get("op")
        if op == "add":
            try:
                song = AddRequest.model_validate(request.get("song"))
            except ValidationError:
                return INVALID
            try:
                entry = self._playlist.add(Result(**song.model_dump()))
            except NotAddable:
                return {"ok": False, "error": "not_addable"}
            return {"ok": True, "entry_id": entry.id}
        if op == "remove" and isinstance(request.get("entry_id"), str):
            try:
                self._session.remove(request["entry_id"])
            except SessionError as error:
                return {"ok": False, "error": error.code}
            return {"ok": True}
        return INVALID

    def _search(self, request_id: str, query: str) -> None:
        try:
            found = self._find(query)
            self._answer(
                request_id,
                {
                    "ok": True,
                    "results": [asdict(result) for result in found.results],
                    "notice": None if found.notice is None else "offline",
                },
            )
        except StoreError:
            return
        except Exception:
            # The pool would keep the error to itself.
            log.exception("public request %s failed", request_id)

    def _answer(self, request_id: str, answer: dict) -> None:
        self._store.set_json(ANSWER_PREFIX + request_id, answer, ANSWER_EXPIRY_S)
