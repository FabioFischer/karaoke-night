"""The Stage library on disk: one folder per package, named by its song id.

The folder is the library: there is no database next to it. Folders starting
with "." are downloads or deletions in flight and are never listed.
"""

import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from core.manifest import Manifest
from pydantic import ValidationError

log = logging.getLogger(__name__)

# The shape of the Studio's song_id: slugs of a-z, 0-9 and "-" joined by "--",
# then 6 hex digits. A song id becomes a folder name the Stage creates and
# deletes, so anything else (dots, separators, drives, upper case, device
# names like "con") is refused. Used with fullmatch.
_SONG_ID = r"[a-z0-9-]*--[a-z0-9-]*--[0-9a-f]{6}"
_SONG_ID_RE = re.compile(_SONG_ID)
# .<song_id>.partial, a download in progress, kept between syncs to resume,
# and .<song_id>.<8 hex>.deleting, the trash folder of delete. Used with fullmatch.
_PARTIAL = re.compile(rf"\.({_SONG_ID})\.partial")
_TRASH = re.compile(rf"\.{_SONG_ID}\.[0-9a-f]{{8}}\.deleting")


@dataclass(frozen=True)
class Song:
    folder: Path
    manifest_bytes: bytes
    manifest: Manifest


class Busy(Exception):
    """The folder could not be moved, so nothing of it was deleted."""


def is_song_id(value: str) -> bool:
    return _SONG_ID_RE.fullmatch(value) is not None


def partial_folder(library: Path, song_id: str) -> Path:
    return library / f".{song_id}.partial"


def scan(library: Path) -> dict[str, Song]:
    """Every package in library, by folder name, with manifest.json as stored."""
    if not library.is_dir():
        return {}
    songs = {}
    for folder in sorted(library.iterdir()):
        path = folder / "manifest.json"
        if folder.name.startswith(".") or not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            manifest = Manifest.model_validate_json(raw)
        except ValidationError as error:
            log.warning(
                "%s is not listed: invalid manifest.json (%s)",
                folder.name,
                describe(error),
            )
            continue
        songs[folder.name] = Song(folder, raw, manifest)
    return songs


def describe(error: ValidationError) -> str:
    """The first error of a validation, on one line."""
    first = error.errors(include_url=False)[0]
    where = ".".join(str(part) for part in first["loc"])
    return f"{where}: {first['msg']}" if where else first["msg"]


def move_to_trash(folder: Path) -> Path:
    """Rename folder out of the listing and return its new path.

    On Windows a folder holding an open file cannot be renamed (REAPER keeps
    the stems of the loaded song open), so a folder in use stays whole.
    """
    trash = folder.with_name(f".{folder.name}.{uuid.uuid4().hex[:8]}.deleting")
    try:
        folder.rename(trash)
    except PermissionError as error:
        raise Busy(folder.name) from error
    return trash


def delete(folder: Path) -> None:
    """Move the folder out of the listing, then remove it."""
    shutil.rmtree(move_to_trash(folder), ignore_errors=True)


def remove_leftovers(library: Path, listed: set[str]) -> None:
    """Remove the trash folders and the partials of songs not in listed.

    No other folder is touched.
    """
    for folder in library.iterdir():
        partial = _PARTIAL.fullmatch(folder.name)
        stale = partial is not None and partial[1] not in listed
        if (stale or _TRASH.fullmatch(folder.name)) and folder.is_dir():
            _remove_leftover(folder)


def _remove_leftover(folder: Path) -> None:
    errors: list[BaseException] = []
    shutil.rmtree(folder, onexc=lambda func, path, error: errors.append(error))
    if errors:
        log.warning(
            "could not fully remove the leftover folder %s (%d errors, first: %s)",
            folder.name,
            len(errors),
            errors[0],
        )
