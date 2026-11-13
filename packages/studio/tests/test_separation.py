from pathlib import Path

import numpy as np
import pytest
from studio.separation import (
    HEADROOM,
    MODELS,
    NORMALIZATION_THRESHOLD,
    OVERLAP,
    STEMS,
    cascade,
    pick,
    separator_options,
    stem_from_filename,
)


class LibraryLikeSeparator:
    """Wraps a separator with what audio-separator 0.48.0 does around a model.

    Every output that peaks above the normalization threshold is scaled down to
    it on its own, and an MDXC model does the same to its input.
    """

    def __init__(self, inner, threshold=NORMALIZATION_THRESHOLD):
        self.inner = inner
        self.threshold = threshold

    def _normalize(self, audio):
        peak = float(np.max(np.abs(audio)))
        return audio * (self.threshold / peak) if peak > self.threshold else audio

    def __call__(self, step, model_file, audio):
        if step in ("vocals", "karaoke"):
            audio = self._normalize(audio)
        outputs = self.inner(step, model_file, audio)
        return {
            name: self._normalize(out).astype(np.float32)
            for name, out in outputs.items()
        }


def test_cascade_gives_the_seven_stems_in_order(mix, fake_separator) -> None:
    stems = cascade(mix, fake_separator(), MODELS)

    assert tuple(stems) == STEMS
    for audio in stems.values():
        assert audio.shape == mix.shape


def test_cascade_stems_add_up_to_the_mix(mix, fake_separator) -> None:
    stems = cascade(mix, fake_separator(), MODELS)

    np.testing.assert_allclose(sum(stems.values()), mix, atol=1e-6)


def test_cascade_runs_each_model_on_its_input_at_headroom(mix, fake_separator) -> None:
    fake = fake_separator()

    cascade(mix, fake, MODELS)

    steps = [(step, model) for step, model, _ in fake.calls]
    assert steps == [
        ("vocals", MODELS["vocals"]),
        ("karaoke", MODELS["karaoke"]),
        ("instruments", MODELS["instruments"]),
    ]
    np.testing.assert_allclose(fake.calls[0][2], mix * HEADROOM)
    vocals = mix * fake.vocals_gain
    np.testing.assert_allclose(fake.calls[1][2], vocals * HEADROOM, atol=1e-6)
    np.testing.assert_allclose(fake.calls[2][2], (mix - vocals) * HEADROOM, atol=1e-6)


@pytest.mark.parametrize(
    "gains",
    [
        # At full level the vocals peak at 1.17 and the lead at about 1.05.
        {"vocals_gain": 1.3, "lead_gain": 0.9},
        # At full level the instrumental peaks at 1.35 and the drums at about 1.2.
        {"vocals_gain": -0.5, "drums_gain": 0.9},
    ],
    ids=["loud-vocals", "loud-drums"],
)
def test_cascade_keeps_levels_through_the_library_normalization(
    synthetic_mix, fake_separator, gains
) -> None:
    loud = synthetic_mix(peak=0.9)
    expected = cascade(loud, fake_separator(**gains), MODELS)
    levels: dict = {}

    stems = cascade(loud, LibraryLikeSeparator(fake_separator(**gains)), MODELS, levels)

    for name in STEMS:
        np.testing.assert_allclose(stems[name], expected[name], atol=1e-6)
    assert not any(row["reached_threshold"] for row in levels.values())


def test_cascade_flags_a_step_that_reached_the_threshold(
    synthetic_mix, fake_separator
) -> None:
    loud = synthetic_mix(peak=0.9)
    levels: dict = {}

    stems = cascade(
        loud, LibraryLikeSeparator(fake_separator(vocals_gain=2.5)), MODELS, levels
    )

    assert levels["vocals"]["reached_threshold"] is True
    assert levels["karaoke"]["reached_threshold"] is False
    np.testing.assert_allclose(sum(stems.values()), loud, atol=1e-6)


def test_pick_matches_the_name_ignoring_case() -> None:
    vocals = np.ones((2, 2))

    assert pick(
        {"Instrumental": np.zeros((2, 2)), "Vocals": vocals}, "vocals", "m"
    ) is (vocals)


def test_pick_missing_name_lists_what_the_model_gave() -> None:
    with pytest.raises(KeyError, match="karaoke-model.ckpt.*lead, other"):
        pick(
            {"Lead": np.zeros(1), "Other": np.zeros(1)}, "vocals", "karaoke-model.ckpt"
        )


def test_models_are_combination_b() -> None:
    assert MODELS == {
        "vocals": "vocals_mel_band_roformer.ckpt",
        "karaoke": "mel_band_roformer_karaoke_becruily.ckpt",
        "instruments": "htdemucs_6s.yaml",
    }
    assert OVERLAP == 2


def test_separator_options_set_the_threshold_and_overlap(tmp_path: Path) -> None:
    options = separator_options(tmp_path / "models", tmp_path / "out")

    assert options["normalization_threshold"] == 1.0
    assert options["model_file_dir"] == str(tmp_path / "models")
    assert options["output_dir"] == str(tmp_path / "out")
    assert options["mdxc_params"]["overlap"] == 2
    assert options["mdxc_params"]["segment_size"] == 256


def test_stem_from_filename_reads_the_stem_name() -> None:
    name = "vocals_input_(Vocals)_vocals_mel_band_roformer.wav"

    assert stem_from_filename(name, "vocals_input") == "Vocals"
    with pytest.raises(ValueError):
        stem_from_filename("other.wav", "vocals_input")
