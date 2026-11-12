"""The song package of docs/PLAN.md section 5.2, written to the library."""

import hashlib
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf
from core.manifest import Lyrics, Manifest, Models, Source, Stem

from studio.audio import SAMPLE_RATE, to_pcm16
from studio.lyrics import FoundLyrics
from studio.separation import STEMS

PIPELINE_VERSION = 1


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
    temp = Path(tempfile.mkdtemp(prefix=f".{package_id}.", suffix=".tmp", dir=library))
    try:
        build(temp)
        final = library / package_id
        temp.rename(final)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return final
