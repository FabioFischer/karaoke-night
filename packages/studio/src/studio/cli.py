"""The `studio` command line: `studio process <url|video_id|file>` and `studio serve`."""

import argparse
import http.client
import importlib.util
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import soundfile as sf
import uvicorn
from core.manifest import FileSource, Source, YouTubeMusicSource
from core.studio_api import JobRequest

from studio import audio, download
from studio.api import create_app
from studio.audio import SAMPLE_RATE, fit_headroom, null_test
from studio.download import source_metadata, video_id_from
from studio.jobs import Build, JobQueue
from studio.lyrics import PAUSE_S, LrclibError, Paced, lookup, lrclib_get
from studio.package import (
    build_atomically,
    sha256_file,
    song_id,
    source_key,
    write_package,
)
from studio.separation import (
    HEADROOM,
    MODELS,
    AudioSeparatorRunner,
    Runner,
    cascade,
    device_info,
    timed,
)

# The modules of the `separation` extra.
EXTRA_MODULES = ("audio_separator", "torch", "yt_dlp")
INSTALL_HINT = "uv sync --package studio --extra separation"
DEFAULT_LIBRARY = Path("library")
# The folder spike S2 used, instead of audio-separator's /tmp/audio-separator-models/.
DEFAULT_MODEL_DIR = Path.home() / ".cache" / "audio-separator-models"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8790
TOKEN_VARIABLE = "STUDIO_TOKEN"


def _paced_lrclib():
    return Paced(lrclib_get, PAUSE_S)


def _run_server(app, host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port)


@dataclass
class Tools:
    """Everything that reaches outside the process, so tests can replace it."""

    find_spec: Callable[[str], object] = importlib.util.find_spec
    fetch_info: Callable[[str], dict] = download.fetch_info
    download: Callable[[str, Path], Path] = download.download_audio
    convert: Callable[[Path, Path], None] = audio.convert
    make_runner: Callable[[Path, Path], Runner] = AudioSeparatorRunner
    lyrics_get: Callable = field(default_factory=_paced_lrclib)
    serve: Callable[..., None] = _run_server


def resolve_target(target: str) -> tuple[str, Path | str] | None:
    """("file", path) for an existing file, ("youtube", video_id) for a URL or id."""
    path = Path(target)
    if "://" not in target and path.is_file():
        return "file", path
    video_id = video_id_from(target)
    if video_id is not None:
        return "youtube", video_id
    return None


def _add_folder_options(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--library",
        type=Path,
        default=DEFAULT_LIBRARY,
        help="the library folder (default: library, in the current folder)",
    )
    command.add_argument(
        "--model-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        help="where audio-separator downloads and caches models "
        "(default: ~/.cache/audio-separator-models); the AUDIO_SEPARATOR_MODEL_DIR "
        "environment variable wins over it, and that folder must exist",
    )


def build_parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """The `studio` parser and its `process` subparser."""
    parser = argparse.ArgumentParser(
        prog="studio", description="The Studio pipeline for karaoke-night."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    process = commands.add_parser(
        "process",
        help="turn a song into a package in the library",
        description=(
            "Download or convert a song, split it into 7 stems, look up its "
            "lyrics on LRCLIB and write the package to <library>/<song_id>. "
            "A song already in the library is left as it is."
        ),
    )
    process.add_argument(
        "target",
        metavar="url|video_id|file",
        help="a YouTube or YouTube Music URL, a bare video id, or a local audio file",
    )
    process.add_argument(
        "--title",
        help="song title: required for a local file, replaces the YouTube one otherwise",
    )
    process.add_argument(
        "--artist",
        help="song artist: required for a local file, replaces the YouTube one otherwise",
    )
    _add_folder_options(process)
    process.set_defaults(error=process.error)

    serve = commands.add_parser(
        "serve",
        help="run the Studio API",
        description=(
            "Serve the Studio API: song jobs, run one at a time into the library, "
            "and the library's packages. Every request needs the token in the "
            f"{TOKEN_VARIABLE} environment variable, as 'Authorization: Bearer <token>'."
        ),
    )
    _add_folder_options(serve)
    serve.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"the address to listen on (default: {DEFAULT_HOST}, every interface)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"the port to listen on (default: {DEFAULT_PORT})",
    )
    return parser, process


