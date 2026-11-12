"""Tests for melody.py on synthetic signals. CPU only, no network, no model downloads.

Run from the repo root:
PYTHONDONTWRITEBYTECODE=1 uv run --with numpy --with soundfile --with librosa python -m unittest discover -s spikes/s6-melody -v
"""

import contextlib
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

import melody

RATE = melody.SAMPLE_RATE
HOP = melody.GRID_HOP
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# I, IV, V, I of a major key, as semitones above the tonic.
PROGRESSION = ((0, 4, 7), (5, 9, 0), (7, 11, 2), (0, 4, 7))


def sung_tone(notes, seconds, rate=RATE, vibrato_cents=30.0, vibrato_hz=5.5, seed=0):
    """A voice-like mono signal: harmonic tones with vibrato and a soft envelope.

    notes is a list of (onset_s, duration_s, midi).
    """
    out = np.zeros(int(seconds * rate))
    rng = np.random.default_rng(seed)
    for onset, duration, midi in notes:
        n = int(duration * rate)
        t = np.arange(n) / rate
        cents = vibrato_cents * np.sin(2 * np.pi * vibrato_hz * t)
        hz = melody.midi_to_hz(midi) * 2 ** (cents / 1200)
        phase = 2 * np.pi * np.cumsum(hz) / rate
        tone = sum(amp * np.sin(k * phase) for k, amp in enumerate((1.0, 0.5, 0.3, 0.2, 0.1), start=1))
        ramp = min(n // 4, int(0.03 * rate))
        env = np.ones(n)
        env[:ramp] = np.linspace(0, 1, ramp)
        env[n - ramp:] = np.linspace(1, 0, ramp)
        start = int(onset * rate)
        out[start:start + n] += 0.3 * tone * env
    return out + 0.001 * rng.standard_normal(len(out))


def chord_bed(chords, seconds, rate=RATE):
    """Sine triads in two octaves, one chord per (start_s, end_s, pitch_classes)."""
    out = np.zeros(int(seconds * rate))
    for start, end, pcs in chords:
        a, b = int(start * rate), int(end * rate)
        t = np.arange(b - a) / rate
        for pc in pcs:
            for octave_midi in (48, 60):
                out[a:b] += 0.05 * np.sin(2 * np.pi * melody.midi_to_hz(octave_midi + pc) * t)
    return out


def progression(tonic, start, end, chord_s=2.0):
    chords, t, i = [], start, 0
    while t < end:
        chords.append((t, min(t + chord_s, end), [(tonic + x) % 12 for x in PROGRESSION[i % 4]]))
        t += chord_s
        i += 1
    return chords


def write_package(folder: Path, seconds, notes, chords, gain_db=1.5, seed=0):
    """A placeholder S2 package: manifest.json plus 7 stereo 16-bit FLAC stems."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    lead = sung_tone(notes, seconds, seed=seed)
    bed = chord_bed(chords, seconds)
    drums = np.zeros(n)
    for start in range(0, n, RATE // 2):
        burst = min(2_000, n - start)
        drums[start:start + burst] += 0.2 * rng.standard_normal(burst) * np.linspace(1, 0, burst)
    mono = {
        "lead": lead,
        "backing": np.zeros(n),
        "drums": drums,
        "bass": 0.5 * bed,
        "guitar": 0.2 * bed,
        "piano": bed,
        "other": 0.002 * rng.standard_normal(n),
    }
    (folder / "stems").mkdir(parents=True, exist_ok=True)
    stems = {}
    for name, audio in mono.items():
        sf.write(folder / "stems" / f"{name}.flac", np.stack([audio, audio], axis=1), RATE, subtype="PCM_16")
        stems[name] = {"file": f"stems/{name}.flac"}
    manifest = {"schema": 1, "id": "song-title--artist-name--a1b2c3", "title": "Song Title",
                "artist": "Artist Name", "duration_s": seconds, "gain_db": gain_db, "stems": stems}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return folder


def f0_curve(segments, seconds):
    """A 10 ms f0 curve (Hz, NaN unvoiced) from (onset_s, duration_s, midi, vibrato_cents) segments."""
    times = np.arange(int(round(seconds / HOP))) * HOP
    midi = np.full(len(times), np.nan)
    for onset, duration, pitch, vibrato in segments:
        mask = (times >= onset - 1e-9) & (times < onset + duration - 1e-9)
        midi[mask] = pitch + vibrato / 100 * np.sin(2 * np.pi * 5.5 * (times[mask] - onset))
    return times, melody.midi_to_hz(midi)


class ConversionTests(unittest.TestCase):
    def test_hz_to_midi_and_back(self):
        self.assertAlmostEqual(float(melody.hz_to_midi(440.0)), 69.0)
        self.assertAlmostEqual(float(melody.hz_to_midi(261.6256)), 60.0, places=4)
        self.assertAlmostEqual(float(melody.midi_to_hz(69)), 440.0)
        self.assertAlmostEqual(float(melody.midi_to_hz(57)), 220.0)
        midi = np.array([40.25, 60.0, 83.5])
        np.testing.assert_allclose(melody.hz_to_midi(melody.midi_to_hz(midi)), midi)
        self.assertTrue(np.isnan(melody.hz_to_midi(np.nan)))
        self.assertTrue(np.isnan(melody.midi_to_hz(np.nan)))

    def test_key_names(self):
        self.assertEqual(melody.key_name(0), "C major")
        self.assertEqual(melody.key_name(12 + 9), "A minor")


class SegmentationTests(unittest.TestCase):
    def test_notes_with_gaps_vibrato_and_a_blip(self):
        segments = [
            (0.50, 0.60, 60, 40),  # vibrato +-40 cents stays one note
            (1.10, 0.40, 62, 0),   # legato step up, no gap
            (1.70, 0.30, 64.2, 0),  # 20 cents sharp
            (2.20, 0.04, 70, 0),   # shorter than the minimum note: dropped
            (2.50, 0.50, 59, 25),
        ]
        times, f0 = f0_curve(segments, 3.5)
        # A 20 ms dropout inside the first note is bridged, not a new note.
        f0[(times >= 0.80) & (times < 0.82)] = np.nan
        confidence = np.where(np.isnan(f0), 0.0, 0.9)
        notes = melody.segment_notes(times, f0, confidence)
        self.assertEqual([n["midi"] for n in notes], [60, 62, 64, 59])
        for note, (onset, duration, _, _) in zip(notes, [s for s in segments if s[1] > 0.1]):
            self.assertAlmostEqual(note["onset"], onset, delta=0.04)
            self.assertAlmostEqual(note["offset"], onset + duration, delta=0.04)
        self.assertAlmostEqual(notes[2]["cents"], 20, delta=3)
        self.assertAlmostEqual(notes[0]["confidence"], 0.9, places=6)

    def test_low_confidence_frames_are_dropped(self):
        times, f0 = f0_curve([(0.0, 1.0, 60, 0)], 1.0)
        confidence = np.where(times < 0.5, 0.9, 0.0)
        notes = melody.segment_notes(times, f0, confidence, min_confidence=0.5)
        self.assertEqual(len(notes), 1)
        self.assertAlmostEqual(notes[0]["offset"], 0.5, delta=0.02)

    def test_midi_file_holds_the_notes(self):
        notes = [{"onset": 0.0, "offset": 0.5, "midi": 60}, {"onset": 0.5, "offset": 1.25, "midi": 64}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.mid"
            melody.write_midi(notes, path)
            data = path.read_bytes()
        self.assertEqual(data[:4], b"MThd")
        self.assertIn(b"MTrk", data)
        self.assertEqual(data.count(bytes([0x90, 60, 100])), 1)
        self.assertEqual(data.count(bytes([0x90, 64, 100])), 1)


class TrackerTests(unittest.TestCase):
    def test_pyin_recovers_a_sung_line(self):
        sung = [(0.10, 0.40, 57), (0.60, 0.40, 60), (1.10, 0.40, 64)]
        audio = sung_tone(sung, 1.7)
        track = melody.run_tracker("pyin", audio, RATE)
        self.assertEqual(track["hop_s"], HOP)
        notes = melody.segment_notes(track["times"], track["f0"], track["confidence"])
        self.assertEqual([n["midi"] for n in notes], [57, 60, 64])
        for note, (onset, duration, _) in zip(notes, sung):
            self.assertAlmostEqual(note["onset"], onset, delta=0.06)

    def test_heavy_trackers_are_not_imported_at_module_import(self):
        for name in ("torch", "torchcrepe", "onnxruntime", "rmvpe_onnx"):
            self.assertNotIn(name, sys.modules)


class GridTests(unittest.TestCase):
    def test_trackers_with_different_hops_land_on_the_common_grid(self):
        def glide(t):
            return melody.midi_to_hz(60 + 2 * t)  # 2 semitones per second

        grid = melody.grid_times(2.0)
        self.assertEqual(len(grid), 200)
        self.assertAlmostEqual(grid[1] - grid[0], HOP)
        # Tracker A: 10 ms hop. Tracker B: 256 samples at 22.05 kHz (11.6 ms), ends early.
        times_a = np.arange(205) * 0.01
        times_b = np.arange(150) * 256 / 22_050
        f0_a, f0_b = melody.to_grid(times_a, glide(times_a), np.ones(205), grid), \
            melody.to_grid(times_b, glide(times_b), np.ones(150), grid)
        for f0, conf in (f0_a, f0_b):
            self.assertEqual(len(f0), len(grid))
            self.assertEqual(len(conf), len(grid))
        covered = grid <= times_b[-1]
        cents = 1200 * np.log2(f0_b[0][covered] / glide(grid[covered]))
        self.assertLess(np.max(np.abs(cents)), 13)  # nearest frame within half a hop at 200 cents/s
        self.assertTrue(np.all(np.isnan(f0_b[0][grid > times_b[-1] + 0.01])))
        self.assertTrue(np.all(f0_b[1][grid > times_b[-1] + 0.01] == 0))
        np.testing.assert_allclose(f0_a[0], glide(grid))

    def test_unvoiced_source_frames_stay_unvoiced(self):
        times = np.arange(100) * 0.01
        f0 = np.where(times < 0.5, 220.0, np.nan)
        grid_f0, _ = melody.to_grid(times, f0, np.ones(100), melody.grid_times(1.0))
        self.assertTrue(np.all(grid_f0[:50] == 220.0))
        self.assertTrue(np.all(np.isnan(grid_f0[50:])))


class KeyTests(unittest.TestCase):
    def synthetic_song(self):
        """C major for 40 s, then D major for 40 s: chroma frames plus a melody on the chord tones."""
        rate = 10  # chroma frames per second
        times = np.arange(80 * rate) / rate
        chroma = np.full((12, len(times)), 0.05)
        notes = []
        for tonic, start, end in ((0, 0, 40), (2, 40, 80)):
            for c_start, c_end, pcs in progression(tonic, start, end):
                frames = (times >= c_start) & (times < c_end)
                for pc in pcs:
                    chroma[pc, frames] = 1.0
                # The melody arpeggiates each chord: root, third, fifth, root.
                for t, pc in zip(np.arange(c_start, c_end, 0.5), (pcs[0], pcs[1], pcs[2], pcs[0])):
                    notes.append({"onset": float(t), "offset": float(t + 0.4), "midi": 60 + pc})
        return times, chroma, notes

    def assert_c_then_d(self, sections, window):
        self.assertEqual([s["key"] for s in sections], ["C major", "D major"])
        self.assertLessEqual(abs(sections[1]["start"] - 40), window)
        self.assertEqual(sections[0]["start"], 0.0)
        self.assertEqual(sections[-1]["end"], 80.0)
        self.assertEqual(sections[0]["end"], sections[1]["start"])

    def test_combined_melody_only_and_chroma_only_find_the_change(self):
        times, chroma, notes = self.synthetic_song()
        window, hop = 8.0, 2.0
        result = melody.detect_keys(chroma, times, notes, 80.0, window_s=window, hop_s=hop)
        for answer in ("combined", "melody_only", "chroma_only"):
            with self.subTest(answer=answer):
                self.assert_c_then_d(result[answer], window)
                for section in result[answer]:
                    self.assertGreater(section["score"], 0.5)

    def test_the_switch_penalty_keeps_one_noisy_window_from_flipping_the_key(self):
        scores = np.zeros((10, 24))
        scores[:, 0] = 0.8
        scores[:, 7] = 0.6
        scores[4, 7] = 0.9  # one window prefers G major
        self.assertEqual(set(melody.viterbi(scores, switch_penalty=1.0)), {0})
        self.assertIn(7, melody.viterbi(scores, switch_penalty=0.0))

    def test_profile_correlation_picks_the_rotated_key(self):
        for key in (0, 5, 12 + 9, 12 + 4):
            vector = np.roll(melody.PROFILES[key // 12], key % 12)
            self.assertEqual(int(np.argmax(melody.key_scores(vector[None, :])[0])), key)


class AgreementTests(unittest.TestCase):
    def test_median_cents_and_share_within_50(self):
        n = 100
        a = np.full(n, 440.0)
        b = np.full(n, 440.0)
        b[:60] *= 2 ** (10 / 1200)
        b[60:] *= 2 ** (-100 / 1200)
        c = np.full(n, 440.0)
        c[:20] = np.nan  # c unvoiced on 20 frames: only frames 20..99 count
        result = melody.agreement({"a": a, "b": b, "c": c})
        ab = result["a-b"]
        self.assertEqual(ab["frames"], 80)
        # 40 frames at 10 cents, 40 at 100 cents.
        self.assertAlmostEqual(ab["median_abs_cents"], 55.0, places=6)
        self.assertAlmostEqual(ab["within_50"], 0.5)
        self.assertAlmostEqual(result["a-c"]["median_abs_cents"], 0.0)
        self.assertAlmostEqual(result["a-c"]["within_50"], 1.0)
        self.assertEqual(set(result), {"a-b", "a-c", "b-c"})

    def test_no_shared_voiced_frames(self):
        a = np.array([440.0, np.nan])
        b = np.array([np.nan, 440.0])
        self.assertEqual(melody.agreement({"a": a, "b": b})["a-b"],
                         {"frames": 0, "median_abs_cents": None, "within_50": None})


class PreviewTests(unittest.TestCase):
    def test_length_and_no_clipping(self):
        notes = [{"onset": 0.2, "offset": 0.8, "midi": 69}, {"onset": 1.0, "offset": 1.9, "midi": 72}]
        n = 2 * RATE
        loud = 0.9 * np.ones((n, 2)) * np.sin(np.linspace(0, 400, n))[:, None]
        for lead in (None, 0.5 * loud):
            preview = melody.render_preview(loud, notes, gain_db=3.0, lead=lead)
            self.assertEqual(preview.shape, (n, 2))
            self.assertLessEqual(np.max(np.abs(preview)), melody.PREVIEW_CEILING)

    def test_synth_plays_the_notes(self):
        notes = [{"onset": 0.5, "offset": 1.0, "midi": 69}]
        synth = melody.synth(notes, RATE * 2)
        self.assertEqual(np.max(np.abs(synth[: int(0.5 * RATE)])), 0)
        self.assertEqual(np.max(np.abs(synth[RATE:])), 0)
        middle = synth[int(0.6 * RATE): int(0.9 * RATE)]
        spectrum = np.abs(np.fft.rfft(middle))
        peak_hz = np.argmax(spectrum) * RATE / len(middle)
        self.assertAlmostEqual(peak_hz, 440, delta=5)

    def test_preview_file_from_a_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = write_package(Path(tmp) / "pkg", 2.0, [(0.2, 0.5, 60)], progression(0, 0, 2.0))
            notes = [{"onset": 0.2, "offset": 0.7, "midi": 60, "cents": 0, "confidence": 1}]
            path = melody.write_preview(pkg, notes, pkg / "s6" / "preview.wav", with_lead=True)
            info = sf.info(str(path))
        self.assertEqual(info.samplerate, 44_100)
        self.assertEqual(info.frames, 2 * RATE)
        self.assertEqual(info.channels, 2)


class EndToEndTests(unittest.TestCase):
    def test_all_with_pyin_writes_every_output(self):
        sung = [(0.2 + 0.5 * i, 0.4, 60 + d) for i, d in enumerate((0, 2, 4, 5, 7, 5, 4, 2, 0, 4, 7, 12))]
        with tempfile.TemporaryDirectory() as tmp:
            pkg = write_package(Path(tmp) / "pkg", 6.5, sung, progression(0, 0, 6.5))
            with contextlib.redirect_stdout(io.StringIO()):
                melody.main(["all", str(pkg), "--trackers", "pyin", "--window", "4", "--hop", "1"])
            out = pkg / "s6"
            for name in ("f0-pyin.npz", "track-pyin.json", "notes-pyin.json", "notes-pyin.mid",
                         "keys-pyin.json", "preview-pyin.wav", "summary.json"):
                self.assertTrue((out / name).exists(), name)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            notes = json.loads((out / "notes-pyin.json").read_text(encoding="utf-8"))
            with np.load(out / "f0-pyin.npz") as f0:
                self.assertEqual(len(f0["times"]), len(f0["f0"]))
                self.assertAlmostEqual(float(f0["times"][1] - f0["times"][0]), HOP)
        self.assertEqual([n["midi"] for n in notes], [60 + d for d in (0, 2, 4, 5, 7, 5, 4, 2, 0, 4, 7, 12)])
        pyin = summary["trackers"]["pyin"]
        self.assertEqual(pyin["note_count"], 12)
        self.assertEqual(pyin["sections"][0]["key"], "C major")
        self.assertGreater(pyin["voiced_fraction"], 0.5)
        self.assertIn("seconds", pyin)
        self.assertEqual(summary["agreement"], {})


if __name__ == "__main__":
    unittest.main()
