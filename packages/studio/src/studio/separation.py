"""The separation cascade of docs/PLAN.md section 4, run through audio-separator.

Each step keeps the model's "vocals" (or instrument) output and computes the
rest as a remainder, so the 7 stems add up to the mix. audio-separator and torch
are imported only where a model runs, so the rest works without the
`separation` extra.
"""

import gc
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf

from studio.audio import SAMPLE_RATE, align, peak

# Combination B from spike S2 (docs/PLAN.md section 3).
MODELS = {
    "vocals": "vocals_mel_band_roformer.ckpt",
    "karaoke": "mel_band_roformer_karaoke_becruily.ckpt",
    "instruments": "htdemucs_6s.yaml",
}
# Overlapping windows for the RoFormer (MDXC) models of the vocals and karaoke steps.
OVERLAP = 2

# Same names and order as the Stage loads them.
STEMS = ("lead", "backing", "drums", "bass", "guitar", "piano", "other")
INSTRUMENT_STEMS = ("drums", "bass", "guitar", "piano")
# audio-separator 0.48.0 scales down, each on its own, every output stem (and an
# MDXC model's input) that peaks above normalization_threshold. Each model gets
# its input at HEADROOM so it stays under the threshold, and its outputs are
# scaled back before the remainders are taken. 0.5 (-6 dB) is exact in float.
NORMALIZATION_THRESHOLD = 1.0
HEADROOM = 0.5
# A rescaled output peaks at the threshold up to float32 rounding.
THRESHOLD_TOLERANCE = 1e-6
# The Separator mdxc_params default in audio-separator 0.48.0 (separator.py:142).
MDXC_DEFAULTS = {
    "segment_size": 256,
    "override_model_segment_size": False,
    "batch_size": None,
    "overlap": None,
    "pitch_shift": 0,
}

Runner = Callable[[str, str, np.ndarray], dict[str, np.ndarray]]
"""run(step, model_file, audio) returns the model's outputs by stem name."""


def pick(outputs: dict[str, np.ndarray], stem: str, model: str) -> np.ndarray:
    """The output named stem (case does not matter). Model outputs differ in order, not in name."""
    by_name = {name.lower(): audio for name, audio in outputs.items()}
    if stem not in by_name:
        raise KeyError(
            f"{model} has no {stem!r} output; it gave: {', '.join(sorted(by_name))}"
        )
    return by_name[stem]


def cascade(
    mix: np.ndarray,
    run: Runner,
    models: dict[str, str],
    levels: dict | None = None,
) -> dict[str, np.ndarray]:
    """Split mix into the 7 stems.

    run(step, model_file, audio) returns the model's outputs by stem name, as
    (frames, channels) float arrays. Each model gets its input at HEADROOM, and
    each kept output is scaled back and aligned to the mix length before it is
    subtracted. levels, when given, gets per step the input and kept output
    peaks the library saw, and whether either reached its threshold.
    """
    n = len(mix)
    levels = {} if levels is None else levels

    def separate_step(step: str, audio: np.ndarray, kept) -> dict[str, np.ndarray]:
        model = models[step]
        scaled = audio * HEADROOM
        outputs = run(step, model, scaled)
        picked = {stem: pick(outputs, stem, model) for stem in kept}
        input_peak = peak(scaled)
        output_peak = max(peak(audio) for audio in picked.values())
        levels[step] = {
            "input_peak": round(input_peak, 6),
            "output_peak": round(output_peak, 6),
            "reached_threshold": input_peak > NORMALIZATION_THRESHOLD
            or output_peak >= NORMALIZATION_THRESHOLD - THRESHOLD_TOLERANCE,
        }
        return {stem: align(audio / HEADROOM, n) for stem, audio in picked.items()}

    vocals = separate_step("vocals", mix, ["vocals"])["vocals"]
    instrumental = mix - vocals
    lead = separate_step("karaoke", vocals, ["vocals"])["vocals"]
    backing = vocals - lead
    parts = separate_step("instruments", instrumental, INSTRUMENT_STEMS)
    other = instrumental - sum(parts.values())
    return {"lead": lead, "backing": backing, **parts, "other": other}


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


