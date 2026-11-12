"""The song package manifest (`manifest.json`), described in docs/PLAN.md section 5.2."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def _check_package_path(value: str) -> str:
    if not value:
        raise ValueError("path is empty")
    if value[0] in "/\\" or _WINDOWS_DRIVE.match(value):
        raise ValueError("path must be relative to the package")
    if ".." in re.split(r"[/\\]", value):
        raise ValueError("path must stay inside the package")
    return value


PackagePath = Annotated[str, AfterValidator(_check_package_path)]
"""A file path relative to the package directory that cannot leave it."""

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Model(BaseModel):
    # Ignored, not rejected: an older Stage must keep reading a newer manifest.
    model_config = ConfigDict(extra="ignore")


class YouTubeMusicSource(_Model):
    type: Literal["youtube_music"]
    video_id: str


Source = Annotated[YouTubeMusicSource, Field(discriminator="type")]


class Stem(_Model):
    file: PackagePath
    sha256: Sha256


class Lyrics(_Model):
    provider: str
    file: PackagePath
    synced: bool
    offset_ms: int = 0


class Models(_Model):
    vocals: str
    karaoke: str
    instrumental: str


class Manifest(_Model):
    # `schema` is a deprecated BaseModel method, so the field takes another name.
    schema_version: Literal[1] = Field(alias="schema")
    pipeline_version: int
    id: str
    title: str
    artist: str
    duration_s: float
    source: Source
    gain_db: float
    stems: dict[str, Stem]
    lyrics: Lyrics | None = None
    models: Models
    created_at: datetime

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate_json(path.read_bytes())

    def write(self, path: Path) -> None:
        """Write in the format the S2 spike wrote: indent 2, trailing newline, utf-8, LF."""
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        path.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))
