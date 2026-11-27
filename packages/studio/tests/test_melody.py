"""The melody analysis on synthetic curves, with the tracker and the chroma faked."""

import inspect
import sys
import types

import numpy as np
import pytest
from studio import melody
from studio.audio import SAMPLE_RATE

HOP = melody.GRID_HOP
# I, IV, V, I of a major key, as semitones above the tonic.
PROGRESSION = ((0, 4, 7), (5, 9, 0), (7, 11, 2), (0, 4, 7))


def f0_curve(segments, seconds, vibrato_hz=5.5):
    """A 10 ms f0 curve (Hz, NaN unvoiced) from (onset_s, duration_s, midi, vibrato_cents)."""
    times = np.arange(round(seconds / HOP)) * HOP
    midi = np.full(len(times), np.nan)
    for onset, duration, pitch, vibrato in segments:
        mask = (times >= onset - 1e-9) & (times < onset + duration - 1e-9)
        midi[mask] = pitch + vibrato / 100 * np.sin(
            2 * np.pi * vibrato_hz * (times[mask] - onset)
        )
    return times, melody.midi_to_hz(midi)


def notes_of(times, f0):
    return melody.segment_notes(times, f0, np.where(np.isnan(f0), 0.0, 0.9))


def pitches(notes):
    return [round(note["midi"]) for note in notes]


def progression(tonic, start, end, chord_s=2.0):
    chords, t, i = [], start, 0
    while t < end:
        pcs = [(tonic + x) % 12 for x in PROGRESSION[i % 4]]
        chords.append((t, min(t + chord_s, end), pcs))
        t += chord_s
        i += 1
    return chords


# Pitch math ----------------------------------------------------------------


def test_hz_to_midi_and_back() -> None:
    assert float(melody.hz_to_midi(440.0)) == pytest.approx(69.0)
    assert float(melody.hz_to_midi(261.6256)) == pytest.approx(60.0, abs=1e-4)
    assert float(melody.midi_to_hz(69)) == pytest.approx(440.0)
    assert float(melody.midi_to_hz(57)) == pytest.approx(220.0)
    midi = np.array([40.25, 60.0, 83.5])
    np.testing.assert_allclose(melody.hz_to_midi(melody.midi_to_hz(midi)), midi)
    assert np.isnan(melody.hz_to_midi(np.nan))
    assert np.isnan(melody.midi_to_hz(np.nan))


def test_key_index_gives_tonic_and_mode() -> None:
    assert melody.key_of(0) == ("C", "major")
    assert melody.key_of(1) == ("C#", "major")
    assert melody.key_of(11) == ("B", "major")
    assert melody.key_of(12 + 9) == ("A", "minor")


# Common grid ---------------------------------------------------------------


def test_trackers_with_different_hops_land_on_the_common_grid() -> None:
    def glide(t):
        return melody.midi_to_hz(60 + 2 * t)  # 2 semitones per second

    grid = melody.grid_times(2.0)
    assert len(grid) == 200
    assert grid[1] - grid[0] == pytest.approx(HOP)
    # Tracker A: 10 ms hop. Tracker B: 256 samples at 22.05 kHz (11.6 ms), ends early.
    times_a = np.arange(205) * 0.01
    times_b = np.arange(150) * 256 / 22_050
    f0_a = melody.to_grid(times_a, glide(times_a), np.ones(205), grid)
    f0_b = melody.to_grid(times_b, glide(times_b), np.ones(150), grid)
    for f0, confidence in (f0_a, f0_b):
        assert len(f0) == len(grid)
        assert len(confidence) == len(grid)
    covered = grid <= times_b[-1]
    cents = 1200 * np.log2(f0_b[0][covered] / glide(grid[covered]))
    assert np.max(np.abs(cents)) < 13  # nearest frame within half a hop at 200 cents/s
    assert np.all(np.isnan(f0_b[0][grid > times_b[-1] + 0.01]))
    assert np.all(f0_b[1][grid > times_b[-1] + 0.01] == 0)
    np.testing.assert_allclose(f0_a[0], glide(grid))


def test_unvoiced_source_frames_stay_unvoiced() -> None:
    times = np.arange(100) * 0.01
    f0 = np.where(times < 0.5, 220.0, np.nan)

    grid_f0, _ = melody.to_grid(times, f0, np.ones(100), melody.grid_times(1.0))

    assert np.all(grid_f0[:50] == 220.0)
    assert np.all(np.isnan(grid_f0[50:]))


