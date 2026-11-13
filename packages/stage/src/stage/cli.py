"""The `stage` command line: `stage sync` and `stage list`."""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import httpx2

from stage import library as lib
from stage.client import StudioClient, StudioError
from stage.sync import Outcome, sync

DEFAULT_LIBRARY = Path("library")
TOKEN_VARIABLE = "STUDIO_TOKEN"


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
    for command in (sync_command, list_command):
        command.add_argument(
            "--library",
            type=Path,
            default=DEFAULT_LIBRARY,
            help="the library folder (default: library, in the current folder)",
        )
    return parser


def main(argv: list[str] | None = None, http: httpx2.Client | None = None) -> int:
    """http replaces the client a sync makes of its own, for tests."""
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
