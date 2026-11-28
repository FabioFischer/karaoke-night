"""The lead melody and the key per section of a song (docs/PLAN.md section 3).

SOME finds where the notes of the lead stem start and end, RMVPE tracks its
pitch on a 10 ms grid, and each note takes the median pitch of its middle
frames. The key per section comes from those pitches plus the chroma of the
instrumental, correlated with the Krumhansl-Kessler profiles over 16 s windows
and smoothed with a Viterbi path, as spike S6 chose. Each note then goes to its
nearest semitone, or to the scale note on the voice's side when that semitone
is outside the key. torch, yaml, librosa and rmvpe_onnx are imported only
inside some_notes, rmvpe_track and instrumental_chroma, so the rest works
without the `separation` extra.
"""

import math
import shutil
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np

from studio.audio import SAMPLE_RATE
from studio.some.utils.slicer2 import Slicer

# RMVPE's pitch output is put on this grid, frame i at i * GRID_HOP seconds.
GRID_HOP = 0.01
# RMVPE salience below 0.03 is unvoiced (the RVC default).
RMVPE_THRESHOLD = 0.03
RMVPE_MODEL = "rmvpe.onnx"
CHROMA_RATE = 22_050
CHROMA_HOP = 2_048

# A note's pitch is the median of its frames without the first and last 20%.
NOTE_TRIM = 0.2
# SOME's own infer.py slices with this; silences longer than 1 s are cut down.
SLICER_MAX_SIL_KEPT = 1000
SCALES = {"major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10)}

# SOME's model, CC BY-NC-SA 4.0. The zip says 128 and its folder 256, as released.
SOME_URL = (
    "https://github.com/openvpi/SOME/releases/download/v1.0.0-baseline/"
    "0119_continuous128_5spk.zip"
)
SOME_FOLDER = "some"
SOME_MODEL = "0119_continuous256_5spk"
SOME_CHECKPOINT = "model_ckpt_steps_100000_simplified.ckpt"

# The instrumental the key is read from, as spike S6 read it.
INSTRUMENT_STEMS = ("drums", "bass", "guitar", "piano", "other")

PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl-Kessler key profiles, tonic first. Keys 0..11 are major, 12..23 minor.
PROFILES = np.array(
    [
        [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
        [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    ]
)

PitchTracker = Callable[[np.ndarray, int], tuple[np.ndarray, np.ndarray, np.ndarray]]
"""track(mono, rate) returns the tracker's frame times, pitch in Hz and confidence."""

NoteSplitter = Callable[[np.ndarray, int], list[dict]]
"""split(mono, rate) returns SOME's chunks: offset in seconds, then note_midi,
note_dur in seconds and note_rest per note."""

Chroma = Callable[[np.ndarray, int], tuple[np.ndarray, np.ndarray]]
"""chroma(mono, rate) returns the (12, frames) chroma and the frame times."""


# Pitch math -------------------------------------------------------------------


def hz_to_midi(hz):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 69 + 12 * np.log2(np.asarray(hz, dtype=float) / 440.0)


def midi_to_hz(midi):
    return 440.0 * 2 ** ((np.asarray(midi, dtype=float) - 69) / 12)


def key_of(key: int) -> tuple[str, str]:
    """The tonic and mode of a key index of PROFILES' rotations."""
    return PITCH_NAMES[key % 12], "major" if key < 12 else "minor"


# Common grid ------------------------------------------------------------------


def grid_times(duration_s: float) -> np.ndarray:
    return np.arange(int(np.floor(duration_s / GRID_HOP + 1e-6))) * GRID_HOP


def to_grid(times, f0, confidence, grid):
    """Nearest source frame for each grid time; grid times beyond half a source hop get NaN and 0."""
    times, f0, confidence = (
        np.asarray(x, dtype=float) for x in (times, f0, confidence)
    )
    hop = float(np.median(np.diff(times))) if len(times) > 1 else GRID_HOP
    right = np.clip(np.searchsorted(times, grid), 1, len(times) - 1)
    left = right - 1
    idx = np.where(grid - times[left] <= times[right] - grid, left, right)
    near = np.abs(times[idx] - grid) <= hop / 2 + 1e-9
    return np.where(near, f0[idx], np.nan), np.where(near, confidence[idx], 0.0)


# Notes ------------------------------------------------------------------------


def notes_from_chunks(chunks) -> list[tuple[float, float]]:
    """The (start_s, end_s) of SOME's notes, walking each chunk's durations from its offset."""
    notes = []
    for chunk in chunks:
        start = float(chunk["offset"])
        for duration, rest in zip(chunk["note_dur"], chunk["note_rest"], strict=True):
            end = start + float(duration)
            if not rest:
                notes.append((start, end))
            start = end
    return notes


def note_value(midi, start, end) -> float | None:
    """The median voiced pitch of a note's grid frames, its first and last 20% left out.

    A note of 4 frames or fewer keeps them all; None when no frame is voiced.
    """
    a, b = round(start / GRID_HOP), round(end / GRID_HOP)
    trim = int((b - a) * NOTE_TRIM)
    frames = midi[a + trim : b - trim] if b - a > 4 else midi[a:b]
    frames = frames[~np.isnan(frames)]
    return float(np.median(frames)) if len(frames) else None


def key_at(keys: list[dict], time: float) -> dict:
    """The key section a time falls in; the last one past the end."""
    return [key for key in keys if key["start_s"] <= time][-1]


def snap_to_key(value: float, key: dict) -> int:
    """The nearest semitone, or the next one on the voice's side when it is out of the key."""
    pitch = round(value)
    tonic = PITCH_NAMES.index(key["tonic"])
    if (pitch - tonic) % 12 in SCALES[key["mode"]]:
        return pitch
    return math.floor(value) if pitch > value else math.ceil(value)


# Key --------------------------------------------------------------------------


def key_scores(vectors) -> np.ndarray:
    """Pearson correlation of each 12-bin vector with the 24 rotated profiles, shape (n, 24)."""
    vectors = np.atleast_2d(np.asarray(vectors, dtype=float))
    profiles = np.array([np.roll(PROFILES[k // 12], k % 12) for k in range(24)])
    v = vectors - vectors.mean(axis=1, keepdims=True)
    p = profiles - profiles.mean(axis=1, keepdims=True)
    norms = (
        np.linalg.norm(v, axis=1, keepdims=True) * np.linalg.norm(p, axis=1)[None, :]
    )
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
        overlap = min(note["end_s"], end) - max(note["start_s"], start)
        if overlap > 0:
            hist[round(note["midi"]) % 12] += overlap
    return hist


def _unit(vector):
    total = vector.sum()
    return vector / total if total > 0 else vector


def _sections(windows, path, duration):
    """One section per run of a key; a change falls halfway between the window centers."""
    centers = [(a + b) / 2 for a, b in windows]
    runs = []
    for i, key in enumerate(path):
        if runs and runs[-1][1] == key:
            continue
        start = 0.0 if not runs else (centers[i - 1] + centers[i]) / 2
        runs.append((start, key))
    ends = [start for start, _ in runs[1:]] + [duration]
    return [
        {
            "start_s": round(float(start), 3),
            "end_s": round(float(end), 3),
            "tonic": key_of(key)[0],
            "mode": key_of(key)[1],
        }
        for (start, key), end in zip(runs, ends, strict=True)
    ]


def detect_keys(
    chroma,
    chroma_times,
    notes,
    duration,
    window_s=16.0,
    hop_s=2.0,
    melody_weight=0.5,
    switch_penalty=6.0,
) -> list[dict]:
    """Key sections from instrumental chroma (12, frames) and melody notes.

    Each window sums the chroma frames and the melody's pitch classes weighted
    by duration, each normalized to sum 1, mixed by melody_weight, and
    correlated with the 24 key profiles; viterbi smooths the key sequence.
    """
    chroma, chroma_times = (
        np.asarray(chroma, dtype=float),
        np.asarray(chroma_times, dtype=float),
    )
    windows = _windows(duration, window_s, hop_s)
    vectors = []
    for start, end in windows:
        frames = (chroma_times >= start) & (chroma_times < end)
        c = _unit(chroma[:, frames].sum(axis=1))
        m = _unit(_melody_histogram(notes, start, end))
        if c.any() and m.any():
            vectors.append(melody_weight * m + (1 - melody_weight) * c)
        else:
            vectors.append(c if c.any() else m)
    path = viterbi(key_scores(np.array(vectors)), switch_penalty)
    return _sections(windows, path, duration)


# The analysis -----------------------------------------------------------------


def analyze(
    stems: dict[str, np.ndarray],
    track: PitchTracker,
    split: NoteSplitter,
    chroma: Chroma,
    rate: int = SAMPLE_RATE,
) -> dict:
    """The melody.json document of a package's (frames, channels) stems."""
    lead = stems["lead"].mean(axis=1)
    duration = len(lead) / rate
    times, frequency, confidence = track(lead, rate)
    confidence = np.asarray(confidence, dtype=float)
    f0 = np.where(confidence >= RMVPE_THRESHOLD, frequency, np.nan)
    grid = grid_times(duration)
    midi = hz_to_midi(to_grid(times, f0, confidence, grid)[0])
    notes = []
    for start, end in notes_from_chunks(split(lead, rate)):
        value = note_value(midi, start, end)
        if value is not None:
            notes.append({"start_s": start, "end_s": end, "midi": value})
    instrumental = sum(stems[name] for name in INSTRUMENT_STEMS).mean(axis=1)
    frames, frame_times = chroma(instrumental, rate)
    keys = detect_keys(frames, frame_times, notes, duration)
    return {
        "notes": [
            {
                "start_s": round(note["start_s"], 3),
                "end_s": round(note["end_s"], 3),
                "midi": snap_to_key(note["midi"], key_at(keys, note["start_s"])),
            }
            for note in notes
        ],
        "keys": keys,
    }


def rmvpe_track(mono: np.ndarray, rate: int, model_dir: Path):
    """RMVPE on the CPU, as in S6; downloads rmvpe.onnx into model_dir on first use."""
    from rmvpe_onnx import RMVPE

    model = RMVPE(model_dir / RMVPE_MODEL, device="cpu")
    times, frequency, confidence, _ = model.predict(mono.astype(np.float32), rate)
    return times, frequency, confidence


def instrumental_chroma(mono: np.ndarray, rate: int):
    import librosa

    audio = librosa.resample(
        mono.astype(np.float32), orig_sr=rate, target_sr=CHROMA_RATE
    )
    chroma = librosa.feature.chroma_cqt(y=audio, sr=CHROMA_RATE, hop_length=CHROMA_HOP)
    return chroma, librosa.times_like(chroma, sr=CHROMA_RATE, hop_length=CHROMA_HOP)


def some_notes(mono: np.ndarray, rate: int, model_dir: Path) -> list[dict]:
    """SOME on the CPU, as the tuning round ran it; downloads its model on first use."""
    import yaml

    from studio.some.inference.me_infer import MIDIExtractionInference

    checkpoint = ensure_some_model(model_dir)
    config = yaml.safe_load((checkpoint.parent / "config.yaml").read_text("utf-8"))
    model = MIDIExtractionInference(config, checkpoint, device="cpu")
    return slice_and_infer(mono, rate, model.infer)


def slice_and_infer(
    mono: np.ndarray, rate: int, infer: Callable[[list[np.ndarray]], list[dict]]
) -> list[dict]:
    """Slice the lead at its silences, as SOME's infer.py does, and infer each chunk."""
    chunks = Slicer(sr=rate, max_sil_kept=SLICER_MAX_SIL_KEPT).slice(
        mono.astype(np.float32)
    )
    results = infer([chunk["waveform"] for chunk in chunks])
    return [
        {"offset": chunk["offset"], **result}
        for chunk, result in zip(chunks, results, strict=True)
    ]


def download_file(url: str, path: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, path.open("wb") as f:
        shutil.copyfileobj(response, f)


def ensure_some_model(
    model_dir: Path, fetch: Callable[[str, Path], None] = download_file
) -> Path:
    """SOME's checkpoint in model_dir/some, downloaded and unpacked when missing.

    The zip downloads to a .part name and unpacks into a hidden folder, and only
    the complete model folder is renamed into place, so an interrupted run
    leaves nothing that looks like the model; the next one starts over.
    """
    folder = model_dir / SOME_FOLDER
    checkpoint = folder / SOME_MODEL / SOME_CHECKPOINT
    if checkpoint.is_file():
        return checkpoint
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / (SOME_URL.rsplit("/", 1)[1] + ".part")
    unpacked = folder / ".unpacking"
    shutil.rmtree(unpacked, ignore_errors=True)
    try:
        fetch(SOME_URL, archive)
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(unpacked)
        (unpacked / SOME_MODEL).rename(folder / SOME_MODEL)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(unpacked, ignore_errors=True)
    return checkpoint