def _hyphen_ids_last(process: argparse.ArgumentParser, argv: list[str]) -> list[str]:
    """Move a bare video id that starts with "-" after "--".

    YouTube ids can start with "-", and argparse would read one as an option.
    Only tokens before an existing "--" that no option of process starts with move.
    """
    if not argv or argv[0] != "process":
        return argv
    end = argv.index("--") if "--" in argv else len(argv)
    known = [option for action in process._actions for option in action.option_strings]
    ids = [
        token
        for token in argv[1:end]
        if token.startswith("-")
        and video_id_from(token) == token
        and not any(option.startswith(token) for option in known)
    ]
    if not ids:
        return argv
    head = [token for token in argv[:end] if token not in ids]
    return [*head, "--", *ids, *argv[end + 1 :]]


def main(argv: list[str] | None = None, tools: Tools | None = None) -> int:
    parser, process_parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(_hyphen_ids_last(process_parser, argv))
    command = process if args.command == "process" else serve
    return command(args, tools or Tools())


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _seconds_since(start: float) -> float:
    return round(time.perf_counter() - start, 2)


def _lacks_extra(command: str, tools: Tools) -> bool:
    missing = [name for name in EXTRA_MODULES if tools.find_spec(name) is None]
    if missing:
        print(
            f"studio {command} needs the separation extra ({', '.join(missing)} "
            f"not installed). Install it with:\n  {INSTALL_HINT}",
            file=sys.stderr,
        )
    return bool(missing)


def serve(args: argparse.Namespace, tools: Tools) -> int:
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        print(
            f"studio serve needs the {TOKEN_VARIABLE} environment variable: the "
            "token every request must send as 'Authorization: Bearer <token>'",
            file=sys.stderr,
        )
        return 1
    if _lacks_extra("serve", tools):
        return 1

    queue = JobQueue(song_builder(args.library, args.model_dir, tools))
    queue.start()
    try:
        tools.serve(create_app(args.library, token, queue), args.host, args.port)
    finally:
        queue.stop()
    return 0


def process(args: argparse.Namespace, tools: Tools) -> int:
    target = resolve_target(args.target)
    if target is None:
        args.error(f"not a file, a YouTube URL or a video id: {args.target}")
    kind, value = target
    if kind == "file" and not (args.title and args.artist):
        args.error("a local file needs --title and --artist")

    if _lacks_extra("process", tools):
        return 1

    started = time.perf_counter()
    report: dict = {"steps": {}}
    source: Source
    if kind == "file":
        source = FileSource(type="file", sha256=sha256_file(value))
        title, artist, album, duration = args.title, args.artist, None, None
    else:
        meta = source_metadata(tools.fetch_info(value))
        report["metadata_seconds"] = _seconds_since(started)
        source = YouTubeMusicSource(type="youtube_music", video_id=value)
        title = args.title or meta["title"]
        artist = args.artist or meta["artist"]
        album, duration = meta["album"], meta["duration_s"]

    package_id = song_id(title, artist, source_key(source))
    existing = args.library / package_id / "manifest.json"
    if existing.is_file():
        print(f"Already processed: {existing}")
        return 0

    package = build_song(
        args.library,
        args.model_dir,
        tools,
        source=source,
        title=title,
        artist=artist,
        album=album,
        duration_s=duration,
        audio_file=value if kind == "file" else None,
        report=report,
    )
    report["total_seconds"] = _seconds_since(started)
    _print_report(report)
    print(f"Wrote {package / 'manifest.json'}")
    return 0


OnStage = Callable[..., None]
"""on_stage(status, step=None), called as each stage starts; step only while separating."""


def _no_stage(status: str, step: str | None = None) -> None:
    pass


def _announced(run: Runner, on_stage: OnStage) -> Runner:
    def wrapped(step, model_file, audio):
        on_stage("separating", step)
        return run(step, model_file, audio)

    return wrapped


