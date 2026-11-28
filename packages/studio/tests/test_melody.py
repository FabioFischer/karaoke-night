"""The melody analysis on synthetic data, with RMVPE, SOME and the chroma faked."""

import inspect
import io
import sys
import types
import zipfile
from pathlib import Path

import numpy as np
import pytest
from studio import melody
from studio.audio import SAMPLE_RATE

HOP = melody.GRID_HOP
# I, IV, V, I of a major key, as semitones above the tonic.
PROGRESSION = ((0, 4, 7), (5, 9, 0), (7, 11, 2), (0, 4, 7))


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


def chunk(offset, durations, rests):
    """One chunk as SOME's inference gives it, its note_midi left to RMVPE."""
    return {
        "offset": offset,
        "note_midi": np.full(len(durations), 60.0),
        "note_dur": np.array(durations, dtype=float),
        "note_rest": np.array(rests, dtype=bool),
    }


def test_notes_walk_each_chunk_from_its_offset_skipping_rests() -> None:
    chunks = [
        chunk(1.0, [0.25, 0.5, 0.125], [False, True, False]),
        chunk(4.0, [0.5], [False]),
        chunk(6.0, [0.75], [True]),
    ]

    assert melody.notes_from_chunks(chunks) == [
        (1.0, 1.25),
        (1.75, 1.875),
        (4.0, 4.5),
    ]


def test_no_chunk_gives_no_notes() -> None:
    assert melody.notes_from_chunks([]) == []


def test_a_note_takes_the_median_of_its_middle_frames() -> None:
    midi = np.arange(100, dtype=float)  # frame i holds the value i

    # Frames 10 to 29, 4 trimmed from each end: 14 to 25.
    assert melody.note_value(midi, 0.10, 0.30) == 19.5
    # 5 frames, 1 trimmed from each end: 11 to 13.
    assert melody.note_value(midi, 0.10, 0.15) == 12.0


def test_the_trim_rounds_down_to_whole_frames() -> None:
    midi = np.full(100, np.nan)
    # 8 frames: 20% is 1.6 frames, so 1 is trimmed from each end, not 2.
    midi[10:18] = [0.0, 70.0, 60.0, 61.0, 62.0, 63.0, 70.0, 0.0]

    assert melody.note_value(midi, 0.10, 0.18) == 62.5


def test_a_note_takes_the_median_not_the_mean() -> None:
    midi = np.full(100, np.nan)
    # One frame an octave up: the median stays on the note, the mean would be 63.
    midi[10:14] = [60.0, 60.0, 60.0, 72.0]

    assert melody.note_value(midi, 0.10, 0.14) == 60.0


def test_a_note_of_4_frames_or_fewer_keeps_them_all() -> None:
    midi = np.arange(100, dtype=float)

    assert melody.note_value(midi, 0.10, 0.14) == 11.5
    assert melody.note_value(midi, 0.10, 0.11) == 10.0


def test_unvoiced_frames_do_not_count_for_the_pitch() -> None:
    midi = np.arange(100, dtype=float)
    midi[14:18] = np.nan

    assert melody.note_value(midi, 0.10, 0.30) == 21.5


def test_a_note_without_a_voiced_frame_has_no_pitch() -> None:
    midi = np.full(100, np.nan)

    assert melody.note_value(midi, 0.10, 0.30) is None
    assert melody.note_value(np.arange(10, dtype=float), 0.5, 0.7) is None


C_MAJOR = {"start_s": 0.0, "end_s": 10.0, "tonic": "C", "mode": "major"}
A_MINOR = {"start_s": 0.0, "end_s": 10.0, "tonic": "A", "mode": "minor"}


@pytest.mark.parametrize(
    ("value", "key", "expected"),
    [
        (64.4, C_MAJOR, 64),  # E, in the key: the nearest semitone
        (61.3, C_MAJOR, 62),  # C# is out, the voice is above it: up to D
        (60.7, C_MAJOR, 60),  # C# is out, the voice is below it: down to C
        (68.2, A_MINOR, 69),  # G# is out of A minor: up to A
        (67.6, A_MINOR, 67),  # G# is out of A minor: down to G
        (69.0, A_MINOR, 69),
        (61.0, C_MAJOR, 61),  # exactly on C#: the ceiling of 61.0 is 61
    ],
)
def test_a_pitch_outside_the_key_goes_to_the_side_the_voice_is_on(
    value: float, key: dict, expected: int
) -> None:
    pitch = melody.snap_to_key(value, key)

    assert pitch == expected
    assert type(pitch) is int


