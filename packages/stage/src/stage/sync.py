"""The library sync: the Stage mirrors the Studio library (docs/PLAN.md section 5.6).

The Studio is the source of truth. A song it lists and the Stage lacks, or
holds in another version, is downloaded; a song the Stage holds and the
Studio no longer lists is removed, even when that leaves nothing.
"""

import hashlib
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from core.manifest import Manifest
from core.studio_api import SongSummary
from pydantic import ValidationError

from stage import library as lib
from stage.client import StudioClient, StudioError

log = logging.getLogger(__name__)

Action = Literal["added", "updated", "removed", "busy", "failed"]

BUSY_REMOVE = "a file of the song is open; it stays until a later sync"
BUSY_UPDATE = (
    "a file of the song is open; the old version stays and the new one "
    "is installed on a later sync"
)


@dataclass(frozen=True)
class Outcome:
    song_id: str
    action: Action
    reason: str | None = None


@dataclass
class Summary:
    outcomes: list[Outcome] = field(default_factory=list)
    unchanged: int = 0
    skipped: int = 0
    bytes_downloaded: int = 0


def sync(
    library: Path,
    studio: StudioClient,
    on_outcome: Callable[[Outcome], None] = lambda outcome: None,
) -> Summary:
    """Mirror the Studio library into library; on_outcome sees each song acted on.

    A StudioError on the listing propagates before anything on disk changes.
    """
    listing = studio.songs()
    summary = Summary()

    def record(outcome: Outcome) -> None:
        summary.outcomes.append(outcome)
        on_outcome(outcome)

    wanted: dict[str, SongSummary] = {}
    for song in listing:
        if lib.is_song_id(song.id):
            wanted[song.id] = song
        else:
            summary.skipped += 1
            log.warning(
                "skipped a song the Studio lists under an id the Stage does not "
                "accept: %r",
                song.id,
            )

    library.mkdir(parents=True, exist_ok=True)
    lib.remove_leftovers(library, set(wanted))
    local = lib.scan(library)
    # Every listed id counts, so a skipped id never removes a folder of that name.
    listed = {song.id for song in listing}
    for song_id, song in local.items():
        if song_id not in listed:
            record(_remove(song))
    for song_id, song in wanted.items():
        current = local.get(song_id)
        if current is not None and _same_version(current.manifest, song):
            summary.unchanged += 1
        else:
            record(_install(library, studio, song, summary))
    return summary


def _same_version(manifest: Manifest, song: SongSummary) -> bool:
    return (
        manifest.created_at == song.created_at
        and manifest.pipeline_version == song.pipeline_version
    )


def _remove(song: lib.Song) -> Outcome:
    try:
        lib.delete(song.folder)
    except lib.Busy:
        return Outcome(song.folder.name, "busy", BUSY_REMOVE)
    except OSError as error:
        return Outcome(song.folder.name, "failed", str(error))
    return Outcome(song.folder.name, "removed")


def _install(
    library: Path, studio: StudioClient, song: SongSummary, summary: Summary
) -> Outcome:
    """Download the song into its partial folder, then move it into place.

    The old version is replaced only once the new one is complete.
    """
    try:
        errors = _download(library, studio, song, summary)
    except StudioError as error:
        return Outcome(song.id, "failed", str(error))
    except ValidationError as error:
        reason = lib.describe(error)
        return Outcome(
            song.id, "failed", f"the Studio sent an invalid manifest ({reason})"
        )
    except OSError as error:
        return Outcome(song.id, "failed", str(error))
    if errors:
        return Outcome(song.id, "failed", "; ".join(errors))

    partial = lib.partial_folder(library, song.id)
    target = library / song.id
    try:
        if not target.exists():
            partial.rename(target)
            return Outcome(song.id, "added")
        trash = lib.move_to_trash(target)
        partial.rename(target)
    except lib.Busy:
        return Outcome(song.id, "busy", BUSY_UPDATE)
    except OSError as error:
        return Outcome(song.id, "failed", str(error))
    shutil.rmtree(trash, ignore_errors=True)
    return Outcome(song.id, "updated")


def _download(
    library: Path, studio: StudioClient, song: SongSummary, summary: Summary
) -> list[str]:
    """Fill the partial folder of song and return the reasons it is not complete."""
    partial = lib.partial_folder(library, song.id)
    manifest = _kept_manifest(partial, song)
    if manifest is None:
        shutil.rmtree(partial, ignore_errors=True)
        raw = studio.manifest(song.id)
        summary.bytes_downloaded += len(raw)
        manifest = Manifest.model_validate_json(raw)
        partial.mkdir()
        # As downloaded: writing it through the model would drop fields it does not know.
        (partial / "manifest.json").write_bytes(raw)

    errors = []
    for stem in manifest.stems.values():
        target = partial / stem.file
        if target.is_file() and _sha256(target) == stem.sha256:
            continue
        try:
            summary.bytes_downloaded += _fetch(studio, song.id, stem.file, target)
        except StudioError as error:
            errors.append(f"{stem.file}: {error}")
            continue
        if _sha256(target) != stem.sha256:
            target.unlink()
            errors.append(
                f"{stem.file} does not match the sha256 of the manifest; "
                "it is downloaded again on the next sync"
            )
    if manifest.lyrics is not None:
        target = partial / manifest.lyrics.file
        try:
            summary.bytes_downloaded += _fetch(
                studio, song.id, manifest.lyrics.file, target
            )
        except StudioError as error:
            errors.append(f"{manifest.lyrics.file}: {error}")
    return errors


def _kept_manifest(partial: Path, song: SongSummary) -> Manifest | None:
    """The manifest a partial folder holds, if it is of the version listed."""
    path = partial / "manifest.json"
    if not path.is_file():
        return None
    try:
        manifest = Manifest.model_validate_json(path.read_bytes())
    except ValidationError:
        return None
    return manifest if _same_version(manifest, song) else None


def _fetch(studio: StudioClient, song_id: str, path: str, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    return studio.download(song_id, path, target)


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()
