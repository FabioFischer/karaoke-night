# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "librosa",
#     "numpy",
#     "rmvpe-onnx==0.2.3",
#     "soundfile",
#     "torch",
#     "torchaudio",
#     "torchcrepe==0.0.24",
# ]
#
# [tool.uv.sources]
# torch = [{ index = "pytorch-cu130", marker = "sys_platform == 'win32' or sys_platform == 'linux'" }]
# torchaudio = [{ index = "pytorch-cu130", marker = "sys_platform == 'win32' or sys_platform == 'linux'" }]
#
# [[tool.uv.index]]
# name = "pytorch-cu130"
# url = "https://download.pytorch.org/whl/cu130"
# explicit = true
# ///
"""Melody and key analysis of an S2 package for spike S6.

Usage:
  uv run spikes/s6-melody/melody.py track <pkg> --tracker pyin|crepe|rmvpe [--stem lead] [--model-dir DIR]
  uv run spikes/s6-melody/melody.py notes <pkg> --tracker T [--stem lead]
  uv run spikes/s6-melody/melody.py key <pkg> --tracker T [--stem lead] [--window S] [--hop S]
      [--melody-weight W] [--switch-penalty P]
  uv run spikes/s6-melody/melody.py preview <pkg> --tracker T [--stem lead] [--with-lead]
  uv run spikes/s6-melody/melody.py all <pkg> [--trackers pyin,crepe,rmvpe] [--stem lead] [...]

<pkg> is a package folder from S2 (manifest.json plus stems/*.flac). Every
output goes to <pkg>/s6/. torch, torchcrepe and rmvpe_onnx are imported only
inside the trackers that use them, so the tests run with numpy, soundfile and
librosa alone.
"""

import argparse
import itertools
import json
import time
import warnings
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

SAMPLE_RATE = 44_100
INSTRUMENT_STEMS = ("drums", "bass", "guitar", "piano", "other")
TRACKERS = ("pyin", "crepe", "rmvpe")

# Every tracker runs at 16 kHz with a 160-sample hop, and its output is put on
# this common 10 ms grid, frame i at i * GRID_HOP seconds.
TRACKER_RATE = 16_000
TRACKER_HOP = 160
GRID_HOP = 0.01
# Sung range searched by pYIN and CREPE: C2 to C6.
FMIN = 65.0
FMAX = 1_047.0
# Voicing: pYIN uses its own voiced flag. CREPE periodicity below 0.21 is
# silence (the torchcrepe README). RMVPE salience below 0.03 is unvoiced (the
# RVC default).
CREPE_THRESHOLD = 0.21
RMVPE_THRESHOLD = 0.03
CREPE_BATCH = 2_048
DEFAULT_MODEL_DIR = Path.home() / ".cache" / "s6-melody-models"

CHROMA_RATE = 22_050
CHROMA_HOP = 2_048

PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl-Kessler key profiles, tonic first. Keys 0..11 are major, 12..23 minor.
PROFILES = np.array([
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
])

# -1 dBFS, so the 16-bit preview never clips.
PREVIEW_CEILING = 10 ** (-1 / 20)


# Pitch math ---------------------------------------------------------------

def hz_to_midi(hz):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 69 + 12 * np.log2(np.asarray(hz, dtype=float) / 440.0)


def midi_to_hz(midi):
    return 440.0 * 2 ** ((np.asarray(midi, dtype=float) - 69) / 12)


def key_name(key: int) -> str:
    return f"{PITCH_NAMES[key % 12]} {'major' if key < 12 else 'minor'}"


# Common grid --------------------------------------------------------------

def grid_times(duration_s: float) -> np.ndarray:
    return np.arange(int(np.floor(duration_s / GRID_HOP + 1e-6))) * GRID_HOP


def to_grid(times, f0, confidence, grid):
    """Nearest source frame for each grid time; grid times beyond half a source hop get NaN and 0."""
    times, f0, confidence = (np.asarray(x, dtype=float) for x in (times, f0, confidence))
    hop = float(np.median(np.diff(times))) if len(times) > 1 else GRID_HOP
    right = np.clip(np.searchsorted(times, grid), 1, len(times) - 1)
    left = right - 1
    idx = np.where(grid - times[left] <= times[right] - grid, left, right)
    near = np.abs(times[idx] - grid) <= hop / 2 + 1e-9
    return np.where(near, f0[idx], np.nan), np.where(near, confidence[idx], 0.0)