def test_the_key_of_a_note_is_the_section_at_its_start() -> None:
    keys = [
        {"start_s": 0.0, "end_s": 10.0, "tonic": "C", "mode": "major"},
        {"start_s": 10.0, "end_s": 20.0, "tonic": "D", "mode": "major"},
    ]

    assert melody.key_at(keys, 0.0) == keys[0]
    assert melody.key_at(keys, 9.99) == keys[0]
    assert melody.key_at(keys, 10.0) == keys[1]
    assert melody.key_at(keys, 25.0) == keys[1]


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
    """Stands in for RMVPE: a 10 ms curve holding each (start_s, end_s, midi)
    segment, the frames between them below the threshold."""

    def __init__(self, segments=((0.5, 1.5, 67.0),), confidence=0.5):
        self.segments = segments
        self.confidence = confidence
        self.calls: list[tuple[np.ndarray, int]] = []

    def __call__(self, mono: np.ndarray, rate: int):
        self.calls.append((mono, rate))
        times = np.arange(int(len(mono) / rate / 0.01)) * 0.01
        midi = np.full(len(times), 40.0)
        confidence = np.full(len(times), 0.01)
        for start, end, pitch in self.segments:
            inside = (times >= start - 1e-9) & (times < end - 1e-9)
            midi[inside] = pitch
            confidence[inside] = self.confidence
        return times, melody.midi_to_hz(midi), confidence


class FakeSome:
    """Stands in for SOME: the chunks it was given."""

    def __init__(self, chunks=None):
        self.chunks = [chunk(0.5, [1.0], [False])] if chunks is None else chunks
        self.calls: list[tuple[np.ndarray, int]] = []

    def __call__(self, mono: np.ndarray, rate: int):
        self.calls.append((mono, rate))
        return self.chunks


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


def test_analysis_tracks_and_splits_the_lead_and_keys_on_the_instrumental() -> None:
    stems = make_stems()
    tracker, some, chroma = FakeTracker(), FakeSome(), FakeChroma()

    document = melody.analyze(stems, tracker, some, chroma)

    for calls in (tracker.calls, some.calls):
        ((lead, lead_rate),) = calls
        assert lead_rate == SAMPLE_RATE
        np.testing.assert_allclose(lead, stems["lead"].mean(axis=1))
    ((instrumental, chroma_rate),) = chroma.calls
    assert chroma_rate == SAMPLE_RATE
    expected = sum(
        stems[name] for name in ("drums", "bass", "guitar", "piano", "other")
    )
    np.testing.assert_allclose(instrumental, expected.mean(axis=1), rtol=1e-6)
    assert document == {
        "notes": [{"start_s": 0.5, "end_s": 1.5, "midi": 67}],
        "keys": [{"start_s": 0.0, "end_s": 2.0, "tonic": "G", "mode": "major"}],
    }


def test_keys_come_from_the_pitches_before_the_snap(monkeypatch) -> None:
    tracker = FakeTracker(((0.5, 1.0, 64.3), (1.0, 1.5, 65.4)))
    some = FakeSome(
        [
            chunk(0.5, [0.5, 0.5], [False, False]),
            # A rest, then a note RMVPE hears nothing in: both dropped.
            chunk(1.8, [0.2, 0.5], [True, False]),
        ]
    )
    g_major = [{"start_s": 0.0, "end_s": 3.0, "tonic": "G", "mode": "major"}]
    detected: list[tuple] = []

    def detect_keys(chroma, chroma_times, notes, duration):
        detected.append((chroma, chroma_times, notes, duration))
        return g_major

    monkeypatch.setattr(melody, "detect_keys", detect_keys)

    document = melody.analyze(make_stems(3.0), tracker, some, FakeChroma())

    ((chroma, chroma_times, notes, duration),) = detected
    assert chroma.shape == (12, 30)
    assert len(chroma_times) == 30
    assert duration == 3.0
    assert [(n["start_s"], n["end_s"]) for n in notes] == [(0.5, 1.0), (1.0, 1.5)]
    assert [n["midi"] for n in notes] == [
        pytest.approx(64.3, abs=1e-6),
        pytest.approx(65.4, abs=1e-6),
    ]
    # F is out of G major and the voice is above it: F#.
    assert document == {
        "notes": [
            {"start_s": 0.5, "end_s": 1.0, "midi": 64},
            {"start_s": 1.0, "end_s": 1.5, "midi": 66},
        ],
        "keys": g_major,
    }