def build_song(
    library: Path,
    model_dir: Path,
    tools: Tools,
    *,
    source: Source,
    title: str,
    artist: str,
    album: str | None = None,
    duration_s: float | None = None,
    audio_file: Path | None = None,
    on_stage: OnStage = _no_stage,
    report: dict | None = None,
) -> Path:
    """Build the package of one song into library and return its folder.

    audio_file is the local file of a file source; a YouTube Music source is
    downloaded. Without duration_s, the lyrics lookup uses the audio length.
    report, when given, gets the timings and checks _print_report shows.
    """
    report = {"steps": {}} if report is None else report
    package_id = song_id(title, artist, source_key(source))

    def build(out: Path) -> None:
        work = out / "work"
        work.mkdir()
        mix_path = work / "mix.wav"
        # Created before the download, so a runner that cannot start is never
        # mistaken for a download failure, the only kind a job tries again.
        runner = tools.make_runner(model_dir, work)

        on_stage("downloading")
        start = time.perf_counter()
        if audio_file is not None:
            tools.convert(audio_file, mix_path)
        else:
            downloaded = tools.download(source.video_id, work)
            tools.convert(downloaded, mix_path)
            downloaded.unlink()
        report["download_seconds"] = _seconds_since(start)

        mix, _ = sf.read(mix_path, dtype="float32", always_2d=True)
        n_frames = len(mix)
        levels: dict = {}
        # timed reads the load time and overlap off the runner itself, so it wraps it first.
        run = _announced(timed(runner, report["steps"]), on_stage)
        stems = cascade(mix, run, MODELS, levels)
        for step, row in levels.items():
            report["steps"][step].update(row)
        del mix

        on_stage("lyrics")
        start = time.perf_counter()
        lyrics_duration = round(duration_s or n_frames / SAMPLE_RATE)
        try:
            lyrics = lookup(tools.lyrics_get, title, artist, album, lyrics_duration)
        except (LrclibError, OSError, ValueError, http.client.HTTPException) as error:
            _warn(f"LRCLIB lookup failed ({error}); the package has no lyrics")
            lyrics = None
        report["lyrics_seconds"] = _seconds_since(start)
        report["lyrics"] = lyrics

        on_stage("packaging")
        start = time.perf_counter()
        stems, gain_db = fit_headroom(stems)
        write_package(
            out,
            stems,
            title=title,
            artist=artist,
            source=source,
            gain_db=gain_db,
            lyrics=lyrics,
            models=MODELS,
        )
        del stems
        report["write_seconds"] = _seconds_since(start)
        report["gain_db"] = gain_db
        report["audio_s"] = n_frames / SAMPLE_RATE

        # Shown only, never fatal: nothing is decided on it, so no error from it stops a run.
        try:
            report["null_test"] = null_test(out / "manifest.json", mix_path)
        except Exception as error:  # noqa: BLE001
            _warn(f"null test failed ({error})")
            report["null_test"] = None
        shutil.rmtree(work)

    return build_atomically(library, package_id, build)


def song_builder(library: Path, model_dir: Path, tools: Tools) -> Build:
    """The build of a Studio API job: the request gives the title and artist."""

    def build(request: JobRequest, on_stage: OnStage) -> None:
        build_song(
            library,
            model_dir,
            tools,
            source=request.source,
            title=request.title,
            artist=request.artist,
            album=request.album,
            duration_s=request.duration_s,
            on_stage=on_stage,
        )

    return build


def _db(value: float | None) -> str:
    return "-inf" if value is None else f"{value:.1f}"


def _print_report(report: dict) -> None:
    info = device_info()
    print(f"device: {info['device']} (torch {info['torch']}, CUDA {info['cuda']})")
    if "metadata_seconds" in report:
        print(f"{'metadata':<12} {report['metadata_seconds']:>8.1f} s")
    print(f"{'download':<12} {report['download_seconds']:>8.1f} s")
    for step, row in report["steps"].items():
        overlap = "" if row["overlap"] is None else f"  overlap {row['overlap']}"
        print(
            f"{step:<12} {row['seconds']:>8.1f} s  load {row['load_seconds']} s  "
            f"peak VRAM {row['peak_vram_mb']} MB{overlap}  {row['model']}"
        )
    for step, row in report["steps"].items():
        if row["reached_threshold"]:
            print(
                f"warning: {step} reached the normalization threshold at headroom "
                f"{HEADROOM} (input peak {row['input_peak']}, kept output peak "
                f"{row['output_peak']}); audio-separator may have rescaled it, "
                "so its stems are off in level"
            )
    lyrics = report["lyrics"]
    found = "none" if lyrics is None else lyrics.file
    print(f"{'lyrics':<12} {report['lyrics_seconds']:>8.1f} s  {found}")
    print(f"{'write':<12} {report['write_seconds']:>8.1f} s")
    print(
        f"{'total':<12} {report['total_seconds']:>8.1f} s "
        f"for {report['audio_s']:.1f} s of audio"
    )
    print(f"gain_db: {report['gain_db']}")
    null = report["null_test"]
    if null is not None:
        print(
            f"null test: max abs error {null['max_abs_error']:.3g} "
            f"({_db(null['max_abs_error_dbfs'])} dBFS, "
            f"{null['max_abs_error_lsb']:.2f} LSB), residual RMS "
            f"{_db(null['residual_rms_dbfs'])} dBFS, "
            f"mix RMS {_db(null['mix_rms_dbfs'])} dBFS"
        )
