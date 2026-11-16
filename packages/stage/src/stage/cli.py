"""The `stage` command line: `stage sync`, `list`, `play`, `search` and `serve`."""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import httpx2
import uvicorn

from stage import library as lib
from stage import search
from stage.client import StudioClient, StudioError
from stage.player import Player, ReaperError, ReaperPlayer
from stage.playlist import Playlist
from stage.public import Publisher, RequestReader, StoreClock
from stage.server import create_app
from stage.session import Session, SessionError
from stage.store import Store
from stage.sync import Outcome, sync

DEFAULT_LIBRARY = Path("library")
DEFAULT_WEB = Path("web") / "dist"
# Loopback only: in the alpha the TV mirrors the Stage's own screen.
HOST = "127.0.0.1"
# REAPER's web interface is on 8080, the Studio API on 8790.
DEFAULT_PORT = 8791
TOKEN_VARIABLE = "STUDIO_TOKEN"
STORE_URL_VARIABLE = "KARAOKE_STORE_URL"
STORE_TOKEN_VARIABLE = "KARAOKE_STORE_TOKEN"
POLL_INTERVAL_S = 0.25
ACK_TIMEOUT_S = 5.0
# 128 + SIGINT, as shells report a command stopped with Ctrl+C.
INTERRUPTED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stage", description="The Stage of karaoke-night."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sync_command = commands.add_parser(
        "sync",
        help="mirror the Studio library",
        description=(
            "Mirror the Studio library: download the songs the Studio lists and "
            "the library lacks or holds in another version, and remove the songs "
            "the Studio no longer lists. Interrupted downloads resume on the next "
            f"sync. The token comes from the {TOKEN_VARIABLE} environment variable."
        ),
    )
    sync_command.add_argument(
        "--studio",
        required=True,
        metavar="URL",
        help="the Studio API, for example http://studio:8790",
    )
    list_command = commands.add_parser(
        "list",
        help="list the songs in the library",
        description="List the songs in the library. Needs no network.",
    )
    play_command = commands.add_parser(
        "play",
        help="play a song of the library in REAPER",
        description=(
            "Load a song of the library in REAPER, set its stems and key, and play "
            "it from the start until it ends. Ctrl+C stops it. Needs REAPER open "
            "with the web interface on port 8080 and the karaoke listener running "
            "(reaper/README.md)."
        ),
    )
    play_command.add_argument("song_id", help="the song, as stage list shows it")
    play_command.add_argument(
        "--mute",
        nargs="+",
        action="extend",
        default=[],
        metavar="STEM",
        help="stems to mute, for example lead; every other stem plays",
    )
    play_command.add_argument(
        "--transpose",
        type=int,
        default=0,
        metavar="N",
        help="semitones to shift every stem except drums (default: 0)",
    )
    play_command.set_defaults(usage_error=play_command.error)
    search_command = commands.add_parser(
        "search",
        help="search the library and YouTube Music",
        description=(
            "Search the library, then YouTube Music (or YouTube through yt-dlp "
            "when YouTube Music fails). The songs already in the library come "
            "first. Without internet, only the library is searched."
        ),
    )
    search_command.add_argument("text", help="words of the title or the artist")
    serve_command = commands.add_parser(
        "serve",
        help="run the Stage API and the TV screens",
        description=(
            f"Serve the Stage API and the built web on http://{HOST}:<port>: the "
            "playlist, the session that plays it in REAPER, search and lyrics. "
            "New songs go to the Studio given by --studio, with the token from the "
            f"{TOKEN_VARIABLE} environment variable; without them, only songs in "
            "the library can be added. With the public store's REST URL and token "
            f"in {STORE_URL_VARIABLE} and {STORE_TOKEN_VARIABLE}, it also publishes "
            "the playlist to the public page and applies the requests left there. "
            "Never run it next to stage sync on the same library."
        ),
    )
    serve_command.add_argument(
        "--studio",
        metavar="URL",
        help="the Studio API, for example http://studio:8790",
    )
    serve_command.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"the port to listen on (default: {DEFAULT_PORT})",
    )
    serve_command.add_argument(
        "--web",
        type=Path,
        default=DEFAULT_WEB,
        metavar="DIR",
        help="the built web, from npm run build in web/ (default: web/dist, in "
        "the current folder)",
    )
    commands_with_library = (
        sync_command,
        list_command,
        play_command,
        search_command,
        serve_command,
    )
    for command in commands_with_library:
        command.add_argument(
            "--library",
            type=Path,
            default=DEFAULT_LIBRARY,
            help="the library folder (default: library, in the current folder)",
        )
    return parser


