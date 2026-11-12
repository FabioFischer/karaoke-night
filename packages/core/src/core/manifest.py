"""The song package manifest (`manifest.json`), described in docs/PLAN.md section 5.2."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

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

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


def _to_utc_seconds(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(microsecond=0)


UtcSeconds = Annotated[AwareDatetime, AfterValidator(_to_utc_seconds)]
"""Stored in UTC with whole seconds, so it always writes as `YYYY-MM-DDTHH:MM:SSZ`."""


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
    model_config = ConfigDict(validate_by_name=True)

    # `schema` is a deprecated BaseModel method, so the field takes another name.
    schema_version: Literal[1] = Field(alias="schema")
    pipeline_version: int
    id: str
    title: str
    artist: str
    duration_s: FiniteFloat
    source: Source
    gain_db: FiniteFloat
    stems: dict[str, Stem]
    lyrics: Lyrics | None = None
    models: Models
    created_at: UtcSeconds

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate_json(path.read_bytes())

    def write(self, path: Path) -> None:
        """Write in the format the S2 spike wrote: indent 2, trailing newline, utf-8, LF."""
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        path.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))
