"""Tests for the pure parts of driver.py and for make_test_songs.py. No network.

Run from the repo root: python -m unittest discover -s spikes/s3-reaper -v
"""

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from urllib.parse import unquote

import driver
import make_test_songs

TRACK_REPLY = (
    "TRACK\t0\tMASTER\t0\t1.000000\t0.000000\t-1500\t-1500\t1.000000\t0\t0\t0\t1\t0\n"
    "TRACK\t1\tMic\t192\t1.000000\t0.000000\t-1500\t-1500\t1.000000\t0\t0\t0\t1\t0\n"
    "TRACK\t2\tLead\t8\t1.000000\t0.000000\t-1500\t-1500\t1.000000\t0\t0\t0\t1\t0\n"
    "TRACK\t3\tdrums\t0\t1.000000\t0.000000\t-1500\t-1500\t1.000000\t0\t0\t0\t1\t0\n"
)


class ParseTrack(unittest.TestCase):
    def test_parses_every_track_line(self):
        tracks = driver.parse_tracks(TRACK_REPLY)
        self.assertEqual([t["index"] for t in tracks], [0, 1, 2, 3])
        self.assertEqual([t["name"] for t in tracks], ["MASTER", "Mic", "Lead", "drums"])
        self.assertEqual(tracks[1]["flags"], 192)

    def test_flags_decode_mute_arm_and_monitoring(self):
        mic, lead = driver.parse_tracks(TRACK_REPLY)[1:3]
        self.assertTrue(mic["armed"])
        self.assertTrue(mic["monitoring"])
        self.assertFalse(mic["muted"])
        self.assertTrue(lead["muted"])

    def test_ignores_other_lines(self):
        reply = "TRANSPORT\t0\t0.000000\t0\t0:00.000\t1.1.00\n" + TRACK_REPLY + "\n"
        self.assertEqual(len(driver.parse_tracks(reply)), 4)


class NameToIndexMap(unittest.TestCase):
    def test_maps_lowercase_names_and_skips_master(self):
        mapping = driver.track_index_map(driver.parse_tracks(TRACK_REPLY))
        self.assertEqual(mapping, {"mic": 1, "lead": 2, "drums": 3})

    def test_first_track_wins_on_duplicate_names(self):
        reply = TRACK_REPLY + "TRACK\t4\tLEAD\t0\t1\t0\t0\t0\t1\t0\t0\t0\t1\t0\n"
        mapping = driver.track_index_map(driver.parse_tracks(reply))
        self.assertEqual(mapping["lead"], 2)


class ParseTransport(unittest.TestCase):
    def test_parses_playstate_and_position(self):
        reply = "TRANSPORT\t1\t12.345678\t0\t0:12.345\t7.1.00\n"
        transport = driver.parse_transport(reply)
        self.assertEqual(transport["playstate"], 1)
        self.assertAlmostEqual(transport["position"], 12.345678)
        self.assertFalse(transport["repeat"])
        self.assertEqual(transport["position_str"], "0:12.345")

    def test_returns_none_without_a_transport_line(self):
        self.assertIsNone(driver.parse_transport(TRACK_REPLY))


class Unescape(unittest.TestCase):
    def test_decodes_newline_tab_and_backslash(self):
        self.assertEqual(driver.unescape(r"a\nb\tc\\d"), "a\nb\tc\\d")

    def test_encoded_backslash_before_n_stays_a_backslash(self):
        # REAPER sends the JSON text {"e":"C:\\new"} with each backslash doubled.
        # Replacing \n before \\ (as main.js simple_unescape does) breaks it.
        wire = r'{"e":"C:\\\\new"}'
        self.assertEqual(driver.unescape(wire), r'{"e":"C:\\new"}')
        self.assertEqual(json.loads(driver.unescape(wire))["e"], "C:\\new")

    def test_unknown_escape_is_kept(self):
        self.assertEqual(driver.unescape(r"a\xb"), r"a\xb")


class ParseExtState(unittest.TestCase):
    def test_returns_the_decoded_value(self):
        ack = {"id": 3, "op": "load", "ok": False, "error": "C:\\Songs\\x\nline 2"}
        wire_value = json.dumps(ack).replace("\\", "\\\\")
        reply = f"EXTSTATE\tkaraoke\tack\t{wire_value}\n"
        self.assertEqual(json.loads(driver.parse_extstate(reply, "karaoke", "ack")), ack)

    def test_empty_value(self):
        self.assertEqual(driver.parse_extstate("EXTSTATE\tkaraoke\tack\t\n", "karaoke", "ack"), "")

    def test_missing_line_is_none(self):
        self.assertIsNone(driver.parse_extstate(TRACK_REPLY, "karaoke", "ack"))

    def test_other_key_is_ignored(self):
        reply = "EXTSTATE\tkaraoke\tcmd\tx\nEXTSTATE\tkaraoke\tack\ty\n"
        self.assertEqual(driver.parse_extstate(reply, "karaoke", "ack"), "y")


