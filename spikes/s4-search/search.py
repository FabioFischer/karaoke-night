# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "ytmusicapi",
#     "yt-dlp",
# ]
# ///
"""Search YouTube Music, yt-dlp and LRCLIB for every song in a list, for spike S4.

Usage: uv run spikes/s4-search/search.py [--songs PATH] [--out DIR]

Each line of the song list is "Title - Artist", split on the last " - ". Lines
starting with "#" and blank lines are skipped. Text is kept as typed: typos are
part of the test.

Per song, sequentially:
  1. ytmusicapi search with the "songs" filter, first 3 results.
  2. yt-dlp "ytsearch3:" with flat extraction: listing only, no format
     resolution and no download.
  3. LRCLIB lyrics for the ytmusic #1 result: /api/get with its signature, and
     on 404 /api/search, taking the first hit within DURATION_TOLERANCE_S.

Writes results.local.json (everything) and results.local.md (one row per song,
with an empty verdict column) into --out, then prints a summary. An error on
one song is recorded for that song and the run goes on.
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
LRCLIB = "https://lrclib.net"
# LRCLIB asks clients to identify themselves with a name, version and project link.
USER_AGENT = "karaoke-night-s4-spike/0.1 (https://github.com/FabioFischer/karaoke-night)"
# /api/get itself matches a duration within 2 s, so search hits get the same slack.
DURATION_TOLERANCE_S = 2
# LRCLIB asks batch clients to wait 200 to 500 ms between requests.
PAUSE_S = 0.3
TOP = 3
STATUSES = ("synced", "plain", "instrumental", "none")


class LrclibError(Exception):
    pass


def parse_songs(text: str) -> list[dict]:
    songs = []
    for raw in text.removeprefix("\ufeff").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        title, sep, artist = line.rpartition(" - ")
        if not sep:
            title, artist = line, ""
        query = f"{title} {artist}" if artist else title
        songs.append({"line": line, "title": title, "artist": artist, "query": query})
    return songs


def ytmusic_top(search, query: str) -> list[dict]:
    """search is YTMusic().search. It ignores small limits, so slice here."""
    return [
        {
            "videoId": r.get("videoId"),
            "title": r.get("title"),
            "artists": [a["name"] for a in r.get("artists") or []],
            "album": (r.get("album") or {}).get("name"),
            "duration_seconds": r.get("duration_seconds"),
        }
        for r in search(query, filter="songs")[:TOP]
    ]


def ytdlp_top(extract, query: str) -> list[dict]:
    """extract is YoutubeDL.extract_info with download off and extract_flat on."""
    info = extract(f"ytsearch{TOP}:{query}")
    return [
        {
            "id": e.get("id"),
            "title": e.get("title"),
            "channel": e.get("channel") or e.get("uploader"),
            "duration": e.get("duration"),
        }
        for e in (info.get("entries") or [])[:TOP]
    ]


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


def _summary(record: dict) -> dict:
    keep = ("id", "trackName", "artistName", "albumName", "duration", "instrumental")
    summary = {k: record.get(k) for k in keep}
    summary["synced_lines"] = len((record.get("syncedLyrics") or "").splitlines())
    summary["plain_lines"] = len((record.get("plainLyrics") or "").splitlines())
    return summary


def find_lyrics(get, track: str, artist: str, album: str | None, duration: float | None) -> dict:
    """get(path, params) returns (http status, parsed JSON body or None).

    route is "get" or "search" for the request that matched, None when nothing did.
    /api/get can answer 200 with a record that has no lyrics, so that case also
    searches. The best candidate wins in STATUSES order; on a tie, the first.
    """
    params = {"track_name": track, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = duration
    status, body = get("/api/get", params)
    candidates = []
    if status == 200:
        candidates.append(("get", body))
        if classify(body) != "none":
            return _match(candidates, None)
    elif status != 404:
        raise LrclibError(f"/api/get returned {status}")

    status, hits = get("/api/search", {"track_name": track, "artist_name": artist})
    if status != 200:
        raise LrclibError(f"/api/search returned {status}")
    hits = hits or []
    candidates += [
        ("search", hit) for hit in hits
        if duration is None or (hit.get("duration") is not None and abs(hit["duration"] - duration) <= DURATION_TOLERANCE_S)
    ]
    return _match(candidates, len(hits))


def _match(candidates: list[tuple[str, dict]], search_hits: int | None) -> dict:
    if not candidates:
        return {"status": "none", "route": None, "record": None, "search_hits": search_hits}
    route, best = min(candidates, key=lambda c: STATUSES.index(classify(c[1])))
    return {"status": classify(best), "route": route, "record": _summary(best), "search_hits": search_hits}


def lrclib_get(path: str, params: dict, timeout_s: float = 15.0) -> tuple[int, object]:
    request = urllib.request.Request(f"{LRCLIB}{path}?{urlencode(params)}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as reply:
            return reply.status, json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, None


def _error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _timed(func, *args):
    start = time.perf_counter()
    try:
        return func(*args), None, time.perf_counter() - start
    except Exception as error:  # one song's failure is data, not a reason to stop
        return None, _error(error), time.perf_counter() - start


def run(songs: list[dict], ytmusic_search, ytdlp_extract, lrclib, pause_s: float = PAUSE_S, log=None) -> list[dict]:
    results = []
    for n, song in enumerate(songs, 1):
        if n > 1 and pause_s:
            time.sleep(pause_s)
        yt, yt_error, yt_s = _timed(ytmusic_top, ytmusic_search, song["query"])
        dl, dl_error, dl_s = _timed(ytdlp_top, ytdlp_extract, song["query"])

        lyrics = {"seconds": None, "error": None, "status": None, "route": None, "record": None, "search_hits": None}
        if yt:
            first = yt[0]
            found, lr_error, lr_s = _timed(
                find_lyrics, lrclib, first["title"], ", ".join(first["artists"]), first["album"], first["duration_seconds"],
            )
            lyrics.update(found or {})
            lyrics.update(seconds=lr_s, error=lr_error)

        result = {
            **song,
            "ytmusic": {"seconds": yt_s, "error": yt_error, "results": yt or []},
            "ytdlp": {"seconds": dl_s, "error": dl_error, "results": dl or []},
            "lrclib": lyrics,
        }
        results.append(result)
        if log:
            log(f"{n:>2}/{len(songs)} {song['line']}: ytmusic {len(result['ytmusic']['results'])} in {yt_s:.2f} s, "
                f"yt-dlp {len(result['ytdlp']['results'])} in {dl_s:.2f} s, lyrics {lyrics_cell(lyrics)}")
    return results


def mmss(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def ytmusic_cell(r: dict) -> str:
    return f"{r['title']} · {', '.join(r['artists'])} · {r['album'] or '(no album)'} · {mmss(r['duration_seconds'])}"


def ytdlp_cell(r: dict) -> str:
    return f"{r['title']} · {r['channel'] or '(no channel)'} · {mmss(r['duration'])}"


def lyrics_cell(lyrics: dict) -> str:
    if lyrics["error"]:
        return f"error: {lyrics['error']}"
    if lyrics["status"] is None:
        return "not checked"
    return f"{lyrics['status']} ({lyrics['route']})" if lyrics["route"] else lyrics["status"]


def _top_cells(source: dict, render, count: int) -> list[str]:
    if source["error"]:
        return [f"error: {source['error']}"] + [""] * (count - 1)
    cells = [render(r) for r in source["results"][:count]]
    return cells + [""] * (count - len(cells))


def markdown_row(result: dict) -> str:
    cells = [result["line"]]
    cells += _top_cells(result["ytmusic"], ytmusic_cell, TOP)
    cells += _top_cells(result["ytdlp"], ytdlp_cell, 1)
    cells += [lyrics_cell(result["lrclib"]), ""]
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


HEADER = ("Line as typed", "ytmusic #1", "ytmusic #2", "ytmusic #3", "yt-dlp #1", "LRCLIB", "Verdict")


def markdown_table(results: list[dict]) -> str:
    lines = ["| " + " | ".join(HEADER) + " |", "|" + "---|" * len(HEADER)]
    lines += [markdown_row(r) for r in results]
    return "\n".join(lines) + "\n"


def _median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def summarize(results: list[dict]) -> dict:
    counts = {status: 0 for status in STATUSES}
    counts.update({"not checked": 0, "error": 0})
    for r in results:
        lyrics = r["lrclib"]
        key = "error" if lyrics["error"] else lyrics["status"] or "not checked"
        counts[key] += 1
    return {
        "songs": len(results),
        "lrclib": counts,
        "median_seconds": {source: _median([r[source]["seconds"] for r in results]) for source in ("ytmusic", "ytdlp", "lrclib")},
    }


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not installed"


def real_clients():
    from ytmusicapi import YTMusic
    from yt_dlp import YoutubeDL

    # extract_flat lists the search hits without resolving formats, which is
    # where yt-dlp needs a JS runtime for YouTube's "n" challenge.
    ydl = YoutubeDL({"extract_flat": True, "skip_download": True, "quiet": True, "no_warnings": True})
    return YTMusic().search, lambda url: ydl.extract_info(url, download=False), lrclib_get


def _fmt_s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f} s"


def main(argv: list[str] | None = None, clients=None) -> None:
    parser = argparse.ArgumentParser(description="Search ytmusic, yt-dlp and LRCLIB for every song in a list (spike S4).")
    parser.add_argument("--songs", type=Path, default=HERE.parent / "songs.local.txt", help="song list (default spikes/songs.local.txt)")
    parser.add_argument("--out", type=Path, default=HERE, help="output folder (default spikes/s4-search/)")
    args = parser.parse_args(argv)

    if not args.songs.is_file():
        sys.exit(f"song list not found: {args.songs}")
    songs = parse_songs(args.songs.read_text(encoding="utf-8"))
    if not songs:
        sys.exit(f"no songs in {args.songs}")

    versions = {package: _version(package) for package in ("ytmusicapi", "yt-dlp")}
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = run(songs, *(clients or real_clients()), log=print)
    summary = summarize(results)

    args.out.mkdir(parents=True, exist_ok=True)
    report = {"started_at": started, "versions": versions, "summary": summary, "songs": results}
    (args.out / "results.local.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    heading = (
        f"# S4 results: {started}\n\n"
        f"ytmusicapi {versions['ytmusicapi']}, yt-dlp {versions['yt-dlp']}. "
        "Verdict: 1, 2 or 3 for the ytmusic result that is the right version, or none.\n\n"
    )
    (args.out / "results.local.md").write_text(heading + markdown_table(results), encoding="utf-8")

    counts = ", ".join(f"{k}: {v}" for k, v in summary["lrclib"].items())
    medians = summary["median_seconds"]
    print(f"songs: {summary['songs']}")
    print(f"LRCLIB: {counts}")
    print(f"median query time: ytmusic {_fmt_s(medians['ytmusic'])}, yt-dlp {_fmt_s(medians['ytdlp'])}, LRCLIB {_fmt_s(medians['lrclib'])}")
    print(f"wrote {args.out / 'results.local.json'} and {args.out / 'results.local.md'}")


if __name__ == "__main__":
    # A Windows console or pipe may not encode every title; a crash there would lose the run.
    sys.stdout.reconfigure(errors="replace")
    main()
