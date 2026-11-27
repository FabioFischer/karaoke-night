"""The song package of docs/PLAN.md section 5.2, written to the library."""

import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf
from core.manifest import Lyrics, Manifest, Melody, Models, Source, Stem

from studio.audio import SAMPLE_RATE, to_pcm16
from studio.library import Package, move_to_trash
from studio.lyrics import FoundLyrics
from studio.separation import STEMS

PIPELINE_VERSION = 2
MELODY_FILE = "melody.json"


def slug(text: str) -> str:
    """Only a-z, 0-9 and "-", so a song id is always one plain folder name."""
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def song_id(title: str, artist: str, source_key: str) -> str:
    """The section 5.2 shape: song-title--artist-name--a1b2c3.

    The suffix hashes source_key: the video_id, or the file's sha256.
    """
    suffix = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:6]
    return f"{slug(title)}--{slug(artist)}--{suffix}"


def sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def source_key(source: Source) -> str:
    return source.video_id if source.type == "youtube_music" else source.sha256


def write_melody(out_dir: Path, document: dict) -> Melody:
    """Write the melody document as the manifest is written: indent 2, utf-8, LF."""
    path = out_dir / MELODY_FILE
    path.write_bytes((json.dumps(document, indent=2) + "\n").encode("utf-8"))
    return Melody(file=MELODY_FILE, sha256=sha256_file(path))


def write_package(
    out_dir: Path,
    stems: dict[str, np.ndarray],
    *,
    title: str,
    artist: str,
    source: Source,
    gain_db: float,
    lyrics: FoundLyrics | None,
    models: dict[str, str],
    melody: dict,
) -> Manifest:
    stems_dir = out_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    entries = {}
    for stem in STEMS:
        relative = f"stems/{stem}.flac"
        sf.write(
            out_dir / relative,
            to_pcm16(stems[stem]),
            SAMPLE_RATE,
            format="FLAC",
            subtype="PCM_16",
        )
        entries[stem] = Stem(file=relative, sha256=sha256_file(out_dir / relative))
    lyrics_entry = None
    if lyrics is not None:
        # Bytes, not text: text mode would turn LRCLIB's line endings into CRLF on Windows.
        (out_dir / lyrics.file).write_bytes(lyrics.text.encode("utf-8"))
        lyrics_entry = Lyrics(provider="lrclib", file=lyrics.file, synced=lyrics.synced)
    manifest = Manifest(
        schema_version=1,
        pipeline_version=PIPELINE_VERSION,
        id=song_id(title, artist, source_key(source)),
        title=title,
        artist=artist,
        duration_s=round(len(stems[STEMS[0]]) / SAMPLE_RATE, 3),
        source=source,
        gain_db=gain_db,
        stems=entries,
        lyrics=lyrics_entry,
        melody=write_melody(out_dir, melody),
        models=Models(
            vocals=models["vocals"],
            karaoke=models["karaoke"],
            instrumental=models["instruments"],
        ),
        created_at=datetime.now(UTC),
    )
    manifest.write(out_dir / "manifest.json")
    return manifest


def build_atomically(
    library: Path, package_id: str, build: Callable[[Path], None]
) -> Path:
    """Run build in a temporary folder inside library, then rename it to package_id.

    The folder is on the same drive as the library, so the rename is atomic:
    the library holds a finished package or none. On failure it is removed.
    """
    library.mkdir(parents=True, exist_ok=True)
    temp = _build_folder(library, package_id)
    try:
        build(temp)
        final = library / package_id
        temp.rename(final)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return final


def _build_folder(library: Path, package_id: str) -> Path:
    """.<package_id>.<8 random>.tmp, a shape `studio serve` removes as a leftover."""
    return Path(tempfile.mkdtemp(prefix=f".{package_id}.", suffix=".tmp", dir=library))


def upgrade_package(package: Package, analyze: Callable[[dict], dict]) -> None:
    """Give package the melody of PIPELINE_VERSION, swapped in whole.

    The new version is built next to it from a copy of its stems and lyrics,
    with a manifest that changes only pipeline_version, melody and created_at.
    The old folder then moves to a trash name, the new one takes its place and
    the trash is removed. Busy when the old folder cannot move; if the new one
    cannot take its place, the old one is put back.
    """
    folder, old = package.folder, package.manifest
    temp = _build_folder(folder.parent, folder.name)
    try:
        copied = [stem.file for stem in old.stems.values()]
        if old.lyrics is not None:
            copied.append(old.lyrics.file)
        for path in copied:
            (temp / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / path, temp / path)
        stems = {
            name: sf.read(temp / stem.file, dtype="float32", always_2d=True)[0]
            for name, stem in old.stems.items()
        }
        melody = write_melody(temp, analyze(stems))
        del stems
        changes = {
            "pipeline_version": PIPELINE_VERSION,
            "melody": melody,
            "created_at": datetime.now(UTC),
        }
        Manifest.model_validate(old.model_dump() | changes).write(
            temp / "manifest.json"
        )
        trash = move_to_trash(folder)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    try:
        temp.rename(folder)
    except OSError as error:
        shutil.rmtree(temp, ignore_errors=True)
        _put_back(trash, folder, error)
    shutil.rmtree(trash, ignore_errors=True)


def _put_back(trash: Path, folder: Path, error: OSError) -> None:
    """Rename the old version back after the new one failed to take its place."""
    try:
        trash.rename(folder)
    except OSError as restore_error:
        raise OSError(
            f"{error}; the old version could not be put back ({restore_error}) "
            f"and is kept in {trash.name}, which the next studio serve removes"
        ) from error
    raise OSError(f"{error}; the old version stays") from error
