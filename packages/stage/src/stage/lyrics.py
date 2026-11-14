"""The lyrics of a library song, as the screen shows them while it plays.

Synced lyrics are an LRC file: lines of "[mm:ss.xx] text", where one line may
carry several stamps. Unsynced lyrics are plain text, one line per line.
"""

import re
from dataclasses import dataclass
from typing import Literal

from stage import library as lib

_STAMP = re.compile(r"\[(\d+):(\d{2})(?:\.(\d{1,3}))?\]")

Status = Literal["synced", "unsynced", "none"]


@dataclass(frozen=True)
class Line:
    """time_s is None for unsynced lyrics."""

    time_s: float | None
    text: str


@dataclass(frozen=True)
class SongLyrics:
    status: Status
    lines: list[Line]


def parse_lrc(text: str, offset_ms: int = 0) -> list[Line]:
    """The stamped lines in time order, each shifted by offset_ms.

    A line with several stamps comes once per stamp. Lines without a stamp
    at their start (tags such as [ar:...], blank lines) are skipped.
    """
    lines = []
    for raw in text.lstrip("\ufeff").splitlines():
        stamps = []
        rest = raw
        while (stamp := _STAMP.match(rest)) is not None:
            stamps.append(_milliseconds(stamp))
            rest = rest[stamp.end() :]
        lines.extend(Line((ms + offset_ms) / 1000, rest.strip()) for ms in stamps)
    return sorted(lines, key=lambda line: line.time_s)


def _milliseconds(stamp: re.Match) -> int:
    minutes, seconds, fraction = stamp.groups()
    # ".5" is half a second, ".05" and ".050" both 50 ms.
    fraction_ms = int((fraction or "0").ljust(3, "0"))
    return (int(minutes) * 60 + int(seconds)) * 1000 + fraction_ms


def song_lyrics(song: lib.Song) -> SongLyrics:
    lyrics = song.manifest.lyrics
    if lyrics is None:
        return SongLyrics("none", [])
    text = (song.folder / lyrics.file).read_text(encoding="utf-8-sig")
    if lyrics.synced:
        return SongLyrics("synced", parse_lrc(text, lyrics.offset_ms))
    return SongLyrics("unsynced", [Line(None, line) for line in text.splitlines()])