# Notes ---------------------------------------------------------------------


def test_notes_with_gaps_vibrato_and_a_blip() -> None:
    segments = [
        (0.50, 0.60, 60, 40),  # vibrato +-40 cents stays one note
        (1.10, 0.40, 62, 0),  # legato step up, no gap
        (1.70, 0.30, 64.2, 0),  # 20 cents sharp
        (2.20, 0.04, 70, 0),  # shorter than the minimum note: dropped
        (2.50, 0.50, 59, 25),
    ]
    times, f0 = f0_curve(segments, 3.5)
    # A 20 ms dropout inside the first note is bridged, not a new note.
    f0[(times >= 0.80) & (times < 0.82)] = np.nan

    notes = notes_of(times, f0)

    assert pitches(notes) == [60, 62, 64, 59]
    kept = [segment for segment in segments if segment[1] > 0.1]
    for note, (onset, duration, _, _) in zip(notes, kept, strict=True):
        assert note["start_s"] == pytest.approx(onset, abs=HOP / 2)
        assert note["end_s"] == pytest.approx(onset + duration, abs=HOP / 2)
    assert notes[2]["midi"] == pytest.approx(64.2, abs=0.03)


def test_a_note_keeps_its_median_pitch_rounded_to_2_decimals() -> None:
    times, f0 = f0_curve([(0.2, 0.5, 60.123456, 0)], 1.0)

    (note,) = notes_of(times, f0)

    assert note["midi"] == 60.12
    assert set(note) == {"start_s", "end_s", "midi"}
    assert note["start_s"] == round(note["start_s"], 3)
    assert note["end_s"] == round(note["end_s"], 3)


@pytest.mark.parametrize("cents", [60, 80])
@pytest.mark.parametrize("rate", [5.0, 5.5, 6.0])
def test_wide_vibrato_stays_one_note(cents: int, rate: float) -> None:
    times, f0 = f0_curve([(0.2, 1.5, 60, cents)], 2.0, vibrato_hz=rate)

    notes = notes_of(times, f0)

    assert pitches(notes) == [60]
    assert notes[0]["start_s"] == pytest.approx(0.2, abs=HOP / 2)
    assert notes[0]["end_s"] == pytest.approx(1.7, abs=HOP / 2)


@pytest.mark.parametrize("cents", [0, 60])
def test_a_held_step_with_vibrato_splits_at_the_step(cents: int) -> None:
    times, f0 = f0_curve([(0.2, 0.6, 60, cents), (0.8, 0.6, 62, cents)], 1.6)

    notes = notes_of(times, f0)

    assert pitches(notes) == [60, 62]
    assert notes[0]["end_s"] == pytest.approx(0.8, abs=HOP / 2)
    assert notes[1]["start_s"] == pytest.approx(0.8, abs=HOP / 2)


def test_a_quick_slide_between_two_held_notes_gives_the_two_notes() -> None:
    # 4 semitones in 200 ms. Slower slides keep notes on the pitches passed (S6).
    times = np.arange(160) * HOP
    midi = np.where(times >= 0.2 - 1e-9, 60.0, np.nan)
    glide = (times >= 0.7 - 1e-9) & (times < 0.9 - 1e-9)
    midi[glide] = 60 + 4 * (times[glide] - 0.7 + HOP) / 0.2
    midi[times >= 0.9 - 1e-9] = 64.0

    notes = notes_of(times, melody.midi_to_hz(midi))

    assert pitches(notes) == [60, 64]


def test_low_confidence_frames_are_dropped() -> None:
    times, f0 = f0_curve([(0.0, 1.0, 60, 0)], 1.0)
    confidence = np.where(times < 0.5, 0.9, 0.0)

    notes = melody.segment_notes(times, f0, confidence, min_confidence=0.5)

    assert len(notes) == 1
    assert notes[0]["end_s"] == pytest.approx(0.5, abs=0.02)


def test_no_voiced_frame_gives_no_notes() -> None:
    times = np.arange(100) * HOP

    assert melody.segment_notes(times, np.full(100, np.nan), np.zeros(100)) == []


# Key -----------------------------------------------------------------------


def synthetic_song():
    """C major for 40 s, then D major for 40 s: chroma frames and a melody on the chord tones."""
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
            steps = np.arange(c_start, c_end, 0.5)
            for t, pc in zip(steps, (pcs[0], pcs[1], pcs[2], pcs[0]), strict=False):
                notes.append(
                    {"start_s": float(t), "end_s": float(t + 0.4), "midi": 60 + pc}
                )
    return times, chroma, notes


