"""The lead melody and the key per section of a song, chosen in spike S6.

RMVPE tracks the pitch of the lead stem on a 10 ms grid, the curve is grouped
into notes, and the key per section comes from those notes plus the chroma of
the instrumental, correlated with the Krumhansl-Kessler profiles over 16 s
windows and smoothed with a Viterbi path (docs/PLAN.md section 3). librosa and
rmvpe_onnx are imported only inside rmvpe_track and instrumental_chroma, so
the rest works without the `separation` extra.
"""

import warnings
from collections.abc import Callable
from pathlib import Path

import numpy as np

from studio.audio import SAMPLE_RATE

# RMVPE's pitch output is put on this grid, frame i at i * GRID_HOP seconds.
GRID_HOP = 0.01
# RMVPE salience below 0.03 is unvoiced (the RVC default).
RMVPE_THRESHOLD = 0.03
RMVPE_MODEL = "rmvpe.onnx"
CHROMA_RATE = 22_050
CHROMA_HOP = 2_048

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


def _nan_median_filter(values, size):
    """Median over the voiced frames of a centered window; unvoiced frames stay NaN."""
    half = size // 2
    padded = np.pad(values, half, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, size)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        smoothed = np.nanmedian(windows, axis=1)
    return np.where(np.isnan(values), np.nan, smoothed)


def segment_notes(
    times,
    f0,
    confidence,
    min_confidence=0.0,
    smooth_frames=19,
    split_semitones=0.7,
    split_frames=3,
    max_gap_s=0.03,
    min_note_s=0.08,
) -> list[dict]:
    """Group an f0 curve into notes.

    Frames that are unvoiced or under min_confidence are dropped, the pitch is
    median smoothed over smooth_frames, and a note ends at a gap longer than
    max_gap_s or when the smoothed pitch stays more than split_semitones away
    from the note's smoothed median for split_frames frames. The default
    window of 190 ms spans about one vibrato period (5 to 6 Hz), so vibrato
    does not reach the split decision, while the median still keeps a held
    step sharp. Notes shorter than min_note_s are dropped. midi is the median
    of the unsmoothed pitch, unrounded to a semitone so the tuning stays.
    """
    times, confidence = (
        np.asarray(times, dtype=float),
        np.asarray(confidence, dtype=float),
    )
    hop = float(times[1] - times[0]) if len(times) > 1 else GRID_HOP
    midi = hz_to_midi(f0)
    midi[~(confidence >= min_confidence)] = np.nan
    smoothed = _nan_median_filter(midi, smooth_frames)
    notes = []

    def close(frames):
        if (
            not frames
            or (times[frames[-1]] + hop - times[frames[0]]) < min_note_s - 1e-9
        ):
            return
        notes.append(
            {
                "start_s": round(float(times[frames[0]]), 3),
                "end_s": round(float(times[frames[-1]] + hop), 3),
                "midi": round(float(np.median(midi[frames])), 2),
            }
        )

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
    notes = segment_notes(grid, *to_grid(times, f0, confidence, grid))
    instrumental = sum(stems[name] for name in INSTRUMENT_STEMS).mean(axis=1)
    frames, frame_times = chroma(instrumental, rate)
    return {"notes": notes, "keys": detect_keys(frames, frame_times, notes, duration)}


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
