"""Sample math, the ffmpeg conversion and the null test."""

import math
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from core.manifest import Manifest

SAMPLE_RATE = 44_100
# The largest value a 16-bit sample holds is 32767 / 32768 of full scale.
PCM16_CEILING = 32_767 / 32_768


def align(audio: np.ndarray, n_frames: int) -> np.ndarray:
    """Trim or zero-pad (frames, channels) audio to n_frames."""
    if len(audio) >= n_frames:
        return audio[:n_frames]
    pad = np.zeros((n_frames - len(audio),) + audio.shape[1:], dtype=audio.dtype)
    return np.concatenate([audio, pad])


def peak(audio: np.ndarray) -> float:
    return float(np.max(np.abs(audio)))


def fit_headroom(
    stems: dict[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], float]:
    """Scale every stem by one factor when any would clip in 16 bits.

    Returns the stems and gain_db, the gain that restores the original level:
    stems x 10^(gain_db / 20) still add up to the mix. gain_db is rounded up to
    3 decimals and the stems are scaled by that rounded value, so the manifest
    holds the exact gain. Without clipping the stems come back unchanged with
    gain_db 0.
    """
    loudest = max(peak(audio) for audio in stems.values())
    if loudest <= PCM16_CEILING:
        return stems, 0.0
    gain_db = math.ceil(20 * math.log10(loudest / PCM16_CEILING) * 1_000) / 1_000
    factor = 10 ** (-gain_db / 20)
    scaled = {
        name: (audio * factor).astype(audio.dtype) for name, audio in stems.items()
    }
    return scaled, gain_db


def to_pcm16(audio: np.ndarray) -> np.ndarray:
    """Round to 16-bit samples, with 32768 as full scale like soundfile uses on read."""
    return np.clip(np.round(audio * 32_768), -32_768, 32_767).astype(np.int16)


def dbfs(value: float) -> float | None:
    return 20 * math.log10(value) if value > 0 else None


def ffmpeg_command(source: Path, target: Path) -> list[str]:
    """To a 44.1 kHz 16-bit stereo WAV, the mix every package is made from."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "2",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(target),
    ]


def convert(source: Path, target: Path) -> None:
    subprocess.run(ffmpeg_command(source, target), check=True)


def null_test(manifest_path: Path, mix_path: Path) -> dict:
    """Sum the written stems, apply gain_db and compare with the mix."""
    manifest = Manifest.load(manifest_path)
    mix, _ = sf.read(mix_path, dtype="float64", always_2d=True)
    total = np.zeros_like(mix)
    for stem in manifest.stems.values():
        audio, _ = sf.read(
            manifest_path.parent / stem.file, dtype="float64", always_2d=True
        )
        total += audio
    error = total * 10 ** (manifest.gain_db / 20) - mix
    max_abs = float(np.max(np.abs(error)))
    rms = float(np.sqrt(np.mean(error**2)))
    return {
        "max_abs_error": max_abs,
        "max_abs_error_dbfs": dbfs(max_abs),
        "max_abs_error_lsb": max_abs * 32_768,
        "residual_rms_dbfs": dbfs(rms),
        "mix_rms_dbfs": dbfs(float(np.sqrt(np.mean(mix**2)))),
    }
