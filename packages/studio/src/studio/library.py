"""The master library on disk: one folder per package, named by its song id.

Folders starting with "." are builds or deletions in flight and are never
listed. Callers reach a package only through scan(), never through a path
built from outside input.
"""

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from core.manifest import Manifest


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
        manifest = Manifest.model_validate_json(raw)
        packages[manifest.id] = Package(folder, raw, manifest)
    return packages


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