def main(argv: list[str] | None = None, http: httpx2.Client | None = None) -> int:
    """http replaces the client sync or play makes of its own, for tests."""
    # Redirected on Windows, both default to the ANSI code page, which cannot
    # encode most song titles.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("warning: %(message)s"))
    handler.setLevel(logging.WARNING)
    logger = logging.getLogger("stage")
    logger.addHandler(handler)
    try:
        if args.command == "sync":
            return sync_library(args, http)
        if args.command == "play":
            return play_song(args, http)
        if args.command == "search":
            return search_songs(args)
        if args.command == "serve":
            return serve(args, http)
        return list_library(args)
    finally:
        logger.removeHandler(handler)


def _line(outcome: Outcome) -> str:
    line = f"{outcome.action:<7} {outcome.song_id}"
    return line if outcome.reason is None else f"{line}: {outcome.reason}"


def sync_library(args: argparse.Namespace, http: httpx2.Client | None) -> int:
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        print(
            f"stage sync needs the {TOKEN_VARIABLE} environment variable: the token "
            "of the Studio API, sent as 'Authorization: Bearer <token>'",
            file=sys.stderr,
        )
        return 1

    started = time.perf_counter()
    with StudioClient(args.studio, token, http=http) as studio:
        try:
            summary = sync(
                args.library, studio, lambda outcome: print(_line(outcome), flush=True)
            )
        except StudioError as error:
            print(
                f"stage sync could not get the song list from {studio.base_url}: "
                f"{error}; nothing in the library changed",
                file=sys.stderr,
            )
            return 1
    seconds = time.perf_counter() - started

    counts = {action: 0 for action in ("added", "updated", "removed", "busy", "failed")}
    for outcome in summary.outcomes:
        counts[outcome.action] += 1
    print(
        ", ".join(f"{count} {action}" for action, count in counts.items())
        + f", {summary.unchanged} unchanged, {summary.skipped} skipped; "
        f"{summary.bytes_downloaded / 1e6:.1f} MB downloaded in {seconds:.1f} s"
    )
    return 1 if counts["failed"] else 0