# Trackers -----------------------------------------------------------------

def _pyin(audio16k):
    start = time.perf_counter()
    f0, _, voiced_prob = librosa.pyin(audio16k, fmin=FMIN, fmax=FMAX, sr=TRACKER_RATE,
                                      frame_length=1_024, hop_length=TRACKER_HOP)
    seconds = time.perf_counter() - start
    times = np.arange(len(f0)) * TRACKER_HOP / TRACKER_RATE
    return {"times": times, "f0": f0, "confidence": voiced_prob, "load_s": 0.0, "seconds": seconds,
            "device": "cpu", "peak_vram_mb": None}


def _crepe(audio16k, model_dir):
    import torch
    import torchcrepe

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    start = time.perf_counter()
    torchcrepe.load.model(device, "full")
    load_s = time.perf_counter() - start
    if device != "cpu":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    pitch, periodicity = torchcrepe.predict(
        torch.from_numpy(audio16k.astype(np.float32))[None], TRACKER_RATE, hop_length=TRACKER_HOP,
        fmin=FMIN, fmax=FMAX, model="full", decoder=torchcrepe.decode.viterbi,
        return_periodicity=True, batch_size=CREPE_BATCH, device=device)
    seconds = time.perf_counter() - start
    pitch, periodicity = pitch[0].numpy().astype(float), periodicity[0].numpy().astype(float)
    vram = round(torch.cuda.max_memory_reserved() / 2**20, 1) if device != "cpu" else None
    name = torch.cuda.get_device_name(0) if device != "cpu" else "cpu"
    return {"times": np.arange(len(pitch)) * TRACKER_HOP / TRACKER_RATE,
            "f0": np.where(periodicity >= CREPE_THRESHOLD, pitch, np.nan), "confidence": periodicity,
            "load_s": load_s, "seconds": seconds, "device": f"{name} (torch {torch.__version__})",
            "peak_vram_mb": vram}


def _rmvpe(audio16k, model_dir):
    from rmvpe_onnx import RMVPE, ensure_model

    # Downloads rmvpe.onnx on first use, outside the timings.
    path = ensure_model(Path(model_dir) / "rmvpe.onnx")
    start = time.perf_counter()
    model = RMVPE(path)
    load_s = time.perf_counter() - start
    start = time.perf_counter()
    times, frequency, confidence, _ = model.predict(audio16k.astype(np.float32), TRACKER_RATE)
    seconds = time.perf_counter() - start
    frequency, confidence = frequency.astype(float), confidence.astype(float)
    return {"times": times, "f0": np.where(confidence >= RMVPE_THRESHOLD, frequency, np.nan),
            "confidence": confidence, "load_s": load_s, "seconds": seconds,
            "device": f"onnxruntime {model.session.get_providers()[0]}", "peak_vram_mb": None}


def run_tracker(tracker: str, audio, rate: int, model_dir=DEFAULT_MODEL_DIR) -> dict:
    """f0 of mono audio on the common grid. seconds covers resampling and inference, not the model load."""
    start = time.perf_counter()
    audio16k = librosa.resample(np.asarray(audio, dtype=np.float32), orig_sr=rate, target_sr=TRACKER_RATE)
    resample_s = time.perf_counter() - start
    raw = {"pyin": _pyin, "crepe": _crepe, "rmvpe": _rmvpe}[tracker](
        *((audio16k,) if tracker == "pyin" else (audio16k, model_dir)))
    grid = grid_times(len(audio) / rate)
    f0, confidence = to_grid(raw["times"], raw["f0"], raw["confidence"], grid)
    return {"times": grid, "f0": f0, "confidence": confidence, "hop_s": GRID_HOP,
            "native_hop_s": TRACKER_HOP / TRACKER_RATE, "tracker_rate": TRACKER_RATE,
            "load_s": round(raw["load_s"], 2), "seconds": round(resample_s + raw["seconds"], 2),
            "device": raw["device"], "peak_vram_mb": raw["peak_vram_mb"]}