def test_a_note_across_a_key_change_snaps_to_the_key_at_its_start(
    monkeypatch,
) -> None:
    # The note runs from 0.5 s to 1.5 s, the key changes at 1.0 s. C# is out of
    # C major, so the voice above it goes up to D; in D major it stays C#.
    keys = [
        {"start_s": 0.0, "end_s": 1.0, "tonic": "C", "mode": "major"},
        {"start_s": 1.0, "end_s": 2.0, "tonic": "D", "mode": "major"},
    ]
    monkeypatch.setattr(melody, "detect_keys", lambda *args: keys)
    tracker = FakeTracker(((0.5, 1.5, 61.3),))

    document = melody.analyze(make_stems(), tracker, FakeSome(), FakeChroma())

    assert document["notes"] == [{"start_s": 0.5, "end_s": 1.5, "midi": 62}]


def test_note_times_keep_3_decimals() -> None:
    some = FakeSome([chunk(0.123456, [0.5], [False])])
    tracker = FakeTracker(((0.0, 2.0, 67.0),))

    document = melody.analyze(make_stems(), tracker, some, FakeChroma())

    assert document["notes"] == [{"start_s": 0.123, "end_s": 0.623, "midi": 67}]


def test_frames_under_the_rmvpe_threshold_are_unvoiced() -> None:
    document = melody.analyze(
        make_stems(),
        FakeTracker(confidence=melody.RMVPE_THRESHOLD - 0.001),
        FakeSome(),
        FakeChroma(),
    )

    assert document["notes"] == []
    assert [(k["tonic"], k["mode"]) for k in document["keys"]] == [("G", "major")]