def assert_c_then_d(sections, hop) -> None:
    assert [(s["tonic"], s["mode"]) for s in sections] == [
        ("C", "major"),
        ("D", "major"),
    ]
    assert abs(sections[1]["start_s"] - 40) <= hop
    assert sections[0]["start_s"] == 0.0
    assert sections[-1]["end_s"] == 80.0
    assert sections[0]["end_s"] == sections[1]["start_s"]


def test_keys_find_the_change_from_melody_and_chroma() -> None:
    times, chroma, notes = synthetic_song()

    sections = melody.detect_keys(chroma, times, notes, 80.0, window_s=8.0, hop_s=2.0)

    assert_c_then_d(sections, 2.0)


@pytest.mark.parametrize("source", ["melody", "chroma"])
def test_keys_find_the_change_from_either_source_alone(source: str) -> None:
    times, chroma, notes = synthetic_song()
    if source == "melody":
        chroma = np.zeros_like(chroma)
    else:
        notes = []

    sections = melody.detect_keys(chroma, times, notes, 80.0, window_s=8.0, hop_s=2.0)

    assert_c_then_d(sections, 2.0)


def test_keys_with_the_s6_defaults_still_find_the_change() -> None:
    times, chroma, notes = synthetic_song()

    sections = melody.detect_keys(chroma, times, notes, 80.0)

    assert [(s["tonic"], s["mode"]) for s in sections] == [
        ("C", "major"),
        ("D", "major"),
    ]
    assert abs(sections[1]["start_s"] - 40) <= 4
    assert set(sections[0]) == {"start_s", "end_s", "tonic", "mode"}


def test_a_note_counts_for_the_key_by_its_nearest_semitone() -> None:
    # An E major triad sung 40 cents flat: 63.6 is E, not D#.
    notes = [
        {"start_s": 0.0, "end_s": 1.0, "midi": 63.6},
        {"start_s": 1.0, "end_s": 2.0, "midi": 67.6},
        {"start_s": 2.0, "end_s": 3.0, "midi": 70.6},
    ]
    times = np.arange(30) / 10

    sections = melody.detect_keys(np.zeros((12, 30)), times, notes, 3.0)

    assert [(s["tonic"], s["mode"]) for s in sections] == [("E", "major")]


def test_a_song_shorter_than_a_window_gets_one_key() -> None:
    times = np.arange(50) / 10
    chroma = np.full((12, 50), 0.05)
    chroma[[9, 0, 4], :] = 1.0  # A minor triad
    notes = [{"start_s": 0.0, "end_s": 4.0, "midi": 69.0}]

    sections = melody.detect_keys(chroma, times, notes, 5.0)

    assert sections == [{"start_s": 0.0, "end_s": 5.0, "tonic": "A", "mode": "minor"}]


def test_the_switch_penalty_keeps_one_noisy_window_from_flipping_the_key() -> None:
    scores = np.zeros((10, 24))
    scores[:, 0] = 0.8
    scores[:, 7] = 0.6
    scores[4, 7] = 0.9  # one window prefers G major

    assert set(melody.viterbi(scores, switch_penalty=1.0)) == {0}
    assert 7 in melody.viterbi(scores, switch_penalty=0.0)