# Notes --------------------------------------------------------------------

def _nan_median_filter(values, size):
    """Median over the voiced frames of a centered window; unvoiced frames stay NaN."""
    half = size // 2
    padded = np.pad(values, half, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, size)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        smoothed = np.nanmedian(windows, axis=1)
    return np.where(np.isnan(values), np.nan, smoothed)


def segment_notes(times, f0, confidence, min_confidence=0.0, smooth_frames=5, split_semitones=0.7,
                  split_frames=3, max_gap_s=0.03, min_note_s=0.08) -> list[dict]:
    """Group an f0 curve into notes.

    Frames that are unvoiced or under min_confidence are dropped, the pitch is
    median smoothed over smooth_frames, and a note ends at a gap longer than
    max_gap_s or when the pitch stays more than split_semitones away from the
    note's median for split_frames frames (vibrato stays inside one note).
    Notes shorter than min_note_s are dropped. midi is the rounded median in
    semitones and cents the deviation from it.
    """
    times, confidence = np.asarray(times, dtype=float), np.asarray(confidence, dtype=float)
    hop = float(times[1] - times[0]) if len(times) > 1 else GRID_HOP
    midi = hz_to_midi(f0)
    midi[~(confidence >= min_confidence)] = np.nan
    smoothed = _nan_median_filter(midi, smooth_frames)
    notes = []

    def close(frames):
        if not frames or (times[frames[-1]] + hop - times[frames[0]]) < min_note_s - 1e-9:
            return
        median = float(np.median(midi[frames]))
        pitch = int(np.clip(round(median), 0, 127))
        notes.append({"onset": round(float(times[frames[0]]), 3),
                      "offset": round(float(times[frames[-1]] + hop), 3),
                      "midi": pitch, "cents": round((median - pitch) * 100, 1),
                      "confidence": round(float(np.mean(confidence[frames])), 4)})

    current, pending, gap = [], [], 0
    for i, value in enumerate(smoothed):
        if np.isnan(value):
            if current:
                gap += 1
                if gap * hop > max_gap_s + 1e-9:
                    close(current)
                    current, pending = [], []
            continue
        gap = 0
        if not current:
            current = [i]
            continue
        if abs(value - np.median(smoothed[current])) > split_semitones:
            pending.append(i)
            if len(pending) >= split_frames:
                close(current)
                current, pending = pending, []
        else:
            current += pending + [i]
            pending = []
    close(current)
    return notes


