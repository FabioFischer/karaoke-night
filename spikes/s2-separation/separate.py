# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "audio-separator[gpu]==0.48.0",
#     "audioread",
#     "numpy",
#     "soundfile",
#     "torch",
#     "yt-dlp[default]",
# ]
#
# [tool.uv.sources]
# torch = [{ index = "pytorch-cu130", marker = "sys_platform == 'win32' or sys_platform == 'linux'" }]
#
# [[tool.uv.index]]
# name = "pytorch-cu130"
# url = "https://download.pytorch.org/whl/cu130"
# explicit = true
# ///
"""Download a song and split it into 7 stems for spike S2.

Usage:
  uv run spikes/s2-separation/separate.py download <video_id> [--out DIR]
  uv run spikes/s2-separation/separate.py separate <mix.wav> --combo A|B|C [--vocals-model F]
      [--karaoke-model F] [--instruments-model F] [--model-dir DIR] [--out DIR]
  uv run spikes/s2-separation/separate.py null <manifest.json> [--mix mix.wav]

The cascade follows docs/PLAN.md section 4: each step keeps the model's "vocals"
(or instrument) output and computes the rest as a remainder, so the 7 stems add
up to the mix. The package follows the layout of section 5.2 and loads with the
S3 driver as is.

audio-separator, torch and yt-dlp are imported only inside the commands that use
them, so the tests run with numpy and soundfile alone.
"""

import argparse
import gc
import hashlib
import json
import logging
import math
import re
import subprocess
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 44_100
# Same names and order as the S3 listener and driver.
STEMS = ("lead", "backing", "drums", "bass", "guitar", "piano", "other")
INSTRUMENT_STEMS = ("drums", "bass", "guitar", "piano")
# The largest value a 16-bit sample holds is 32767 / 32768 of full scale.
PCM16_CEILING = 32_767 / 32_768

COMBOS = {
    "A": {
        "vocals": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "karaoke": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
        "instruments": "htdemucs_6s.yaml",
    },
    "B": {
        "vocals": "vocals_mel_band_roformer.ckpt",
        "karaoke": "mel_band_roformer_karaoke_becruily.ckpt",
        "instruments": "htdemucs_6s.yaml",
    },
    "C": {
        "vocals": "vocals_mel_band_roformer.ckpt",
        "karaoke": "bs_roformer_karaoke_frazer_becruily.ckpt",
        "instruments": "htdemucs_6s.yaml",
    },
}

SPIKE_DIR = Path(__file__).resolve().parent
LIBRARY = SPIKE_DIR / "library"
DEFAULT_MODEL_DIR = Path.home() / ".cache" / "audio-separator-models"


# Audio math ---------------------------------------------------------------

def align(audio: np.ndarray, n_frames: int) -> np.ndarray:
    """Trim or zero-pad (frames, channels) audio to n_frames."""
    if len(audio) >= n_frames:
        return audio[:n_frames]
    pad = np.zeros((n_frames - len(audio),) + audio.shape[1:], dtype=audio.dtype)
    return np.concatenate([audio, pad])


def pick(outputs: dict[str, np.ndarray], stem: str, model: str) -> np.ndarray:
    """The output named stem (case does not matter). Model outputs differ in order, not in name."""
    by_name = {name.lower(): audio for name, audio in outputs.items()}
    if stem not in by_name:
        raise KeyError(f"{model} has no {stem!r} output; it gave: {', '.join(sorted(by_name))}")
    return by_name[stem]


def cascade(mix: np.ndarray, run, models: dict[str, str]) -> dict[str, np.ndarray]:
    """Split mix into the 7 stems.

    run(step, model_file, audio) returns the model's outputs by stem name, as
    (frames, channels) float arrays. Each model output is aligned to the mix
    length before it is subtracted.
    """
    n = len(mix)
    vocals = align(pick(run("vocals", models["vocals"], mix), "vocals", models["vocals"]), n)
    instrumental = mix - vocals
    lead = align(pick(run("karaoke", models["karaoke"], vocals), "vocals", models["karaoke"]), n)
    backing = vocals - lead
    outputs = run("instruments", models["instruments"], instrumental)
    parts = {stem: align(pick(outputs, stem, models["instruments"]), n) for stem in INSTRUMENT_STEMS}
    other = instrumental - sum(parts.values())
    return {"lead": lead, "backing": backing, **parts, "other": other}