class EncodeCommand(unittest.TestCase):
    COMMAND = {"id": 42, "op": "load", "manifest": "C:\\Songs\\a;b\\song a\\manifest.json"}

    def test_prefix_and_round_trip(self):
        request = driver.encode_command(self.COMMAND)
        prefix = "SET/EXTSTATE/karaoke/cmd/"
        self.assertTrue(request.startswith(prefix))
        value = request[len(prefix):]
        self.assertEqual(json.loads(unquote(value)), self.COMMAND)

    def test_value_has_no_separator_or_raw_special_chars(self):
        value = driver.encode_command(self.COMMAND)[len("SET/EXTSTATE/karaoke/cmd/"):]
        for char in ";/\\ \"{}:":
            self.assertNotIn(char, value)
        self.assertIn("%3B", value)
        self.assertIn("%5C", value)

    def test_exact_encoding_of_a_small_command(self):
        self.assertEqual(
            driver.encode_command({"id": 1, "op": "ping"}),
            "SET/EXTSTATE/karaoke/cmd/%7B%22id%22%3A1%2C%22op%22%3A%22ping%22%7D",
        )


class Summarize(unittest.TestCase):
    def test_min_median_max(self):
        stats = driver.summarize([30.0, 10.0, 20.0, 50.0])
        self.assertEqual(stats, {"n": 4, "min": 10.0, "median": 25.0, "max": 50.0})

    def test_empty(self):
        self.assertEqual(driver.summarize([]), {"n": 0, "min": None, "median": None, "max": None})


class Cadence(unittest.TestCase):
    def test_steady_playback_has_no_drift(self):
        samples = [(i * 0.1, 1, 5.0 + i * 0.1) for i in range(11)]
        result = driver.cadence(samples)
        self.assertEqual(result["intervals_ms"]["n"], 10)
        self.assertAlmostEqual(result["intervals_ms"]["median"], 100.0)
        self.assertAlmostEqual(result["drift_ms"], 0.0)
        self.assertAlmostEqual(result["step_error_ms"]["max"], 0.0)

    def test_position_lagging_the_clock(self):
        # Position advances 90 ms per 100 ms of wall clock: 10 ms behind per step.
        samples = [(i * 0.1, 1, i * 0.09) for i in range(5)]
        result = driver.cadence(samples)
        self.assertAlmostEqual(result["drift_ms"], -40.0)
        self.assertAlmostEqual(result["step_error_ms"]["min"], -10.0)

    def test_only_playing_samples_count(self):
        samples = [(0.0, 0, 0.0), (0.1, 1, 0.0), (0.2, 1, 0.1), (0.3, 0, 0.1)]
        result = driver.cadence(samples)
        self.assertEqual(result["playing_samples"], 2)
        self.assertEqual(result["step_error_ms"]["n"], 1)


class TestSongs(unittest.TestCase):
    SECONDS = 0.5

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            make_test_songs.main(["--seconds", str(cls.SECONDS), "--out", str(cls.out)])
        cls.manifests = {
            song: json.loads((cls.out / song / "manifest.json").read_text(encoding="utf-8"))
            for song in ("song-a", "song-b")
        }

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_manifest_shape(self):
        for song, manifest in self.manifests.items():
            self.assertEqual(manifest["schema"], 1)
            self.assertEqual(manifest["pipeline_version"], 1)
            self.assertEqual(manifest["id"], song)
            self.assertTrue(manifest["title"].startswith("Test Song "))
            self.assertIsInstance(manifest["artist"], str)
            self.assertEqual(manifest["duration_s"], self.SECONDS)
            self.assertIn("type", manifest["source"])
            self.assertIsInstance(manifest["gain_db"], float)
            self.assertRegex(manifest["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertEqual(list(manifest["stems"]), list(driver.STEMS))

    def test_stem_files_match_their_sha256(self):
        for song, manifest in self.manifests.items():
            for stem, entry in manifest["stems"].items():
                self.assertEqual(entry["file"], f"stems/{stem}.wav")
                data = (self.out / song / entry["file"]).read_bytes()
                self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())

    def test_stems_are_stereo_16_bit_44k_with_the_right_length(self):
        for song in self.manifests:
            for stem in driver.STEMS:
                with wave.open(str(self.out / song / "stems" / f"{stem}.wav"), "rb") as w:
                    self.assertEqual(w.getnchannels(), 2)
                    self.assertEqual(w.getsampwidth(), 2)
                    self.assertEqual(w.getframerate(), 44_100)
                    self.assertEqual(w.getnframes(), int(self.SECONDS * 44_100))

    def test_songs_differ_and_the_sum_does_not_clip(self):
        for song in self.manifests:
            total = None
            for stem in driver.STEMS:
                with wave.open(str(self.out / song / "stems" / f"{stem}.wav"), "rb") as w:
                    samples = array("h", w.readframes(w.getnframes()))
                total = list(samples) if total is None else [a + b for a, b in zip(total, samples)]
            self.assertLess(max(abs(v) for v in total), 32_767)
        hashes_a = {e["sha256"] for e in self.manifests["song-a"]["stems"].values()}
        hashes_b = {e["sha256"] for e in self.manifests["song-b"]["stems"].values()}
        self.assertEqual(len(hashes_a), 7)
        self.assertFalse(hashes_a & hashes_b)


if __name__ == "__main__":
    unittest.main()