def _vlq(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def write_midi(notes, path: Path, ppq=480, tempo_us=500_000) -> None:
    """A type 0 Standard MIDI File at 120 BPM, one note-on and note-off per note."""
    ticks_per_s = ppq * 1_000_000 / tempo_us
    events = []
    for note in notes:
        on = round(note["onset"] * ticks_per_s)
        off = max(on + 1, round(note["offset"] * ticks_per_s))
        events.append((on, 1, bytes([0x90, note["midi"], 100])))
        events.append((off, 0, bytes([0x80, note["midi"], 0])))
    track = b"\x00\xff\x51\x03" + tempo_us.to_bytes(3, "big")
    last = 0
    for tick, _, message in sorted(events, key=lambda e: (e[0], e[1])):
        track += _vlq(tick - last) + message
        last = tick
    track += b"\x00\xff\x2f\x00"
    header = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big") \
        + ppq.to_bytes(2, "big")
    Path(path).write_bytes(header + b"MTrk" + len(track).to_bytes(4, "big") + track)


# Key ----------------------------------------------------------------------

def key_scores(vectors) -> np.ndarray:
    """Pearson correlation of each 12-bin vector with the 24 rotated profiles, shape (n, 24)."""
    vectors = np.atleast_2d(np.asarray(vectors, dtype=float))
    profiles = np.array([np.roll(PROFILES[k // 12], k % 12) for k in range(24)])
    v = vectors - vectors.mean(axis=1, keepdims=True)
    p = profiles - profiles.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(v, axis=1, keepdims=True) * np.linalg.norm(p, axis=1)[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = (v @ p.T) / norms
    return np.nan_to_num(scores)


def viterbi(scores, switch_penalty=1.0) -> list[int]:
    """The key path that maximizes the summed scores minus switch_penalty per key change."""
    scores = np.asarray(scores, dtype=float)
    total = scores[0].copy()
    back = np.zeros(scores.shape, dtype=int)
    for i in range(1, len(scores)):
        best = int(np.argmax(total))
        switch = total[best] - switch_penalty
        stay = total >= switch
        back[i] = np.where(stay, np.arange(scores.shape[1]), best)
        total = np.where(stay, total, switch) + scores[i]
    path = [int(np.argmax(total))]
    for i in range(len(scores) - 1, 0, -1):
        path.append(int(back[i][path[-1]]))
    return path[::-1]


def _windows(duration, window_s, hop_s):
    starts = np.arange(0.0, max(duration - window_s, 0.0) + 1e-9, hop_s)
    return [(float(s), float(min(s + window_s, duration))) for s in starts]


def _melody_histogram(notes, start, end):
    hist = np.zeros(12)
    for note in notes:
        overlap = min(note["offset"], end) - max(note["onset"], start)
        if overlap > 0:
            hist[note["midi"] % 12] += overlap
    return hist


def _unit(vector):
    total = vector.sum()
    return vector / total if total > 0 else vector


def _sections(windows, scores, path, duration):
    centers = [(a + b) / 2 for a, b in windows]
    sections = []
    for i, key in enumerate(path):
        if sections and sections[-1]["key_index"] == key:
            sections[-1]["windows"].append(i)
            continue
        start = 0.0 if not sections else (centers[i - 1] + centers[i]) / 2
        if sections:
            sections[-1]["end"] = start
        sections.append({"start": start, "key_index": key, "windows": [i]})
    sections[-1]["end"] = duration
    return [{"start": round(s["start"], 2), "end": round(s["end"], 2), "key": key_name(s["key_index"]),
             "score": round(float(np.mean([scores[i, s["key_index"]] for i in s["windows"]])), 3)}
            for s in sections]


def detect_keys(chroma, chroma_times, notes, duration, window_s=8.0, hop_s=2.0, melody_weight=0.5,
                switch_penalty=1.0) -> dict:
    """Key sections from instrumental chroma (12, frames) and melody notes.

    Each window sums the chroma frames and the melody's pitch classes weighted
    by duration, each normalized to sum 1, mixed by melody_weight, and
    correlated with the 24 key profiles; viterbi smooths the key sequence.
    Returns the combined answer and the melody-only and chroma-only ones.
    """
    chroma, chroma_times = np.asarray(chroma, dtype=float), np.asarray(chroma_times, dtype=float)
    windows = _windows(duration, window_s, hop_s)
    vectors = {"combined": [], "melody_only": [], "chroma_only": []}
    for start, end in windows:
        frames = (chroma_times >= start) & (chroma_times < end)
        c = _unit(chroma[:, frames].sum(axis=1))
        m = _unit(_melody_histogram(notes, start, end))
        if c.any() and m.any():
            combined = melody_weight * m + (1 - melody_weight) * c
        else:
            combined = c if c.any() else m
        vectors["combined"].append(combined)
        vectors["melody_only"].append(m)
        vectors["chroma_only"].append(c)
    result = {}
    for answer, rows in vectors.items():
        scores = key_scores(np.array(rows))
        result[answer] = _sections(windows, scores, viterbi(scores, switch_penalty), duration)
    return result


def instrumental_chroma(instrumental) -> tuple[np.ndarray, np.ndarray]:
    mono = librosa.resample(np.asarray(instrumental, dtype=np.float32).mean(axis=1),
                            orig_sr=SAMPLE_RATE, target_sr=CHROMA_RATE)
    chroma = librosa.feature.chroma_cqt(y=mono, sr=CHROMA_RATE, hop_length=CHROMA_HOP)
    return chroma, librosa.times_like(chroma, sr=CHROMA_RATE, hop_length=CHROMA_HOP)


# Preview ------------------------------------------------------------------

def synth(notes, n_samples, rate=SAMPLE_RATE, attack_s=0.01, release_s=0.03) -> np.ndarray:
    """A triangle wave per note, with linear attack and release inside the note."""
    out = np.zeros(n_samples)
    for note in notes:
        a, b = int(note["onset"] * rate), min(int(note["offset"] * rate), n_samples)
        if b <= a:
            continue
        t = np.arange(b - a) / rate
        tone = 2 / np.pi * np.arcsin(np.sin(2 * np.pi * float(midi_to_hz(note["midi"])) * t))
        env = np.ones(b - a)
        up, down = min(int(attack_s * rate), (b - a) // 2), min(int(release_s * rate), (b - a) // 2)
        env[:up] = np.linspace(0, 1, up)
        env[len(env) - down:] = np.linspace(1, 0, down)
        out[a:b] += tone * env
    return out


def render_preview(instrumental, notes, gain_db=0.0, lead=None, synth_db=0.0, lead_db=-12.0,
                   rate=SAMPLE_RATE) -> np.ndarray:
    """Instrumental at gain_db plus the synth at synth_db against the instrumental RMS.

    lead, when given, is added at gain_db + lead_db. The sum is scaled down to
    PREVIEW_CEILING only when it would go above it.
    """
    gain = 10 ** (gain_db / 20)
    mix = np.asarray(instrumental, dtype=float) * gain
    tone = synth(notes, len(mix), rate)
    active = tone != 0
    if active.any():
        mix_rms = float(np.sqrt(np.mean(mix ** 2)))
        tone *= (mix_rms if mix_rms > 0 else 0.1) * 10 ** (synth_db / 20) / np.sqrt(np.mean(tone[active] ** 2))
    out = mix + tone[:, None]
    if lead is not None:
        out = out + np.asarray(lead, dtype=float) * gain * 10 ** (lead_db / 20)
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    return out * (PREVIEW_CEILING / peak) if peak > PREVIEW_CEILING else out


# Package ------------------------------------------------------------------

def load_manifest(pkg: Path) -> dict:
    return json.loads((Path(pkg) / "manifest.json").read_text(encoding="utf-8"))


def read_stem(pkg: Path, manifest: dict, stem: str) -> np.ndarray:
    audio, rate = sf.read(str(Path(pkg) / manifest["stems"][stem]["file"]), dtype="float32", always_2d=True)
    if rate != SAMPLE_RATE:
        raise ValueError(f"{stem} is {rate} Hz, expected {SAMPLE_RATE}")
    return audio


def read_instrumental(pkg: Path, manifest: dict) -> np.ndarray:
    return sum(read_stem(pkg, manifest, stem) for stem in INSTRUMENT_STEMS)


def tag(tracker: str, stem: str) -> str:
    return tracker if stem == "lead" else f"{tracker}-{stem}"


def out_dir(pkg: Path) -> Path:
    path = Path(pkg) / "s6"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_preview(pkg: Path, notes, path: Path, with_lead=False, stem="lead", synth_db=0.0,
                  lead_db=-12.0) -> Path:
    manifest = load_manifest(pkg)
    lead = read_stem(pkg, manifest, stem) if with_lead else None
    preview = render_preview(read_instrumental(pkg, manifest), notes, manifest.get("gain_db", 0.0), lead,
                             synth_db, lead_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), preview, SAMPLE_RATE, subtype="PCM_16")
    return Path(path)


def run_track(pkg: Path, tracker: str, stem="lead", model_dir=DEFAULT_MODEL_DIR) -> dict:
    audio = read_stem(pkg, load_manifest(pkg), stem).mean(axis=1)
    track = run_tracker(tracker, audio, SAMPLE_RATE, model_dir)
    name = tag(tracker, stem)
    out = out_dir(pkg)
    np.savez(out / f"f0-{name}.npz", times=track["times"], f0=track["f0"], confidence=track["confidence"])
    audio_s = len(audio) / SAMPLE_RATE
    meta = {key: track[key] for key in ("hop_s", "native_hop_s", "tracker_rate", "load_s", "seconds",
                                        "device", "peak_vram_mb")}
    meta.update({"tracker": tracker, "stem": stem, "audio_s": round(audio_s, 2),
                 "seconds_per_audio_minute": round(track["seconds"] / audio_s * 60, 2),
                 "voiced_fraction": round(float(np.mean(np.isfinite(track["f0"]))), 4),
                 "threshold": {"pyin": "voiced flag", "crepe": CREPE_THRESHOLD, "rmvpe": RMVPE_THRESHOLD}[tracker]})
    (out / f"track-{name}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"{name}: {meta['seconds']} s for {meta['audio_s']} s of audio "
          f"({meta['seconds_per_audio_minute']} s per minute), load {meta['load_s']} s, "
          f"peak VRAM {meta['peak_vram_mb']} MB, voiced {meta['voiced_fraction']:.0%}, {meta['device']}")
    return meta


def run_notes(pkg: Path, name: str) -> list[dict]:
    out = out_dir(pkg)
    with np.load(out / f"f0-{name}.npz") as f0:
        notes = segment_notes(f0["times"], f0["f0"], f0["confidence"])
    (out / f"notes-{name}.json").write_text(json.dumps(notes, indent=1), encoding="utf-8")
    write_midi(notes, out / f"notes-{name}.mid")
    print(f"{name}: {len(notes)} notes -> notes-{name}.json, notes-{name}.mid")
    return notes


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def _print_sections(label, sections):
    print(f"  {label}:")
    for s in sections:
        print(f"    {_clock(s['start'])} - {_clock(s['end'])}  {s['key']:<9} ({s['score']:.2f})")


def run_key(pkg: Path, name: str, window_s=8.0, hop_s=2.0, melody_weight=0.5, switch_penalty=1.0,
            chroma=None) -> dict:
    manifest = load_manifest(pkg)
    out = out_dir(pkg)
    notes = json.loads((out / f"notes-{name}.json").read_text(encoding="utf-8"))
    if chroma is None:
        instrumental = read_instrumental(pkg, manifest)
        chroma = (*instrumental_chroma(instrumental), len(instrumental) / SAMPLE_RATE)
    frames, frame_times, duration = chroma
    result = detect_keys(frames, frame_times, notes, duration, window_s, hop_s, melody_weight, switch_penalty)
    result["params"] = {"window_s": window_s, "hop_s": hop_s, "melody_weight": melody_weight,
                        "switch_penalty": switch_penalty}
    (out / f"keys-{name}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{name} keys:")
    for answer in ("combined", "melody_only", "chroma_only"):
        _print_sections(answer, result[answer])
    return result


def run_preview(pkg: Path, name: str, with_lead=False, stem="lead", synth_db=0.0, lead_db=-12.0) -> Path:
    out = out_dir(pkg)
    notes = json.loads((out / f"notes-{name}.json").read_text(encoding="utf-8"))
    suffix = "-with-lead" if with_lead else ""
    path = write_preview(pkg, notes, out / f"preview-{name}{suffix}.wav", with_lead, stem, synth_db, lead_db)
    print(f"{name}: preview -> {path}")
    return path


def agreement(curves: dict) -> dict:
    """Pairwise cents difference on the frames every compared tracker calls voiced."""
    n = min(len(f0) for f0 in curves.values())
    curves = {name: np.asarray(f0, dtype=float)[:n] for name, f0 in curves.items()}
    voiced = np.all([np.isfinite(f0) & (f0 > 0) for f0 in curves.values()], axis=0)
    result = {}
    for a, b in itertools.combinations(curves, 2):
        cents = np.abs(1200 * np.log2(curves[a][voiced] / curves[b][voiced]))
        frames = int(voiced.sum())
        result[f"{a}-{b}"] = {
            "frames": frames,
            "median_abs_cents": round(float(np.median(cents)), 1) if frames else None,
            "within_50": round(float(np.mean(cents <= 50)), 4) if frames else None,
        }
    return result


def run_all(pkg: Path, trackers, stem="lead", model_dir=DEFAULT_MODEL_DIR, window_s=8.0, hop_s=2.0,
            melody_weight=0.5, switch_penalty=1.0) -> dict:
    manifest = load_manifest(pkg)
    start = time.perf_counter()
    instrumental = read_instrumental(pkg, manifest)
    chroma, chroma_times = instrumental_chroma(instrumental)
    chroma_s = round(time.perf_counter() - start, 2)
    duration = len(instrumental) / SAMPLE_RATE
    summary = {"package": manifest.get("id"), "stem": stem, "audio_s": round(duration, 2),
               "chroma_seconds": chroma_s, "trackers": {}}
    curves = {}
    for tracker in trackers:
        name = tag(tracker, stem)
        meta = run_track(pkg, tracker, stem, model_dir)
        start = time.perf_counter()
        notes = run_notes(pkg, name)
        notes_s = round(time.perf_counter() - start, 2)
        start = time.perf_counter()
        keys = run_key(pkg, name, window_s, hop_s, melody_weight, switch_penalty, (chroma, chroma_times, duration))
        key_s = round(time.perf_counter() - start, 2)
        run_preview(pkg, name, stem=stem)
        with np.load(out_dir(pkg) / f"f0-{name}.npz") as f0:
            curves[tracker] = f0["f0"]
        summary["trackers"][tracker] = {**meta, "note_count": len(notes), "notes_seconds": notes_s,
                                        "key_seconds": key_s, "sections": keys["combined"],
                                        "melody_only": keys["melody_only"], "chroma_only": keys["chroma_only"]}
    summary["agreement"] = agreement(curves) if len(curves) > 1 else {}
    (out_dir(pkg) / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"chroma: {chroma_s} s")
    for pair, row in summary["agreement"].items():
        print(f"agreement {pair}: {row['frames']} frames, median {row['median_abs_cents']} cents, "
              f"within 50 cents {row['within_50']}")
    print(f"summary -> {out_dir(pkg) / 'summary.json'}")
    return summary


# CLI ----------------------------------------------------------------------

def _key_args(parser):
    parser.add_argument("--window", type=float, default=8.0, help="key window in seconds (default 8)")
    parser.add_argument("--hop", type=float, default=2.0, help="key window hop in seconds (default 2)")
    parser.add_argument("--melody-weight", type=float, default=0.5,
                        help="share of the melody histogram against the chroma, 0 to 1 (default 0.5)")
    parser.add_argument("--switch-penalty", type=float, default=1.0,
                        help="score a key change costs in the smoothing (default 1.0)")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, help_text, tracker=True):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("pkg", type=Path, help="package folder (manifest.json plus stems/)")
        if tracker:
            p.add_argument("--tracker", choices=TRACKERS, required=True)
        p.add_argument("--stem", default="lead", help="stem to track (default lead)")
        return p

    p = command("track", "f0 curve of a stem on the 10 ms grid")
    p.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR,
                   help=f"folder for downloaded weights (default {DEFAULT_MODEL_DIR})")
    command("notes", "group the f0 curve into notes (.json and .mid)")
    _key_args(command("key", "key per section from the melody and the instrumental chroma"))
    p = command("preview", "WAV of the instrumental plus a synth playing the notes")
    p.add_argument("--with-lead", action="store_true", help="add the tracked stem quietly, for A/B")
    p.add_argument("--synth-db", type=float, default=0.0, help="synth level against the instrumental RMS")
    p.add_argument("--lead-db", type=float, default=-12.0, help="level of the tracked stem with --with-lead")
    p = command("all", "track, notes, key and preview for each tracker, plus summary.json", tracker=False)
    p.add_argument("--trackers", default="pyin,crepe,rmvpe", help="comma separated (default pyin,crepe,rmvpe)")
    p.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    _key_args(p)

    args = parser.parse_args(argv)
    if args.command == "track":
        run_track(args.pkg, args.tracker, args.stem, args.model_dir)
    elif args.command == "notes":
        run_notes(args.pkg, tag(args.tracker, args.stem))
    elif args.command == "key":
        run_key(args.pkg, tag(args.tracker, args.stem), args.window, args.hop, args.melody_weight,
                args.switch_penalty)
    elif args.command == "preview":
        run_preview(args.pkg, tag(args.tracker, args.stem), args.with_lead, args.stem, args.synth_db, args.lead_db)
    else:
        trackers = [t.strip() for t in args.trackers.split(",") if t.strip()]
        unknown = set(trackers) - set(TRACKERS)
        if unknown:
            parser.error(f"unknown trackers: {', '.join(sorted(unknown))}")
        run_all(args.pkg, trackers, args.stem, args.model_dir, args.window, args.hop, args.melody_weight,
                args.switch_penalty)


if __name__ == "__main__":
    main()
