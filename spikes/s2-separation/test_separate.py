"""Tests for separate.py with a fake separator. No GPU, no network.

Run from the repo root:
PYTHONDONTWRITEBYTECODE=1 uv run --with numpy --with soundfile python -m unittest discover -s spikes/s2-separation -v
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

import separate

RATE = separate.SAMPLE_RATE
SOURCE = {"video_id": "XXXXXXXXXXX", "title": "Song Title", "artist": "Artist Name", "duration_s": 1.0}
MODELS = {"vocals": "vocals-model.ckpt", "karaoke": "karaoke-model.ckpt", "instruments": "instruments-model.yaml"}


def load_s3_driver():
    path = Path(__file__).resolve().parent.parent / "s3-reaper" / "driver.py"
    spec = importlib.util.spec_from_file_location("s3_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_mix(seconds=1.0, peak=0.8, seed=0):
    """Stereo tones plus noise, rounded to 16 bits like the mix.wav download writes."""
    t = np.arange(int(seconds * RATE)) / RATE
    rng = np.random.default_rng(seed)
    left = 0.5 * np.sin(2 * np.pi * 220 * t) + 0.3 * np.sin(2 * np.pi * 1_250 * t) + 0.05 * rng.standard_normal(len(t))
    right = 0.4 * np.sin(2 * np.pi * 330 * t) + 0.3 * np.sin(2 * np.pi * 2_100 * t) + 0.05 * rng.standard_normal(len(t))
    mix = np.stack([left, right], axis=1)
    mix *= peak / np.max(np.abs(mix))
    return (separate.to_pcm16(mix).astype(np.float32) / 32_768).astype(np.float32)


class FakeSeparator:
    """Stands in for audio-separator: every output is a fraction of the input.

    Lengths are off by a few frames, like real model outputs, and the names
    use the case and extra outputs the real models give.
    """

    def __init__(self, vocals_gain=0.6, lead_gain=0.7, drums_gain=0.1, extra_frames=3):
        self.calls = []
        self.vocals_gain = vocals_gain
        self.lead_gain = lead_gain
        self.drums_gain = drums_gain
        self.extra = extra_frames

    def _longer(self, audio):
        return np.concatenate([audio, np.full((self.extra, audio.shape[1]), 0.25, dtype=audio.dtype)])

    def __call__(self, step, model_file, audio):
        self.calls.append((step, model_file, audio.copy()))
        if step == "vocals":
            return {"Vocals": self._longer(audio * self.vocals_gain), "Instrumental": audio * 0.1}
        if step == "karaoke":
            return {"Vocals": (audio * self.lead_gain)[: -self.extra], "Instrumental": audio}
        # Vocals and Other are far off on purpose: the cascade must not use them.
        return {
            "Vocals": audio * 7.0,
            "Drums": self._longer(audio * self.drums_gain),
            "Bass": audio * 0.2,
            "Guitar": (audio * 0.15)[: -self.extra],
            "Piano": audio * 0.05,
            "Other": audio * 9.0,
        }


class LibraryLikeSeparator:
    """Wraps a separator with what audio-separator 0.48.0 does around a model.

    Every output that peaks above the normalization threshold is scaled down to
    it on its own (common_separator.py, write_audio_soundfile), and an MDXC
    model does the same to its input (mdxc_separator.py, separate).
    """

    def __init__(self, inner, threshold=1.0):
        self.inner = inner
        self.threshold = threshold

    def _normalize(self, audio):
        peak = float(np.max(np.abs(audio)))
        return audio * (self.threshold / peak) if peak > self.threshold else audio

    def __call__(self, step, model_file, audio):
        if step in ("vocals", "karaoke"):
            audio = self._normalize(audio)
        outputs = self.inner(step, model_file, audio)
        return {name: self._normalize(out).astype(np.float32) for name, out in outputs.items()}


class Align(unittest.TestCase):
    def test_trims_a_longer_output(self):
        audio = np.arange(20, dtype=np.float32).reshape(10, 2)
        np.testing.assert_array_equal(separate.align(audio, 7), audio[:7])

    def test_pads_a_shorter_output_with_silence(self):
        audio = np.ones((5, 2), dtype=np.float32)
        aligned = separate.align(audio, 8)
        self.assertEqual(aligned.shape, (8, 2))
        self.assertEqual(aligned.dtype, np.float32)
        np.testing.assert_array_equal(aligned[:5], audio)
        np.testing.assert_array_equal(aligned[5:], 0)

    def test_same_length_is_unchanged(self):
        audio = np.ones((6, 2), dtype=np.float32)
        np.testing.assert_array_equal(separate.align(audio, 6), audio)


class Pick(unittest.TestCase):
    def test_matches_the_name_ignoring_case(self):
        vocals = np.ones((2, 2))
        self.assertIs(separate.pick({"Instrumental": np.zeros((2, 2)), "Vocals": vocals}, "vocals", "m"), vocals)

    def test_missing_name_lists_what_the_model_gave(self):
        with self.assertRaises(KeyError) as caught:
            separate.pick({"Lead": np.zeros(1), "Other": np.zeros(1)}, "vocals", "karaoke-model.ckpt")
        message = str(caught.exception)
        self.assertIn("karaoke-model.ckpt", message)
        self.assertIn("lead, other", message)


class Cascade(unittest.TestCase):
    def setUp(self):
        self.mix = synthetic_mix(seconds=0.25)
        self.fake = FakeSeparator()
        self.stems = separate.cascade(self.mix, self.fake, MODELS)

    def test_gives_the_seven_stems_in_order(self):
        self.assertEqual(tuple(self.stems), separate.STEMS)
        for audio in self.stems.values():
            self.assertEqual(audio.shape, self.mix.shape)

    def test_stems_add_up_to_the_mix(self):
        total = sum(self.stems.values())
        np.testing.assert_allclose(total, self.mix, atol=1e-6)

    def test_each_step_gets_the_right_input_and_model(self):
        (s1, m1, in1), (s2, m2, in2), (s3, m3, in3) = self.fake.calls
        self.assertEqual((s1, m1), ("vocals", MODELS["vocals"]))
        self.assertEqual((s2, m2), ("karaoke", MODELS["karaoke"]))
        self.assertEqual((s3, m3), ("instruments", MODELS["instruments"]))
        vocals = self.mix * 0.6
        np.testing.assert_allclose(in1, self.mix * separate.HEADROOM)
        np.testing.assert_allclose(in2, vocals * separate.HEADROOM, atol=1e-6)
        np.testing.assert_allclose(in3, (self.mix - vocals) * separate.HEADROOM, atol=1e-6)

    def test_remainders_ignore_the_instrument_model_vocals_and_other(self):
        vocals = self.mix * 0.6
        lead = vocals * 0.7
        lead[-3:] = 0  # the karaoke output came back 3 frames short and was padded
        np.testing.assert_allclose(self.stems["lead"], lead, atol=1e-6)
        np.testing.assert_allclose(self.stems["backing"], vocals - lead, atol=1e-6)
        instrumental = self.mix - vocals
        guitar = instrumental * 0.15
        guitar[-3:] = 0
        parts = instrumental * (0.1 + 0.2 + 0.05) + guitar
        np.testing.assert_allclose(self.stems["other"], instrumental - parts, atol=1e-6)

    def test_extra_frames_from_a_model_are_trimmed(self):
        # The vocals and drums outputs carry 3 extra frames of 0.25; none reach the stems.
        np.testing.assert_allclose(self.stems["drums"], (self.mix - self.mix * 0.6) * 0.1, atol=1e-6)


class LibraryRescaling(unittest.TestCase):
    """Models whose outputs go above full scale, run through the library's own rescaling."""

    def check_matches_the_unscaled_cascade(self, mix, **gains):
        expected = separate.cascade(mix, FakeSeparator(**gains), MODELS)
        levels = {}
        stems = separate.cascade(mix, LibraryLikeSeparator(FakeSeparator(**gains)), MODELS, levels)
        for name in separate.STEMS:
            np.testing.assert_allclose(stems[name], expected[name], atol=1e-6, err_msg=name)
        np.testing.assert_allclose(sum(stems.values()), mix, atol=1e-6)
        return levels

    def test_loud_vocals_and_lead_keep_their_level(self):
        # At full level the vocals peak at 1.17 and the lead at about 1.05.
        levels = self.check_matches_the_unscaled_cascade(synthetic_mix(seconds=0.25, peak=0.9),
                                                         vocals_gain=1.3, lead_gain=0.9)
        self.assertFalse(any(row["reached_threshold"] for row in levels.values()))

    def test_loud_drums_keep_their_level(self):
        # At full level the instrumental peaks at 1.35 and the drums at about 1.2.
        levels = self.check_matches_the_unscaled_cascade(synthetic_mix(seconds=0.25, peak=0.9),
                                                         vocals_gain=-0.5, drums_gain=0.9)
        self.assertFalse(levels["instruments"]["reached_threshold"])

    def test_levels_report_the_peaks_the_library_saw(self):
        levels = {}
        # Loud enough that the vocals peak beats the 0.25 frames the fake appends.
        separate.cascade(synthetic_mix(seconds=0.25, peak=0.9), LibraryLikeSeparator(FakeSeparator()), MODELS, levels)
        self.assertEqual(list(levels), ["vocals", "karaoke", "instruments"])
        self.assertAlmostEqual(levels["vocals"]["input_peak"], 0.9 * separate.HEADROOM, places=4)
        self.assertAlmostEqual(levels["vocals"]["output_peak"], 0.9 * 0.6 * separate.HEADROOM, places=4)
        self.assertAlmostEqual(levels["karaoke"]["output_peak"], 0.9 * 0.6 * 0.7 * separate.HEADROOM, places=4)

    def test_ignored_outputs_that_get_rescaled_are_not_reported(self):
        # The fake's Demucs vocals and other outputs are 7 and 9 times the input.
        levels = {}
        separate.cascade(synthetic_mix(seconds=0.25), LibraryLikeSeparator(FakeSeparator()), MODELS, levels)
        self.assertFalse(levels["instruments"]["reached_threshold"])

    def test_an_output_still_above_full_scale_is_reported(self):
        # Vocals at 2.5 times the mix go past full scale even at the headroom level.
        mix = synthetic_mix(seconds=0.25, peak=0.9)
        levels = {}
        stems = separate.cascade(mix, LibraryLikeSeparator(FakeSeparator(vocals_gain=2.5)), MODELS, levels)
        self.assertTrue(levels["vocals"]["reached_threshold"])
        self.assertAlmostEqual(levels["vocals"]["output_peak"], 1.0, places=5)
        self.assertFalse(levels["karaoke"]["reached_threshold"])
        np.testing.assert_allclose(sum(stems.values()), mix, atol=1e-6)