def test_the_analysis_keeps_the_settings_chosen_by_ear() -> None:
    # docs/PLAN.md section 5.2 and the results of beta item 2: a change here
    # is a new analysis, judged by ear again, not a refactor.
    def defaults(function):
        return {
            name: parameter.default
            for name, parameter in inspect.signature(function).parameters.items()
            if parameter.default is not inspect.Parameter.empty
        }

    assert melody.RMVPE_THRESHOLD == 0.03
    assert melody.GRID_HOP == 0.01
    assert melody.NOTE_TRIM == 0.2
    assert melody.SLICER_MAX_SIL_KEPT == 1000
    assert melody.SCALES == {
        "major": (0, 2, 4, 5, 7, 9, 11),
        "minor": (0, 2, 3, 5, 7, 8, 10),
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
    document = melody.analyze(make_stems(), FakeTracker(), FakeSome(), FakeChroma())

    for note in document["notes"]:
        assert [type(note[k]) for k in ("start_s", "end_s", "midi")] == [
            float,
            float,
            int,
        ]
    for row in document["keys"]:
        for value in row.values():
            assert type(value) in (float, str)


# SOME -----------------------------------------------------------------------


def tone(seconds, rate=SAMPLE_RATE):
    t = np.arange(int(seconds * rate)) / rate
    return 0.5 * np.sin(2 * np.pi * 220 * t)


def test_the_lead_is_sliced_at_its_silences_and_each_chunk_inferred() -> None:
    # 6 s sung, 2 s of silence, 6 s sung: two chunks, the silence cut down.
    mono = np.concatenate([tone(6), np.zeros(2 * SAMPLE_RATE), tone(6)])
    given: list[list[np.ndarray]] = []

    def infer(waveforms):
        given.append(waveforms)
        return [
            {
                "note_midi": np.array([57.0]),
                "note_dur": np.array([len(w) / SAMPLE_RATE]),
                "note_rest": np.array([False]),
            }
            for w in waveforms
        ]

    chunks = melody.slice_and_infer(mono, SAMPLE_RATE, infer)

    ((first, second),) = given
    assert first.dtype == second.dtype == np.float32
    assert chunks[0]["offset"] == 0
    assert 6.0 < chunks[1]["offset"] < 8.0
    np.testing.assert_array_equal(first, mono[: len(first)].astype(np.float32))
    start = round(chunks[1]["offset"] * SAMPLE_RATE)
    np.testing.assert_array_equal(
        second, mono[start : start + len(second)].astype(np.float32)
    )
    assert len(first) + len(second) < len(mono)
    for c, w in zip(chunks, (first, second), strict=True):
        assert set(c) == {"offset", "note_midi", "note_dur", "note_rest"}
        assert c["note_dur"][0] == len(w) / SAMPLE_RATE


def model_zip(checkpoint=b"weights") -> bytes:
    """The release's zip: a folder named 256 in a zip named 128, as shipped."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("0119_continuous256_5spk/config.yaml", "units_dim: 80\n")
        archive.writestr(
            "0119_continuous256_5spk/model_ckpt_steps_100000_simplified.ckpt",
            checkpoint,
        )
    return buffer.getvalue()


class FakeFetch:
    """Stands in for the download: writes data to the path it is given."""

    def __init__(self, data: bytes, fail: Exception | None = None):
        self.data = data
        self.fail = fail
        self.calls: list[tuple[str, Path]] = []

    def __call__(self, url: str, path: Path) -> None:
        self.calls.append((url, path))
        path.write_bytes(self.data)
        if self.fail is not None:
            raise self.fail


def test_the_some_model_is_downloaded_and_unpacked_on_first_use(tmp_path) -> None:
    model_dir = tmp_path / "models"
    fetch = FakeFetch(model_zip())

    checkpoint = melody.ensure_some_model(model_dir, fetch)

    folder = model_dir / "some" / "0119_continuous256_5spk"
    assert checkpoint == folder / "model_ckpt_steps_100000_simplified.ckpt"
    assert checkpoint.read_bytes() == b"weights"
    assert (folder / "config.yaml").read_text() == "units_dim: 80\n"
    url = (
        "https://github.com/openvpi/SOME/releases/download/v1.0.0-baseline/"
        "0119_continuous128_5spk.zip"
    )
    part = model_dir / "some" / "0119_continuous128_5spk.zip.part"
    assert fetch.calls == [(url, part)]
    assert [p.name for p in (model_dir / "some").iterdir()] == [folder.name]


def test_a_model_already_there_is_not_downloaded_again(tmp_path) -> None:
    melody.ensure_some_model(tmp_path, FakeFetch(model_zip()))
    fetch = FakeFetch(b"")

    checkpoint = melody.ensure_some_model(tmp_path, fetch)

    assert fetch.calls == []
    assert checkpoint.read_bytes() == b"weights"


@pytest.mark.parametrize(
    ("data", "fail", "error"),
    [
        (model_zip()[:1000], OSError("connection reset"), OSError),
        (b"not a zip", None, zipfile.BadZipFile),
    ],
    ids=["interrupted", "broken"],
)
def test_a_broken_download_is_never_taken_for_the_model(
    tmp_path, data: bytes, fail: Exception | None, error: type
) -> None:
    with pytest.raises(error):
        melody.ensure_some_model(tmp_path, FakeFetch(data, fail))

    assert not (tmp_path / "some" / "0119_continuous256_5spk").exists()
    fetch = FakeFetch(model_zip())
    checkpoint = melody.ensure_some_model(tmp_path, fetch)
    assert len(fetch.calls) == 1
    assert checkpoint.read_bytes() == b"weights"
    assert [p.name for p in (tmp_path / "some").iterdir()] == [
        "0119_continuous256_5spk"
    ]


def test_some_runs_on_the_cpu_with_the_config_next_to_its_checkpoint(
    monkeypatch, tmp_path
) -> None:
    melody.ensure_some_model(tmp_path, FakeFetch(model_zip()))
    made: list[tuple] = []

    class FakeInference:
        def __init__(self, config, model_path, device=None):
            made.append((config, model_path, device))

        def infer(self, waveforms):
            return [
                {"note_midi": [], "note_dur": [], "note_rest": []} for _ in waveforms
            ]

    inference = types.ModuleType("studio.some.inference.me_infer")
    inference.MIDIExtractionInference = FakeInference
    yaml = types.ModuleType("yaml")
    yaml.safe_load = lambda text: {"parsed": text}
    monkeypatch.setitem(sys.modules, "studio.some.inference.me_infer", inference)
    monkeypatch.setitem(sys.modules, "yaml", yaml)

    chunks = melody.some_notes(np.zeros(SAMPLE_RATE), SAMPLE_RATE, tmp_path)

    folder = tmp_path / "some" / "0119_continuous256_5spk"
    assert made == [
        (
            {"parsed": "units_dim: 80\n"},
            folder / "model_ckpt_steps_100000_simplified.ckpt",
            "cpu",
        )
    ]
    assert chunks == [{"offset": 0, "note_midi": [], "note_dur": [], "note_rest": []}]