def _duration(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def list_library(args: argparse.Namespace) -> int:
    songs = lib.scan(args.library)
    if not songs:
        print(f"no songs in {args.library}")
    for song_id, song in songs.items():
        manifest = song.manifest
        print(
            f"{song_id}  {manifest.title}  {manifest.artist}  "
            f"{_duration(manifest.duration_s)}"
        )
    return 0


def search_songs(args: argparse.Namespace) -> int:
    found = search.find(
        args.text, args.library, search.ytmusic_songs, search.ytdlp_videos
    )
    if not found.results:
        print(f"no results for {args.text!r}")
    for result in found.results:
        print(_search_line(result))
    if found.notice is not None:
        print(found.notice)
    return 0


def _search_line(result: search.Result) -> str:
    state = "new" if result.song_id is None else "ready"
    duration = None if result.duration_s is None else _duration(result.duration_s)
    columns = [result.title, result.artist, result.album, duration, result.video_id]
    line = f"{state:<5}  " + "  ".join(value or "-" for value in columns)
    return line if result.song_id is None else f"{line}  {result.song_id}"


def play_song(args: argparse.Namespace, http: httpx2.Client | None) -> int:
    song = lib.scan(args.library).get(args.song_id)
    if song is None:
        print(
            f"stage play: no song {args.song_id!r} in {args.library}; "
            "stage list shows the songs",
            file=sys.stderr,
        )
        return 1
    manifest = song.manifest
    stems = list(manifest.stems)
    for stem in args.mute:
        if stem not in stems:
            args.usage_error(
                f"argument --mute: unknown stem {stem!r}; the stems of this song "
                f"are: {', '.join(stems)}"
            )

    with ReaperPlayer(http=http, ack_timeout_s=ACK_TIMEOUT_S) as player:
        try:
            player.connect()
            player.load((song.folder / "manifest.json").resolve())
            # Every stem is set, so the mutes of the previous song do not stay.
            for stem in stems:
                player.set_mute(stem, stem in args.mute)
            player.transpose(args.transpose)
            player.play()
            print(
                f"playing {manifest.title} by {manifest.artist} "
                f"({_duration(manifest.duration_s)})",
                flush=True,
            )
            return _follow(player, manifest.duration_s)
        except ReaperError as error:
            print(f"stage play: {error}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            try:
                player.stop()
            except ReaperError as error:
                print(f"stage play: could not stop playback: {error}", file=sys.stderr)
            print("stopped", flush=True)
            return INTERRUPTED


def _follow(player: Player, duration_s: float) -> int:
    """Print the position once per second of the song until it ends."""
    printed = None
    while True:
        position = player.position()
        if player.ended():
            print("the song ended", flush=True)
            return 0
        if int(position) != printed:
            printed = int(position)
            print(f"{_duration(printed)} / {_duration(duration_s)}", flush=True)
        time.sleep(POLL_INTERVAL_S)


def run_server(app, host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port)


def serve(args: argparse.Namespace, http: httpx2.Client | None) -> int:
    token = os.environ.get(TOKEN_VARIABLE)
    studio = StudioClient(args.studio, token) if args.studio and token else None
    if studio is None:
        print(
            f"stage serve: no Studio (--studio and the {TOKEN_VARIABLE} environment "
            "variable); only songs in the library can be added",
            file=sys.stderr,
        )
    web = args.web if (args.web / "index.html").is_file() else None
    if web is None:
        print(
            f"stage serve: no built web in {args.web} (npm run build in web/); "
            "serving the API only",
            file=sys.stderr,
        )
    store_url = os.environ.get(STORE_URL_VARIABLE)
    store_token = os.environ.get(STORE_TOKEN_VARIABLE)
    # Its threads may be in a request when the server stops, so it is never
    # closed: the process ending closes its connections.
    store = Store(store_url, store_token) if store_url and store_token else None
    if store is None:
        print(
            f"stage serve: no public store (the {STORE_URL_VARIABLE} and "
            f"{STORE_TOKEN_VARIABLE} environment variables); nothing is published "
            "and no public request is read",
            file=sys.stderr,
        )

    def find(text: str) -> search.Found:
        return search.find(
            text, args.library, search.ytmusic_songs, search.ytdlp_videos
        )

    player = ReaperPlayer(http=http, ack_timeout_s=ACK_TIMEOUT_S)
    playlist = Playlist(args.library, studio)
    session = Session(playlist, args.library, player)
    try:
        session.read_volumes()
    except SessionError as error:
        # REAPER may still be opening: the night script starts both at once.
        print(
            f"stage serve: could not read the volumes from REAPER ({error.detail}); "
            "they are read again when a song starts",
            file=sys.stderr,
        )
    public = []
    if store is not None:
        # The session times its positions on the store clock's local clock.
        clock = StoreClock(store)
        public = [
            Publisher(store, clock, args.library, playlist, session),
            RequestReader(store, clock, playlist, session, find),
        ]
    playlist.start()
    for worker in public:
        worker.start()
    try:
        app = create_app(args.library, playlist, session, web=web, find=find)
        run_server(app, HOST, args.port)
    finally:
        for worker in public:
            worker.stop()
        playlist.stop()
        session.close()
        player.close()
        if studio is not None:
            studio.close()
    return 0