class FitHeadroom(unittest.TestCase):
    def test_no_reduction_below_full_scale(self):
        stems = {name: np.full((4, 2), 0.1, dtype=np.float32) for name in separate.STEMS}
        stems["other"][0, 0] = separate.PCM16_CEILING
        fitted, gain_db = separate.fit_headroom(stems)
        self.assertEqual(gain_db, 0.0)
        for name in separate.STEMS:
            np.testing.assert_array_equal(fitted[name], stems[name])

    def test_one_loud_stem_scales_them_all_by_the_same_factor(self):
        stems = {name: np.full((4, 2), 0.2, dtype=np.float32) for name in separate.STEMS}
        stems["other"][1, 1] = -1.5
        fitted, gain_db = separate.fit_headroom(stems)
        factor = 10 ** (-gain_db / 20)
        for name in separate.STEMS:
            np.testing.assert_allclose(fitted[name], stems[name] * factor, rtol=1e-6)
            self.assertEqual(fitted[name].dtype, np.float32)
        peak = max(float(np.max(np.abs(a))) for a in fitted.values())
        self.assertLessEqual(peak, separate.PCM16_CEILING)
        self.assertGreater(peak, separate.PCM16_CEILING * 10 ** (-0.001 / 20))

    def test_gain_db_is_rounded_up_to_3_decimals(self):
        stems = {name: np.zeros((2, 2), dtype=np.float32) for name in separate.STEMS}
        stems["bass"][0, 1] = 1.5
        _, gain_db = separate.fit_headroom(stems)
        exact = 20 * math.log10(1.5 / separate.PCM16_CEILING)
        self.assertEqual(gain_db, math.ceil(exact * 1_000) / 1_000)
        self.assertEqual(gain_db, round(gain_db, 3))
        self.assertGreaterEqual(gain_db, exact)

    def test_a_tiny_overshoot_still_gets_a_gain(self):
        stems = {name: np.zeros((2, 2), dtype=np.float32) for name in separate.STEMS}
        stems["drums"][0, 0] = 1.0
        fitted, gain_db = separate.fit_headroom(stems)
        self.assertEqual(gain_db, 0.001)
        self.assertLessEqual(float(np.max(np.abs(fitted["drums"]))), separate.PCM16_CEILING)

    def test_gain_db_restores_the_sum(self):
        stems = {name: np.linspace(-0.3, 0.4, 8, dtype=np.float32).reshape(4, 2) for name in separate.STEMS}
        stems["backing"] *= 4
        fitted, gain_db = separate.fit_headroom(stems)
        self.assertGreater(gain_db, 0)
        np.testing.assert_allclose(sum(fitted.values()) * 10 ** (gain_db / 20), sum(stems.values()), rtol=1e-5)

    def test_the_peak_fits_in_16_bits_without_clipping(self):
        stems = {name: np.zeros((2, 2), dtype=np.float32) for name in separate.STEMS}
        stems["lead"][0, 0] = 2.0
        fitted, _ = separate.fit_headroom(stems)
        # Rounding gain_db up costs at most 0.001 dB, under 4 of the 32767 steps.
        self.assertIn(int(separate.to_pcm16(fitted["lead"])[0, 0]), range(32_763, 32_768))


