import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from core.manifest import Manifest, YouTubeMusicSource
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures"
WITH_LYRICS = FIXTURES / "manifest_with_lyrics.json"
WITHOUT_LYRICS = FIXTURES / "manifest_without_lyrics.json"


def manifest_data(path: Path = WITH_LYRICS) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_valid_full_manifest() -> None:
    manifest = Manifest.model_validate(manifest_data())

    assert manifest.schema_version == 1
    assert manifest.pipeline_version == 1
    assert manifest.id == "song-title--artist-name--a1b2c3"
    assert manifest.title == "Song Title"
    assert manifest.artist == "Artist Name"
    assert manifest.duration_s == 245.3
    assert manifest.source == YouTubeMusicSource(
        type="youtube_music", video_id="XXXXXXXXXXX"
    )
    assert manifest.gain_db == -2.5
    assert list(manifest.stems) == [
        "lead",
        "backing",
        "drums",
        "bass",
        "guitar",
        "piano",
        "other",
    ]
    assert manifest.stems["lead"].file == "stems/lead.flac"
    assert manifest.stems["lead"].sha256 == "0123456789abcdef" * 4
    assert manifest.lyrics is not None
    assert manifest.lyrics.provider == "lrclib"
    assert manifest.lyrics.file == "lyrics.lrc"
    assert manifest.lyrics.synced is True
    assert manifest.lyrics.offset_ms == 0
    assert manifest.models.vocals == "vocals-model.ckpt"
    assert manifest.models.karaoke == "karaoke-model.ckpt"
    assert manifest.models.instrumental == "htdemucs_6s"
    assert manifest.created_at == datetime(2026, 11, 4, 20, 0, 0, tzinfo=UTC)


def test_valid_manifest_without_lyrics() -> None:
    manifest = Manifest.model_validate(manifest_data(WITHOUT_LYRICS))

    assert manifest.lyrics is None


def test_lyrics_offset_defaults_to_zero() -> None:
    data = manifest_data()
    del data["lyrics"]["offset_ms"]

    manifest = Manifest.model_validate(data)

    assert manifest.lyrics is not None
    assert manifest.lyrics.offset_ms == 0


def test_unknown_fields_are_accepted_and_dropped() -> None:
    data = manifest_data()
    data["melody"] = {"file": "melody.json"}
    data["stems"]["lead"]["peak_dbfs"] = -0.3
    data["source"]["channel"] = "Artist Name"

    manifest = Manifest.model_validate(data)
    written = manifest.model_dump(mode="json", by_alias=True)

    assert "melody" not in written
    assert "peak_dbfs" not in written["stems"]["lead"]
    assert "channel" not in written["source"]


@pytest.mark.parametrize("fixture", [WITH_LYRICS, WITHOUT_LYRICS])
def test_round_trip_matches_the_s2_format(fixture: Path, tmp_path: Path) -> None:
    loaded = Manifest.load(fixture)
    out = tmp_path / "manifest.json"

    loaded.write(out)

    assert Manifest.load(out) == loaded
    assert out.read_bytes() == fixture.read_text(encoding="utf-8").encode("utf-8")


def test_utc_created_at_is_written_with_a_z_suffix(tmp_path: Path) -> None:
    data = manifest_data()
    data["created_at"] = "2026-11-04T20:00:00+00:00"
    out = tmp_path / "manifest.json"

    Manifest.model_validate(data).write(out)

    assert json.loads(out.read_text(encoding="utf-8"))["created_at"] == (
        "2026-11-04T20:00:00Z"
    )


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "pipeline_version",
        "id",
        "title",
        "artist",
        "duration_s",
        "source",
        "gain_db",
        "stems",
        "models",
        "created_at",
    ],
)
def test_missing_required_field_is_rejected(field: str) -> None:
    data = manifest_data()
    del data[field]

    with pytest.raises(ValidationError):
        Manifest.model_validate(data)


def test_schema_2_is_rejected() -> None:
    data = manifest_data()
    data["schema"] = 2

    with pytest.raises(ValidationError):
        Manifest.model_validate(data)


def test_unknown_source_type_is_rejected() -> None:
    data = manifest_data()
    data["source"] = {"type": "unknown_service", "video_id": "XXXXXXXXXXX"}

    with pytest.raises(ValidationError):
        Manifest.model_validate(data)


INVALID_PATHS = [
    "",
    "/stems/lead.flac",
    "C:\\stems\\lead.flac",
    "C:/stems/lead.flac",
    "C:stems\\lead.flac",
    "\\stems\\lead.flac",
    "\\\\server\\share\\lead.flac",
    "//server/share/lead.flac",
    "../lead.flac",
    "..\\lead.flac",
    "stems/../../lead.flac",
    "stems\\..\\..\\lead.flac",
    "stems/..",
]


@pytest.mark.parametrize("path", INVALID_PATHS)
def test_stem_file_outside_the_package_is_rejected(path: str) -> None:
    data = manifest_data()
    data["stems"]["lead"]["file"] = path

    with pytest.raises(ValidationError):
        Manifest.model_validate(data)


@pytest.mark.parametrize("path", INVALID_PATHS)
def test_lyrics_file_outside_the_package_is_rejected(path: str) -> None:
    data = manifest_data()
    data["lyrics"]["file"] = path

    with pytest.raises(ValidationError):
        Manifest.model_validate(data)


@pytest.mark.parametrize(
    "sha256",
    [
        "0123456789abcdef" * 4 + "0",
        "0123456789abcdef" * 3,
        "0123456789ABCDEF" * 4,
        "0123456789abcdeg" * 4,
    ],
    ids=["too-long", "short", "uppercase", "not-hex"],
)
def test_invalid_sha256_is_rejected(sha256: str) -> None:
    data = manifest_data()
    data["stems"]["lead"]["sha256"] = sha256

    with pytest.raises(ValidationError):
        Manifest.model_validate(data)


@pytest.mark.parametrize("created_at", ["not a date", "2026-13-01T00:00:00Z"])
def test_invalid_created_at_is_rejected(created_at: str) -> None:
    data = manifest_data()
    data["created_at"] = created_at

    with pytest.raises(ValidationError):
        Manifest.model_validate(data)
