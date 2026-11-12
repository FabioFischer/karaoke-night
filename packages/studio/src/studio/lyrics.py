"""Lyrics from LRCLIB (https://lrclib.net), the lookup measured in spike S4."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from urllib.parse import urlencode

LRCLIB = "https://lrclib.net"
# LRCLIB asks clients to identify themselves with a name, version and project link.
USER_AGENT = (
    f"karaoke-night/{metadata.version('studio')} "
    "(https://github.com/FabioFischer/karaoke-night)"
)
# /api/get itself matches a duration within 2 s, so search hits get the same slack.
DURATION_TOLERANCE_S = 2
# LRCLIB asks batch clients to wait 200 to 500 ms between requests.
PAUSE_S = 0.3
STATUSES = ("synced", "plain", "instrumental", "none")


class LrclibError(Exception):
    pass


@dataclass(frozen=True)
class FoundLyrics:
    file: str
    text: str
    synced: bool


def classify(record: dict | None) -> str:
    if not record:
        return "none"
    if record.get("instrumental"):
        return "instrumental"
    if (record.get("syncedLyrics") or "").strip():
        return "synced"
    if (record.get("plainLyrics") or "").strip():
        return "plain"
    return "none"


def find_lyrics(
    get, track: str, artist: str, album: str | None, duration: float | None
) -> dict:
    """get(path, params) returns (http status, parsed JSON body or None).

    Returns the status and the record that won. /api/get can answer 200 with a
    record that has no lyrics, so that case also searches. The best candidate
    wins in STATUSES order; on a tie, the first.
    """
    params = {"track_name": track, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = duration
    status, body = get("/api/get", params)
    candidates = []
    if status == 200:
        candidates.append(body)
        if classify(body) != "none":
            return _match(candidates)
    elif status != 404:
        raise LrclibError(f"/api/get returned {status}")

    status, hits = get("/api/search", {"track_name": track, "artist_name": artist})
    if status != 200:
        raise LrclibError(f"/api/search returned {status}")
    candidates += [
        hit
        for hit in hits or []
        if duration is None
        or (
            hit.get("duration") is not None
            and abs(hit["duration"] - duration) <= DURATION_TOLERANCE_S
        )
    ]
    return _match(candidates)


def _match(candidates: list[dict]) -> dict:
    if not candidates:
        return {"status": "none", "record": None}
    best = min(candidates, key=lambda record: STATUSES.index(classify(record)))
    return {"status": classify(best), "record": best}


def lookup(
    get, track: str, artist: str, album: str | None, duration: float | None
) -> FoundLyrics | None:
    """Synced lyrics as an LRC file, else plain lyrics as text, else None.

    Raises LrclibError on an HTTP failure; a network failure raises OSError.
    """
    found = find_lyrics(get, track, artist, album, duration)
    if found["status"] == "synced":
        return FoundLyrics("lyrics.lrc", found["record"]["syncedLyrics"], True)
    if found["status"] == "plain":
        return FoundLyrics("lyrics.txt", found["record"]["plainLyrics"], False)
    return None


def lrclib_get(path: str, params: dict, timeout_s: float = 15.0) -> tuple[int, object]:
    request = urllib.request.Request(
        f"{LRCLIB}{path}?{urlencode(params)}", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as reply:
            return reply.status, json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, None


class Paced:
    """Wraps get(path, params): waits pause_s before every request but the first."""

    def __init__(self, get, pause_s: float, sleep=time.sleep):
        self.get, self.pause_s, self.sleep = get, pause_s, sleep
        self.started = False

    def __call__(self, path: str, params: dict):
        if self.started and self.pause_s:
            self.sleep(self.pause_s)
        self.started = True
        return self.get(path, params)