class ToPcm16(unittest.TestCase):
    def test_rounds_to_the_nearest_step_with_32768_as_full_scale(self):
        audio = np.array([[0.0, 0.5], [-1.0, 1 / 32_768 * 0.49], [1 / 32_768 * 0.51, -0.25]])
        np.testing.assert_array_equal(separate.to_pcm16(audio), [[0, 16_384], [-32_768, 0], [1, -8_192]])

    def test_clips_at_the_16_bit_range(self):
        np.testing.assert_array_equal(separate.to_pcm16(np.array([[1.2, -1.2]])), [[32_767, -32_768]])


class SongId(unittest.TestCase):
    def test_shape_from_section_5_2(self):
        suffix = hashlib.sha256(b"XXXXXXXXXXX").hexdigest()[:6]
        self.assertEqual(separate.song_id("Song Title", "Artist Name", "XXXXXXXXXXX"),
                         f"song-title--artist-name--{suffix}")

    def test_slug_drops_accents_and_punctuation(self):
        self.assertEqual(separate.slug("  Cancao, Acentuada! (Ao Vivo) "), "cancao-acentuada-ao-vivo")
        self.assertEqual(separate.slug("Ca\u00e7\u00e3o \u00d1"), "cacao-n")

    def test_different_videos_get_different_ids(self):
        self.assertNotEqual(separate.song_id("A", "B", "video-one__"), separate.song_id("A", "B", "video-two__"))