def timed(run: Runner, timings: dict) -> Runner:
    """Wrap run so each step records its wall clock time and peak VRAM in timings."""

    def wrapped(step, model_file, audio):
        reset_vram_peak()
        start = time.perf_counter()
        outputs = run(step, model_file, audio)
        timings[step] = {
            "model": model_file,
            "seconds": round(time.perf_counter() - start, 2),
            "load_seconds": getattr(run, "last_load_s", None),
            "overlap": getattr(run, "last_overlap", None),
            "peak_vram_mb": peak_vram_mb(),
        }
        return outputs

    return wrapped


# audio-separator ------------------------------------------------------------


def stem_from_filename(filename: str, base: str) -> str:
    """audio-separator names each output "<input base>_(<Stem>)_<model>.<ext>"."""
    match = re.match(re.escape(base) + r"_\((.+?)\)_", filename)
    if not match:
        raise ValueError(f"unexpected audio-separator output name {filename!r}")
    return match.group(1)


def separator_options(
    model_dir: Path | None, out_dir: Path, overlap: int | None = OVERLAP
) -> dict:
    """Keyword arguments for audio-separator's Separator.

    normalization_threshold 1.0 (its default is 0.9) only rescales audio that
    peaks above full scale. overlap, when given, replaces the MDXC (RoFormer)
    overlap; without it the library takes the model YAML's num_overlap, else 8.
    Without model_dir the library keeps its own default model folder.
    """
    options = {
        "log_level": logging.WARNING,
        "output_dir": str(out_dir),
        "output_format": "WAV",
        "use_soundfile": True,
        "normalization_threshold": NORMALIZATION_THRESHOLD,
        "sample_rate": SAMPLE_RATE,
    }
    if model_dir is not None:
        options["model_file_dir"] = str(model_dir)
    if overlap is not None:
        options["mdxc_params"] = {**MDXC_DEFAULTS, "overlap": overlap}
    return options


def effective_overlap(model_instance) -> int | None:
    """The overlap a loaded MDXC model runs with, or None for other architectures."""
    if type(model_instance).__name__ != "MDXCSeparator":
        return None
    return model_instance.overlap


class AudioSeparatorRunner:
    """Runs one audio-separator model on an array and returns its outputs by stem name.

    The input goes to the model as a 32-bit float WAV. audio-separator writes
    its outputs with the input's subtype when use_soundfile is on, so they stay
    float too, with no 16-bit rounding or clipping between steps.
    """

    def __init__(
        self, model_dir: Path | None, work_dir: Path, overlap: int | None = OVERLAP
    ):
        from audio_separator.separator import Separator

        self.Separator = Separator
        self.model_dir = model_dir
        self.work_dir = work_dir
        self.overlap = overlap
        self.last_load_s = None
        self.last_overlap = None

    def __call__(
        self, step: str, model_file: str, audio: np.ndarray
    ) -> dict[str, np.ndarray]:
        base = f"{step}_input"
        input_path = self.work_dir / f"{base}.wav"
        out_dir = self.work_dir / step
        sf.write(input_path, audio, SAMPLE_RATE, subtype="FLOAT")
        separator = self.Separator(
            **separator_options(self.model_dir, out_dir, self.overlap)
        )
        start = time.perf_counter()
        separator.load_model(model_file)
        self.last_load_s = round(time.perf_counter() - start, 2)
        self.last_overlap = effective_overlap(separator.model_instance)
        # separate() returns file names relative to output_dir.
        files = [
            out_dir / Path(name).name for name in separator.separate(str(input_path))
        ]
        del separator
        outputs = {}
        for path in files:
            data, _ = sf.read(path, dtype="float32", always_2d=True)
            outputs[stem_from_filename(path.name, base).lower()] = data
            path.unlink()
        input_path.unlink()
        return outputs