@pytest.mark.parametrize("key", [0, 5, 12 + 9, 12 + 4])
def test_profile_correlation_picks_the_rotated_key(key: int) -> None:
    vector = np.roll(melody.PROFILES[key // 12], key % 12)

    assert int(np.argmax(melody.key_scores(vector[None, :])[0])) == key


# The analysis of a package's stems ------------------------------------------


class FakeTracker:
    """Stands in for RMVPE: a 10 ms curve of one held note, quiet frames below the threshold."""

    def __init__(self, midi=67.0, confidence=0.5):
        self.midi = midi
        self.confidence = confidence
        self.calls: list[tuple[np.ndarray, int]] = []

    def __call__(self, mono: np.ndarray, rate: int):
        self.calls.append((mono, rate))
        times = np.arange(int(len(mono) / rate / 0.01)) * 0.01
        frequency = np.full(len(times), float(melody.midi_to_hz(self.midi)))
        confidence = np.where((times >= 0.5) & (times < 1.5), self.confidence, 0.01)
        return times, frequency, confidence


class FakeChroma:
    """Stands in for librosa's chroma: a G major triad on every frame."""

    def __init__(self):
        self.calls: list[tuple[np.ndarray, int]] = []

    def __call__(self, mono: np.ndarray, rate: int):
        self.calls.append((mono, rate))
        times = np.arange(int(len(mono) / rate * 10)) / 10
        chroma = np.full((12, len(times)), 0.05)
        chroma[[7, 11, 2], :] = 1.0
        return chroma, times


def make_stems(seconds=2.0):
    n = int(seconds * SAMPLE_RATE)
    stems = {}
    for index, name in enumerate(
        ("lead", "backing", "drums", "bass", "guitar", "piano", "other")
    ):
        left = np.full(n, 0.01 * (index + 1), dtype=np.float32)
        stems[name] = np.stack([left, 3 * left], axis=1)
    return stems


def test_analysis_tracks_the_lead_and_keys_on_the_instrumental() -> None:
    stems = make_stems()
    tracker, chroma = FakeTracker(), FakeChroma()

    document = melody.analyze(stems, tracker, chroma)

    ((lead, lead_rate),) = tracker.calls
    assert lead_rate == SAMPLE_RATE
    np.testing.assert_allclose(lead, stems["lead"].mean(axis=1))
    ((instrumental, chroma_rate),) = chroma.calls
    assert chroma_rate == SAMPLE_RATE
    expected = sum(
        stems[name] for name in ("drums", "bass", "guitar", "piano", "other")
    )
    np.testing.assert_allclose(instrumental, expected.mean(axis=1), rtol=1e-6)
    assert document == {
        "notes": [{"start_s": 0.5, "end_s": 1.5, "midi": 67.0}],
        "keys": [{"start_s": 0.0, "end_s": 2.0, "tonic": "G", "mode": "major"}],
    }


def test_frames_under_the_rmvpe_threshold_are_unvoiced() -> None:
    document = melody.analyze(
        make_stems(),
        FakeTracker(confidence=melody.RMVPE_THRESHOLD - 0.001),
        FakeChroma(),
    )

    assert document["notes"] == []
    assert [(k["tonic"], k["mode"]) for k in document["keys"]] == [("G", "major")]


def test_the_analysis_keeps_the_s6_settings() -> None:
    # docs/PLAN.md section 5.2 and the S6 results: a change here is a new
    # analysis, judged by ear again, not a refactor.
    def defaults(function):
        return {
            name: parameter.default
            for name, parameter in inspect.signature(function).parameters.items()
            if parameter.default is not inspect.Parameter.empty
        }

    assert melody.RMVPE_THRESHOLD == 0.03
    assert melody.GRID_HOP == 0.01
    assert defaults(melody.segment_notes) == {
        "min_confidence": 0.0,
        "smooth_frames": 19,
        "split_semitones": 0.7,
        "split_frames": 3,
        "max_gap_s": 0.03,
        "min_note_s": 0.08,
    }
    assert defaults(melody.detect_keys) == {
        "window_s": 16.0,
        "hop_s": 2.0,
        "melody_weight": 0.5,
        "switch_penalty": 6.0,
    }


def test_rmvpe_gets_the_model_path_and_checks_the_model_itself(
    monkeypatch, tmp_path
) -> None:
    # RMVPE's constructor downloads the model when missing and hashes it
    # (rmvpe_onnx 0.2.3), so calling ensure_model too would hash it twice.
    made: list[tuple] = []

    class FakeRMVPE:
        def __init__(self, model_path, device=None):
            made.append((model_path, device))

        def predict(self, audio, rate):
            times = np.arange(3) * HOP
            return times, np.full(3, 220.0), np.full(3, 0.9), None

    def ensure_model(model_path=None):
        raise AssertionError("ensure_model hashes the model a second time")

    fake = types.ModuleType("rmvpe_onnx")
    fake.RMVPE, fake.ensure_model = FakeRMVPE, ensure_model
    monkeypatch.setitem(sys.modules, "rmvpe_onnx", fake)

    times, frequency, confidence = melody.rmvpe_track(
        np.zeros(SAMPLE_RATE), SAMPLE_RATE, tmp_path
    )

    assert made == [(tmp_path / "rmvpe.onnx", "cpu")]
    np.testing.assert_allclose(frequency, 220.0)
    assert len(times) == len(confidence) == 3


def test_the_document_holds_plain_python_numbers() -> None:
    document = melody.analyze(make_stems(), FakeTracker(), FakeChroma())

    for row in document["notes"] + document["keys"]:
        for value in row.values():
            assert type(value) in (float, str)
