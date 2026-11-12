import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from core.manifest import FileSource, Manifest, YouTubeMusicSource
from studio.audio import SAMPLE_RATE
from studio.lyrics import FoundLyrics
from studio.package import (
    build_atomically,
    sha256_file,
    slug,
    song_id,
    write_package,
)
from studio.separation import MODELS, STEMS, cascade

SOURCE = YouTubeMusicSource(type="youtube_music", video_id="XXXXXXXXXXX")


def test_song_id_has_the_section_5_2_shape() -> None:
    suffix = hashlib.sha256(b"XXXXXXXXXXX").hexdigest()[:6]

    assert song_id("Song Title", "Artist Name", "XXXXXXXXXXX") == (
        f"song-title--artist-name--{suffix}"
    )


def test_different_sources_get_different_ids() -> None:
    assert song_id("A", "B", "video-one__") != song_id("A", "B", "video-two__")


def test_slug_drops_accents_and_punctuation() -> None:
    assert slug("  Cancao, Acentuada! (Ao Vivo) ") == "cancao-acentuada-ao-vivo"
    assert slug("Ca\u00e7\u00e3o \u00d1") == "cacao-n"


@pytest.mark.parametrize(
    "text",
    ["../../outside", "..\\..\\outside", "C:\\Windows", "/etc/passwd", "a/b\\c:d*e?"],
)
def test_slug_keeps_only_lowercase_letters_digits_and_hyphens(text: str) -> None:
    value = slug(text)

    assert value
    assert set(value) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")


def write_test_package(
    out: Path, mix: np.ndarray, fake, lyrics: FoundLyrics | None = None
) -> Manifest:
    stems = cascade(mix, fake, MODELS)
    return write_package(
        out,
        stems,
        title="Song Title",
        artist="Artist Name",
        source=SOURCE,
        gain_db=1.25,
        lyrics=lyrics,
        models=MODELS,
    )


def test_written_package_loads_and_every_sha256_matches(
    tmp_path: Path, mix, fake_separator
) -> None:
    manifest = write_test_package(tmp_path, mix, fake_separator())

    loaded = Manifest.load(tmp_path / "manifest.json")

    assert loaded == manifest
    assert loaded.id == song_id("Song Title", "Artist Name", "XXXXXXXXXXX")
    assert loaded.source == SOURCE
    assert loaded.duration_s == round(len(mix) / SAMPLE_RATE, 3)
    assert loaded.gain_db == 1.25
    assert loaded.lyrics is None
    assert loaded.models.model_dump() == {
        "vocals": MODELS["vocals"],
        "karaoke": MODELS["karaoke"],
        "instrumental": MODELS["instruments"],
    }
    assert list(loaded.stems) == list(STEMS)
    for name, stem in loaded.stems.items():
        assert stem.file == f"stems/{name}.flac"
        assert stem.sha256 == sha256_file(tmp_path / stem.file)
        info = sf.info(tmp_path / stem.file)
        assert (info.format, info.subtype) == ("FLAC", "PCM_16")
        assert (info.samplerate, info.channels) == (SAMPLE_RATE, 2)


def test_written_package_records_the_lyrics(
    tmp_path: Path, mix, fake_separator
) -> None:
    lyrics = FoundLyrics(file="lyrics.lrc", text="[00:01.00] Line one", synced=True)

    manifest = write_test_package(tmp_path, mix, fake_separator(), lyrics)

    assert manifest.lyrics is not None
    assert manifest.lyrics.model_dump() == {
        "provider": "lrclib",
        "file": "lyrics.lrc",
        "synced": True,
        "offset_ms": 0,
    }
    assert (tmp_path / "lyrics.lrc").read_bytes() == b"[00:01.00] Line one"


@pytest.mark.parametrize(
    "text",
    ["[00:01.00] Line one\n[00:02.00] Line two\n", "Line one\r\nLine two\r\n"],
    ids=["lf", "crlf"],
)
def test_lyrics_are_written_byte_for_byte(
    tmp_path: Path, mix, fake_separator, text: str
) -> None:
    lyrics = FoundLyrics(file="lyrics.lrc", text=text, synced=True)

    write_test_package(tmp_path, mix, fake_separator(), lyrics)

    assert (tmp_path / "lyrics.lrc").read_bytes() == text.encode("utf-8")


def test_written_package_takes_a_file_source(
    tmp_path: Path, mix, fake_separator
) -> None:
    source = FileSource(type="file", sha256="ab" * 32)
    stems = cascade(mix, fake_separator(), MODELS)

    manifest = write_package(
        tmp_path,
        stems,
        title="Song Title",
        artist="Artist Name",
        source=source,
        gain_db=0.0,
        lyrics=None,
        models=MODELS,
    )

    assert manifest.source == source
    assert manifest.id == song_id("Song Title", "Artist Name", "ab" * 32)


def test_build_atomically_renames_the_finished_package(tmp_path: Path) -> None:
    library = tmp_path / "library"

    def build(out: Path) -> None:
        (out / "manifest.json").write_text("{}", encoding="utf-8")

    path = build_atomically(library, "song-id", build)

    assert path == library / "song-id"
    assert [p.name for p in library.iterdir()] == ["song-id"]
    assert (path / "manifest.json").read_text(encoding="utf-8") == "{}"


def test_build_atomically_removes_the_temporary_dir_on_failure(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"

    def build(out: Path) -> None:
        (out / "stems").mkdir()
        (out / "stems" / "lead.flac").write_bytes(b"partial")
        raise RuntimeError("model crashed")

    with pytest.raises(RuntimeError, match="model crashed"):
        build_atomically(library, "song-id", build)

    assert list(library.iterdir()) == []
