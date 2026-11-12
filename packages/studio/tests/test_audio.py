from pathlib import Path

import numpy as np
from studio.audio import (
    PCM16_CEILING,
    SAMPLE_RATE,
    align,
    ffmpeg_command,
    fit_headroom,
    to_pcm16,
)


def test_align_trims_a_longer_output() -> None:
    audio = np.arange(20, dtype=np.float32).reshape(10, 2)

    np.testing.assert_array_equal(align(audio, 7), audio[:7])


def test_align_pads_a_shorter_output_with_silence() -> None:
    audio = np.ones((5, 2), dtype=np.float32)

    aligned = align(audio, 8)

    assert aligned.shape == (8, 2)
    assert aligned.dtype == np.float32
    np.testing.assert_array_equal(aligned[:5], audio)
    np.testing.assert_array_equal(aligned[5:], 0)


def test_fit_headroom_without_clipping_keeps_the_stems() -> None:
    stems = {"a": np.full((4, 2), 0.5, dtype=np.float32), "b": np.zeros((4, 2))}

    fitted, gain_db = fit_headroom(stems)

    assert gain_db == 0.0
    assert fitted is stems


def test_fit_headroom_with_clipping_scales_every_stem_by_one_factor() -> None:
    loud = np.full((4, 2), 1.6, dtype=np.float32)
    quiet = np.full((4, 2), -0.4, dtype=np.float32)

    fitted, gain_db = fit_headroom({"loud": loud, "quiet": quiet})

    assert gain_db > 0
    assert round(gain_db, 3) == gain_db
    factor = 10 ** (-gain_db / 20)
    np.testing.assert_allclose(fitted["loud"], loud * factor, rtol=1e-6)
    np.testing.assert_allclose(fitted["quiet"], quiet * factor, rtol=1e-6)
    assert np.max(np.abs(fitted["loud"])) <= PCM16_CEILING
    # Rounded up to 3 decimals, the loudest peak lands at most 0.001 dB under the ceiling.
    assert np.max(np.abs(fitted["loud"])) >= PCM16_CEILING * 10 ** (-0.001 / 20)
    assert fitted["loud"].dtype == np.float32


def test_to_pcm16_rounds_and_clips() -> None:
    audio = np.array([[0.0, 1.0], [-1.0, 1.5], [1 / 32_768, -2.0]])

    np.testing.assert_array_equal(
        to_pcm16(audio), [[0, 32_767], [-32_768, 32_767], [1, -32_768]]
    )


def test_ffmpeg_command_converts_to_a_44k1_16_bit_stereo_wav() -> None:
    command = ffmpeg_command(Path("in.m4a"), Path("mix.wav"))

    assert command[0] == "ffmpeg"
    assert command[command.index("-i") + 1] == "in.m4a"
    assert command[command.index("-ac") + 1] == "2"
    assert command[command.index("-ar") + 1] == str(SAMPLE_RATE)
    assert command[command.index("-c:a") + 1] == "pcm_s16le"
    assert command[-1] == "mix.wav"
