"""The master library on disk: one folder per package, named by its song id.

Folders starting with "." are builds or deletions in flight and are never
listed. Callers reach a package only through scan(), never through a path
built from outside input.
"""

import hashlib
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from core.manifest import Manifest
from pydantic import ValidationError

log = logging.getLogger(__name__)

# .<song_id>.<random>.tmp, the build folder of build_atomically, and
# .<song_id>.<random>.deleting, the trash folder of delete, where <random> is
# exactly 8 characters of a-z, 0-9 and _: tempfile.mkdtemp's random names, and
# the 8 hex digits of a uuid4. Used with fullmatch.
_LEFTOVER = re.compile(r"\..+\.[a-z0-9_]{8}\.(tmp|deleting)")

# The hash of the invalid manifest.json last warned about, by path, so a broken
# manifest is warned about once, not on every scan. A manifest that loads again
# is dropped, so breaking it again warns again.
_warned: dict[Path, bytes] = {}


@dataclass(frozen=True)
class Package:
    folder: Path
    manifest_bytes: bytes
    manifest: Manifest

    def files(self) -> set[str]:
        """The paths the manifest lists: the stems and the lyrics."""
        files = {stem.file for stem in self.manifest.stems.values()}
        if self.manifest.lyrics is not None:
            files.add(self.manifest.lyrics.file)
        return files


class Busy(Exception):
    """The package folder could not be moved, so nothing of it was deleted."""


def scan(library: Path) -> dict[str, Package]:
    """Every package in library, by song id."""
    if not library.is_dir():
        return {}
    packages = {}
    for folder in sorted(library.iterdir()):
        path = folder / "manifest.json"
        if folder.name.startswith(".") or not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            manifest = Manifest.model_validate_json(raw)
        except ValidationError as error:
            digest = hashlib.sha256(raw).digest()
            if _warned.get(path) != digest:
                _warned[path] = digest
                _warn_invalid(folder.name, error)
            continue
        _warned.pop(path, None)
        packages[manifest.id] = Package(folder, raw, manifest)
    return packages


def _warn_invalid(name: str, error: ValidationError) -> None:
    first = error.errors(include_url=False)[0]
    where = ".".join(str(part) for part in first["loc"])
    reason = f"{where}: {first['msg']}" if where else first["msg"]
    log.warning("%s is not listed: invalid manifest.json (%s)", name, reason)


def remove_leftovers(library: Path) -> None:
    """Remove the builds and deletions a stopped process left in library.

    This also removes the build of a `studio process` running on library now.
    """
    if not library.is_dir():
        return
    for folder in library.iterdir():
        if _LEFTOVER.fullmatch(folder.name) and folder.is_dir():
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
    else:
        log.info("removed the leftover folder %s", folder.name)


def find_video(library: Path, video_id: str) -> str | None:
    """The song id of a package made from video_id, if there is one."""
    for package_id, package in scan(library).items():
        source = package.manifest.source
        if source.type == "youtube_music" and source.video_id == video_id:
            return package_id
    return None


def delete(package: Package) -> None:
    """Move the folder out of the listing, then remove it.

    On Windows a folder holding an open file cannot be renamed, so a package
    in use is left whole instead of half deleted.
    """
    trash = package.folder.with_name(
        f".{package.folder.name}.{uuid.uuid4().hex[:8]}.deleting"
    )
    try:
        package.folder.rename(trash)
    except PermissionError as error:
        raise Busy(package.folder.name) from error
    shutil.rmtree(trash, ignore_errors=True)