class Package(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "pkg"
        mix = synthetic_mix(seconds=0.5)
        cls.n_frames = len(mix)
        stems = separate.cascade(mix, FakeSeparator(), MODELS)
        cls.manifest = separate.write_package(cls.out, stems, SOURCE, 1.25, MODELS)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_manifest_fields(self):
        m = self.manifest
        self.assertEqual(list(m), ["schema", "pipeline_version", "id", "title", "artist", "duration_s",
                                   "source", "gain_db", "stems", "models", "created_at"])
        self.assertEqual((m["schema"], m["pipeline_version"]), (1, 1))
        self.assertEqual(m["id"], separate.song_id("Song Title", "Artist Name", "XXXXXXXXXXX"))
        self.assertEqual((m["title"], m["artist"]), ("Song Title", "Artist Name"))
        self.assertEqual(m["duration_s"], round(self.n_frames / RATE, 3))
        self.assertEqual(m["source"], {"type": "youtube_music", "video_id": "XXXXXXXXXXX"})
        self.assertEqual(m["gain_db"], 1.25)
        self.assertEqual(m["models"], {"vocals": "vocals-model.ckpt", "karaoke": "karaoke-model.ckpt",
                                       "instrumental": "instruments-model.yaml"})
        self.assertRegex(m["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertNotIn("lyrics", m)

    def test_manifest_on_disk_matches(self):
        on_disk = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, self.manifest)

    def test_stem_names_are_the_ones_the_s3_listener_loads(self):
        self.assertEqual(tuple(self.manifest["stems"]), load_s3_driver().STEMS)

    def test_stem_paths_are_relative_to_the_manifest(self):
        for stem, entry in self.manifest["stems"].items():
            self.assertEqual(entry["file"], f"stems/{stem}.flac")
            self.assertTrue((self.out / entry["file"]).is_file())

    def test_sha256_matches_the_files(self):
        for entry in self.manifest["stems"].values():
            data = (self.out / entry["file"]).read_bytes()
            self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())

    def test_stems_are_16_bit_44k_stereo_flac(self):
        for entry in self.manifest["stems"].values():
            info = sf.info(self.out / entry["file"])
            self.assertEqual((info.format, info.subtype), ("FLAC", "PCM_16"))
            self.assertEqual((info.samplerate, info.channels, info.frames), (RATE, 2, self.n_frames))


class NullTest(unittest.TestCase):
    """FLAC round trip of synthetic stems against the mix they came from."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _package(self, mix, fake):
        mix_path = self.dir / "mix.wav"
        sf.write(mix_path, separate.to_pcm16(mix), RATE, subtype="PCM_16")
        stems = separate.cascade(mix, fake, MODELS)
        stems, gain_db = separate.fit_headroom(stems)
        separate.write_package(self.dir / "pkg", stems, SOURCE, gain_db, MODELS)
        return separate.null_test(self.dir / "pkg" / "manifest.json", mix_path), gain_db

    def test_without_gain_the_error_stays_within_the_16_bit_rounding(self):
        result, gain_db = self._package(synthetic_mix(seconds=1.0), FakeSeparator())
        self.assertEqual(gain_db, 0.0)
        # 7 stems, each rounded to within half a step.
        self.assertLessEqual(result["max_abs_error_lsb"], 3.5)
        self.assertLess(result["residual_rms_dbfs"], -85)
        self.assertGreater(result["max_abs_error"], 0)

    def test_with_gain_db_the_stems_still_add_up_to_the_mix(self):
        # A vocals model that overshoots makes a loud negative remainder.
        result, gain_db = self._package(synthetic_mix(seconds=1.0, peak=0.9), FakeSeparator(vocals_gain=2.5))
        self.assertGreater(gain_db, 0)
        self.assertLessEqual(result["max_abs_error_lsb"], 3.5 * 10 ** (gain_db / 20))
        self.assertLess(result["residual_rms_dbfs"], -80)

    def test_the_manifest_gain_db_is_the_one_the_stems_were_scaled_by(self):
        # Rounding only the manifest value would add up to about 2 LSB at a
        # full scale peak, so the stems carry the rounded gain instead.
        result, gain_db = self._package(synthetic_mix(seconds=1.0, peak=0.9), FakeSeparator(vocals_gain=2.12))
        manifest = json.loads((self.dir / "pkg" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["gain_db"], gain_db)
        self.assertEqual(gain_db, round(gain_db, 3))
        self.assertGreater(gain_db, 0)
        self.assertLessEqual(result["max_abs_error_lsb"], 3.5 * 10 ** (gain_db / 20))

    def test_a_wrong_gain_fails_loudly(self):
        self._package(synthetic_mix(seconds=0.5), FakeSeparator())
        manifest_path = self.dir / "pkg" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["gain_db"] = -6.0
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result = separate.null_test(manifest_path, self.dir / "mix.wav")
        self.assertGreater(result["residual_rms_dbfs"], -30)


class SeparateSong(unittest.TestCase):
    def test_writes_the_package_timings_and_null_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            mix_path = tmp / "download" / "mix.wav"
            mix_path.parent.mkdir()
            sf.write(mix_path, separate.to_pcm16(synthetic_mix(seconds=0.5)), RATE, subtype="PCM_16")
            (mix_path.parent / "source.json").write_text(json.dumps(SOURCE), encoding="utf-8")
            out = tmp / "pkg"
            manifest, timings = separate.separate_song(mix_path, MODELS, out, FakeSeparator(), "A")
            self.assertTrue((out / "manifest.json").is_file())
            self.assertEqual(json.loads((out / "timings.json").read_text(encoding="utf-8")), timings)
            self.assertEqual(timings["combo"], "A")
            self.assertEqual(timings["mix"], str(mix_path.resolve()))
            self.assertEqual(list(timings["steps"]), ["vocals", "karaoke", "instruments"])
            self.assertEqual(timings["headroom"], separate.HEADROOM)
            for step, row in timings["steps"].items():
                self.assertEqual(row["model"], MODELS[step])
                self.assertGreaterEqual(row["seconds"], 0)
                self.assertIsNone(row["peak_vram_mb"])
                self.assertIsNone(row["overlap"])
                self.assertGreater(row["input_peak"], 0)
                self.assertGreater(row["output_peak"], 0)
                self.assertFalse(row["reached_threshold"])
            self.assertLessEqual(timings["null_test"]["max_abs_error_lsb"], 3.5)
            self.assertEqual(manifest["title"], "Song Title")

            with contextlib.redirect_stdout(io.StringIO()) as printed:
                separate.main(["null", str(out / "manifest.json")])
            self.assertIn("null test: max abs error", printed.getvalue())


class MDXCSeparator:
    """Same class name as the audio-separator MDXC architecture."""

    overlap = 4


class DemucsSeparator:
    overlap = 0.25


class SeparatorOptions(unittest.TestCase):
    def test_without_overlap_the_library_defaults_stay(self):
        options = separate.separator_options(Path("models"), Path("out"))
        self.assertNotIn("mdxc_params", options)
        self.assertEqual(options["normalization_threshold"], separate.NORMALIZATION_THRESHOLD)
        self.assertEqual(options["model_file_dir"], "models")
        self.assertEqual(options["output_dir"], "out")

    def test_overlap_goes_to_the_mdxc_params(self):
        options = separate.separator_options(Path("models"), Path("out"), overlap=2)
        self.assertEqual(options["mdxc_params"], {"segment_size": 256, "override_model_segment_size": False,
                                                  "batch_size": None, "overlap": 2, "pitch_shift": 0})

    def test_effective_overlap_reads_mdxc_models_only(self):
        self.assertEqual(separate.effective_overlap(MDXCSeparator()), 4)
        self.assertIsNone(separate.effective_overlap(DemucsSeparator()))
        self.assertIsNone(separate.effective_overlap(None))


class StemFromFilename(unittest.TestCase):
    def test_reads_the_stem_from_audio_separator_names(self):
        self.assertEqual(separate.stem_from_filename(
            "vocals_input_(Vocals)_model_bs_roformer_ep_317_sdr_12.9755.wav", "vocals_input"), "Vocals")
        self.assertEqual(separate.stem_from_filename(
            "instruments_input_(Drums)_htdemucs_6s.wav", "instruments_input"), "Drums")
        self.assertEqual(separate.stem_from_filename(
            "karaoke_input_(Instrumental)_mel_band_roformer_karaoke_becruily.wav", "karaoke_input"), "Instrumental")

    def test_unexpected_name_raises(self):
        with self.assertRaises(ValueError):
            separate.stem_from_filename("something_else.wav", "vocals_input")


class Download(unittest.TestCase):
    def test_source_metadata_prefers_track_and_artists(self):
        info = {"id": "XXXXXXXXXXX", "title": "Song Title (Official Audio)", "track": "Song Title",
                "artists": ["Artist Name", "Guest Name"], "uploader": "Artist Name - Topic", "duration": 245}
        self.assertEqual(separate.source_metadata(info), {
            "video_id": "XXXXXXXXXXX", "title": "Song Title", "artist": "Artist Name, Guest Name", "duration_s": 245})

    def test_source_metadata_falls_back_to_title_and_uploader(self):
        info = {"id": "XXXXXXXXXXX", "title": "Song Title", "uploader": "Artist Name - Topic", "duration": 12.5}
        self.assertEqual(separate.source_metadata(info), {
            "video_id": "XXXXXXXXXXX", "title": "Song Title", "artist": "Artist Name", "duration_s": 12.5})

    def test_ffmpeg_writes_44k_stereo_16_bit_wav(self):
        command = separate.ffmpeg_command(Path("in.webm"), Path("mix.wav"))
        self.assertEqual(command[0], "ffmpeg")
        joined = " ".join(command)
        for part in ("-ac 2", "-ar 44100", "-c:a pcm_s16le", "-vn", "-y"):
            self.assertIn(part, joined)
        self.assertEqual(command[-1], "mix.wav")


class Options(unittest.TestCase):
    def parse(self, *argv):
        return separate.build_parser().parse_args(["separate", "mix.wav", *argv])

    def test_combo_gives_its_models(self):
        for combo in ("A", "B", "C"):
            self.assertEqual(separate.resolve_models(self.parse("--combo", combo)), separate.COMBOS[combo])

    def test_a_flag_overrides_one_model(self):
        models = separate.resolve_models(self.parse("--combo", "B", "--karaoke-model", "other-karaoke.ckpt"))
        self.assertEqual(models, {**separate.COMBOS["B"], "karaoke": "other-karaoke.ckpt"})

    def test_every_combo_uses_htdemucs_6s(self):
        self.assertTrue(all(c["instruments"] == "htdemucs_6s.yaml" for c in separate.COMBOS.values()))

    def test_overlap_is_off_by_default(self):
        self.assertIsNone(self.parse("--combo", "A").overlap)
        self.assertEqual(self.parse("--combo", "A", "--overlap", "2").overlap, 2)

    def test_combo_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            separate.build_parser().parse_args(["separate", "mix.wav"])


class NoHeavyImports(unittest.TestCase):
    def test_importing_the_module_loads_no_ml_or_download_package(self):
        for name in ("torch", "audio_separator", "yt_dlp"):
            self.assertNotIn(name, sys.modules)

    def test_vram_is_none_without_cuda(self):
        separate.reset_vram_peak()
        self.assertIsNone(separate.peak_vram_mb())


if __name__ == "__main__":
    unittest.main()
