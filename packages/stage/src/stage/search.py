"""Song search: the library first, then YouTube Music, with yt-dlp as the fallback.

The library part needs no network. The online clients are passed in, so tests
never reach YouTube.
"""

import logging
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yt_dlp
from core.manifest import YouTubeMusicSource
from ytmusicapi import YTMusic

from stage import library as lib

log = logging.getLogger(__name__)

ONLINE_LIMIT = 10
OFFLINE_NOTICE = "could not search online; only songs already in the library are listed"

Origin = Literal["library", "ytmusic", "yt-dlp"]
Client = Callable[[str], list[dict]]
"""Takes the query and returns the raw results of one online source."""


@dataclass(frozen=True)
class Result:
    """What POST /jobs of the Studio needs and what the search list shows.

    song_id is set when the song is in the library.
    """

    title: str
    artist: str
    origin: Origin
    video_id: str | None = None
    album: str | None = None
    duration_s: float | None = None
    thumbnail_url: str | None = None
    song_id: str | None = None


@dataclass(frozen=True)
class Found:
    results: list[Result]
    notice: str | None = None


def ytmusic_songs(query: str) -> list[dict]:
    # Its limit only stops further pages: the first page can hold more
    # results than asked for, so find keeps the first ONLINE_LIMIT.
    return YTMusic().search(query, filter="songs")


class _Silent:
    """Drops yt-dlp's own output: a failure is raised, and logged once by find."""

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


def ytdlp_videos(query: str) -> list[dict]:
    # Flat extraction lists the hits without resolving each video, so it
    # needs no JS runtime.
    options = {
        "extract_flat": True,
        "quiet": True,
        "skip_download": True,
        "extractor_retries": 1,
        "logger": _Silent(),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch{ONLINE_LIMIT}:{query}", download=False)
    return list(info.get("entries") or [])


def find(query: str, library: Path, ytmusic: Client, ytdlp: Client) -> Found:
    """Library matches first, then up to ONLINE_LIMIT online results.

    An online result already in the library shows as its library entry,
    after the songs the words matched. yt-dlp runs only when YouTube Music
    fails or finds nothing; if yt-dlp then fails, the library matches come
    back with OFFLINE_NOTICE. A query without words finds nothing and searches
    nothing online.
    """
    words = _normalize(query).split()
    if not words:
        return Found([])
    entries = [
        _from_library(song_id, song) for song_id, song in lib.scan(library).items()
    ]
    ready = [entry for entry in entries if _matches(words, entry)]
    by_video_id = {entry.video_id: entry for entry in entries if entry.video_id}

    online, notice = _search_online(query, ytmusic, ytdlp)
    new = []
    for result in online:
        in_library = by_video_id.get(result.video_id)
        if in_library is None:
            new.append(result)
        elif in_library not in ready:
            ready.append(in_library)
    return Found(ready + new, notice)


def _search_online(
    query: str, ytmusic: Client, ytdlp: Client
) -> tuple[list[Result], str | None]:
    # Both clients scrape pages YouTube changes without notice, so any error,
    # a network one or an answer in an unknown shape, is a failed search.
    try:
        results = [_from_ytmusic(song) for song in ytmusic(query)[:ONLINE_LIMIT]]
    except Exception as error:  # noqa: BLE001
        log.warning("YouTube Music search failed (%s); searching with yt-dlp", error)
        results = []
    if results:
        return results, None
    try:
        return [_from_ytdlp(video) for video in ytdlp(query)[:ONLINE_LIMIT]], None
    except Exception as error:  # noqa: BLE001
        log.warning("yt-dlp search failed (%s)", error)
        return [], OFFLINE_NOTICE


def _normalize(text: str) -> str:
    """Case and accents folded: "Canção" and "CANCAO" both give "cancao"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _matches(words: list[str], entry: Result) -> bool:
    """Every word appears, even as part of a longer one, in the title or artist."""
    searchable = _normalize(f"{entry.title}\n{entry.artist}")
    return all(word in searchable for word in words)


def _from_library(song_id: str, song: lib.Song) -> Result:
    manifest = song.manifest
    source = manifest.source
    return Result(
        title=manifest.title,
        artist=manifest.artist,
        origin="library",
        video_id=source.video_id if isinstance(source, YouTubeMusicSource) else None,
        duration_s=manifest.duration_s,
        song_id=song_id,
    )


def _from_ytmusic(song: dict) -> Result:
    album = song.get("album")
    return Result(
        title=song["title"],
        artist=", ".join(artist["name"] for artist in song.get("artists") or []),
        origin="ytmusic",
        video_id=song["videoId"],
        album=album["name"] if album else None,
        duration_s=_seconds(song.get("duration_seconds")),
        thumbnail_url=_largest(song.get("thumbnails")),
    )


def _from_ytdlp(video: dict) -> Result:
    # A flat yt-dlp hit has no album and no separate artist.
    return Result(
        title=video["title"],
        artist=video.get("channel") or "",
        origin="yt-dlp",
        video_id=video["id"],
        duration_s=_seconds(video.get("duration")),
        thumbnail_url=_largest(video.get("thumbnails")),
    )


def _seconds(value: float | None) -> float | None:
    return None if value is None else float(value)


def _largest(thumbnails: list[dict] | None) -> str | None:
    if not thumbnails:
        return None
    biggest = max(
        thumbnails,
        key=lambda thumbnail: (
            (thumbnail.get("width") or 0) * (thumbnail.get("height") or 0)
        ),
    )
    return biggest["url"]