def fit_headroom(stems: dict[str, np.ndarray]) -> tuple[dict[str, np.ndarray], float]:
    """Scale every stem by one factor when any would clip in 16 bits.

    Returns the stems and gain_db, the gain that restores the original level:
    stems x 10^(gain_db / 20) still add up to the mix. Without clipping the
    stems come back unchanged with gain_db 0.
    """
    peak = max(float(np.max(np.abs(audio))) for audio in stems.values())
    if peak <= PCM16_CEILING:
        return stems, 0.0
    factor = PCM16_CEILING / peak
    scaled = {name: (audio * factor).astype(audio.dtype) for name, audio in stems.items()}
    return scaled, 20 * math.log10(peak / PCM16_CEILING)


def to_pcm16(audio: np.ndarray) -> np.ndarray:
    """Round to 16-bit samples, with 32768 as full scale like soundfile uses on read."""
    return np.clip(np.round(audio * 32_768), -32_768, 32_767).astype(np.int16)


def dbfs(value: float) -> float | None:
    return 20 * math.log10(value) if value > 0 else None


# Package ------------------------------------------------------------------

def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def song_id(title: str, artist: str, video_id: str) -> str:
    """The section 5.2 shape: song-title--artist-name--a1b2c3, the suffix from the video_id."""
    suffix = hashlib.sha256(video_id.encode("utf-8")).hexdigest()[:6]
    return f"{slug(title)}--{slug(artist)}--{suffix}"


def sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_package(out_dir: Path, stems: dict[str, np.ndarray], source: dict, gain_db: float,
                  models: dict[str, str]) -> dict:
    stems_dir = out_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    entries = {}
    for stem in STEMS:
        relative = f"stems/{stem}.flac"
        sf.write(out_dir / relative, to_pcm16(stems[stem]), SAMPLE_RATE, format="FLAC", subtype="PCM_16")
        entries[stem] = {"file": relative, "sha256": sha256_file(out_dir / relative)}
    n_frames = len(stems[STEMS[0]])
    manifest = {
        "schema": 1,
        "pipeline_version": 1,
        "id": song_id(source["title"], source["artist"], source["video_id"]),
        "title": source["title"],
        "artist": source["artist"],
        "duration_s": round(n_frames / SAMPLE_RATE, 3),
        "source": {"type": "youtube_music", "video_id": source["video_id"]},
        "gain_db": gain_db,
        "stems": entries,
        "models": {"vocals": models["vocals"], "karaoke": models["karaoke"], "instrumental": models["instruments"]},
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def null_test(manifest_path: Path, mix_path: Path) -> dict:
    """Sum the written stems, apply gain_db and compare with the mix."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mix, _ = sf.read(mix_path, dtype="float64", always_2d=True)
    total = np.zeros_like(mix)
    for entry in manifest["stems"].values():
        audio, _ = sf.read(manifest_path.parent / entry["file"], dtype="float64", always_2d=True)
        total += audio
    error = total * 10 ** (manifest["gain_db"] / 20) - mix
    max_abs = float(np.max(np.abs(error)))
    rms = float(np.sqrt(np.mean(error ** 2)))
    return {
        "max_abs_error": max_abs,
        "max_abs_error_dbfs": dbfs(max_abs),
        "max_abs_error_lsb": max_abs * 32_768,
        "residual_rms_dbfs": dbfs(rms),
        "mix_rms_dbfs": dbfs(float(np.sqrt(np.mean(mix ** 2)))),
    }


# GPU measurement ------------------------------------------------------------

def _cuda():
    try:
        import torch
    except ImportError:
        return None
    return torch if torch.cuda.is_available() else None


def reset_vram_peak() -> None:
    torch = _cuda()
    if torch:
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


def peak_vram_mb() -> float | None:
    """Peak memory reserved by torch since the last reset, or None without CUDA."""
    torch = _cuda()
    return round(torch.cuda.max_memory_reserved() / 2**20, 1) if torch else None


def device_info() -> dict:
    try:
        import torch
    except ImportError:
        return {"torch": None, "cuda": None, "device": "cpu"}
    cuda = torch.cuda.is_available()
    return {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "device": torch.cuda.get_device_name(0) if cuda else "cpu",
    }


# audio-separator ------------------------------------------------------------

def stem_from_filename(filename: str, base: str) -> str:
    """audio-separator names each output "<input base>_(<Stem>)_<model>.<ext>"."""
    match = re.match(re.escape(base) + r"_\((.+?)\)_", filename)
    if not match:
        raise ValueError(f"unexpected audio-separator output name {filename!r}")
    return match.group(1)


class AudioSeparatorRunner:
    """Runs one audio-separator model on an array and returns its outputs by stem name.

    The input goes to the model as a 32-bit float WAV. audio-separator writes
    its outputs with the input's subtype when use_soundfile is on, so they stay
    float too, with no 16-bit rounding or clipping between steps.
    normalization_threshold 1.0 (its default is 0.9) only rescales an input or
    output that peaks above full scale; the remainders keep the sum exact even then.
    """

    def __init__(self, model_dir: Path, work_dir: Path):
        from audio_separator.separator import Separator

        self.Separator = Separator
        self.model_dir = model_dir
        self.work_dir = work_dir
        self.last_load_s = None

    def __call__(self, step: str, model_file: str, audio: np.ndarray) -> dict[str, np.ndarray]:
        base = f"{step}_input"
        input_path = self.work_dir / f"{base}.wav"
        out_dir = self.work_dir / step
        sf.write(input_path, audio, SAMPLE_RATE, subtype="FLOAT")
        separator = self.Separator(
            log_level=logging.WARNING,
            model_file_dir=str(self.model_dir),
            output_dir=str(out_dir),
            output_format="WAV",
            use_soundfile=True,
            normalization_threshold=1.0,
            sample_rate=SAMPLE_RATE,
        )
        start = time.perf_counter()
        separator.load_model(model_file)
        self.last_load_s = round(time.perf_counter() - start, 2)
        # separate() returns file names relative to output_dir.
        files = [out_dir / Path(name).name for name in separator.separate(str(input_path))]
        del separator
        outputs = {}
        for path in files:
            data, _ = sf.read(path, dtype="float32", always_2d=True)
            outputs[stem_from_filename(path.name, base).lower()] = data
            path.unlink()
        input_path.unlink()
        return outputs


def timed(run, timings: dict):
    """Wrap run so each step records its wall clock time and peak VRAM in timings."""
    def wrapped(step, model_file, audio):
        reset_vram_peak()
        start = time.perf_counter()
        outputs = run(step, model_file, audio)
        timings[step] = {
            "model": model_file,
            "seconds": round(time.perf_counter() - start, 2),
            "load_seconds": getattr(run, "last_load_s", None),
            "peak_vram_mb": peak_vram_mb(),
        }
        return outputs
    return wrapped


# Commands -------------------------------------------------------------------

def read_source(mix_path: Path) -> dict:
    """The metadata download wrote next to the mix: video_id, title, artist, duration_s."""
    return json.loads((mix_path.parent / "source.json").read_text(encoding="utf-8"))


def separate_song(mix_path: Path, models: dict[str, str], out_dir: Path, run, combo: str) -> tuple[dict, dict]:
    """Run the cascade on mix_path and write the package. Returns (manifest, timings)."""
    started = time.perf_counter()
    source = read_source(mix_path)
    mix, _ = sf.read(mix_path, dtype="float32", always_2d=True)
    steps: dict = {}
    stems = cascade(mix, timed(run, steps), models)
    del mix
    write_start = time.perf_counter()
    stems, gain_db = fit_headroom(stems)
    manifest = write_package(out_dir, stems, source, gain_db, models)
    del stems
    write_s = round(time.perf_counter() - write_start, 2)
    null = null_test(out_dir / "manifest.json", mix_path)
    timings = {
        "combo": combo,
        "mix": str(mix_path.resolve()),
        "audio_s": manifest["duration_s"],
        **device_info(),
        "steps": steps,
        "write_seconds": write_s,
        "total_seconds": round(time.perf_counter() - started, 2),
        "gain_db": manifest["gain_db"],
        "null_test": null,
    }
    (out_dir / "timings.json").write_text(json.dumps(timings, indent=2) + "\n", encoding="utf-8")
    return manifest, timings


def source_metadata(info: dict) -> dict:
    """Title, artist and duration from a yt-dlp info dict (YouTube Music fills track and artists)."""
    artists = info.get("artists") or ([info["artist"]] if info.get("artist") else [])
    artist = ", ".join(artists) or info.get("creator") or info.get("uploader") or ""
    return {
        "video_id": info["id"],
        "title": info.get("track") or info.get("title") or info["id"],
        "artist": artist.removesuffix(" - Topic"),
        "duration_s": info.get("duration"),
    }


def ffmpeg_command(source: Path, target: Path) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(target)]


def cmd_download(args) -> None:
    import yt_dlp

    out_dir = args.out or LIBRARY / "downloads" / args.video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    options = {"format": "bestaudio/best", "outtmpl": str(out_dir / "download.%(ext)s"), "noplaylist": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"https://music.youtube.com/watch?v={args.video_id}", download=True)
    downloaded = Path(info["requested_downloads"][0]["filepath"])
    mix_path = out_dir / "mix.wav"
    subprocess.run(ffmpeg_command(downloaded, mix_path), check=True)
    downloaded.unlink()
    source = source_metadata(info)
    (out_dir / "source.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(source, indent=2))
    print(f"Wrote {mix_path.resolve()}")


def resolve_models(args) -> dict[str, str]:
    models = dict(COMBOS[args.combo])
    for step in ("vocals", "karaoke", "instruments"):
        override = getattr(args, f"{step}_model")
        if override:
            models[step] = override
    return models


def _print_report(timings: dict) -> None:
    print(f"device: {timings['device']} (torch {timings['torch']}, CUDA {timings['cuda']})")
    for step, row in timings["steps"].items():
        print(f"{step:<12} {row['seconds']:>8.1f} s  load {row['load_seconds']} s  "
              f"peak VRAM {row['peak_vram_mb']} MB  {row['model']}")
    print(f"{'write':<12} {timings['write_seconds']:>8.1f} s")
    print(f"{'total':<12} {timings['total_seconds']:>8.1f} s for {timings['audio_s']:.1f} s of audio")
    print(f"gain_db: {timings['gain_db']}")
    _print_null(timings["null_test"])


def _db(value: float | None) -> str:
    return "-inf" if value is None else f"{value:.1f}"


def _print_null(null: dict) -> None:
    print(f"null test: max abs error {null['max_abs_error']:.3g} ({_db(null['max_abs_error_dbfs'])} dBFS, "
          f"{null['max_abs_error_lsb']:.2f} LSB), residual RMS {_db(null['residual_rms_dbfs'])} dBFS, "
          f"mix RMS {_db(null['mix_rms_dbfs'])} dBFS")


def cmd_separate(args) -> None:
    models = resolve_models(args)
    source = read_source(args.mix)
    out_dir = args.out or LIBRARY / f"{song_id(source['title'], source['artist'], source['video_id'])}-{args.combo}"
    with tempfile.TemporaryDirectory(prefix="s2-") as work:
        run = AudioSeparatorRunner(args.model_dir, Path(work))
        _, timings = separate_song(args.mix, models, out_dir, run, args.combo)
    _print_report(timings)
    print(f"Wrote {(out_dir / 'manifest.json').resolve()}")


def cmd_null(args) -> None:
    mix = args.mix
    if mix is None:
        mix = Path(json.loads((args.manifest.parent / "timings.json").read_text(encoding="utf-8"))["mix"])
    _print_null(null_test(args.manifest, mix))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and separate songs for spike S2.")
    sub = parser.add_subparsers(dest="command", required=True)

    download = sub.add_parser("download", help="best audio from YouTube Music to a 44.1 kHz 16-bit stereo mix.wav")
    download.add_argument("video_id")
    download.add_argument("--out", type=Path, help="folder for mix.wav and source.json "
                          "(default spikes/s2-separation/library/downloads/<video_id>)")
    download.set_defaults(func=cmd_download)

    separate = sub.add_parser("separate", help="run the cascade on a mix.wav and write a song package")
    separate.add_argument("mix", type=Path, help="a mix.wav written by download, with its source.json")
    separate.add_argument("--combo", required=True, choices=sorted(COMBOS))
    separate.add_argument("--vocals-model", help="override the combo's vocals model file")
    separate.add_argument("--karaoke-model", help="override the combo's karaoke model file")
    separate.add_argument("--instruments-model", help="override the combo's instruments model file")
    separate.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR,
                          help="where models are downloaded and cached (default ~/.cache/audio-separator-models); "
                               "the AUDIO_SEPARATOR_MODEL_DIR environment variable wins over it")
    separate.add_argument("--out", type=Path, help="package folder "
                          "(default spikes/s2-separation/library/<song_id>-<combo>)")
    separate.set_defaults(func=cmd_separate)

    null = sub.add_parser("null", help="check that a package's stems add up to its mix")
    null.add_argument("manifest", type=Path)
    null.add_argument("--mix", type=Path, help="the mix to compare with (default: the one in timings.json)")
    null.set_defaults(func=cmd_null)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
