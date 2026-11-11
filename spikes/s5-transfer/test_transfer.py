"""Tests for transfer.py against a real server on 127.0.0.1. No other network.

Run from the repo root: python -m unittest discover -s spikes/s5-transfer -v
"""

import contextlib
import hashlib
import http.client
import io
import json
import shutil
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from unittest import mock

import transfer

SONG_ID = "song-title--artist-name--a1b2c3"
STEMS = {"lead": 300_000, "backing": 120_000, "drums": 1}


def write_package(library: Path, stems: dict[str, int]) -> dict[str, bytes]:
    """Placeholder package: each stem is size bytes of a fixed pattern."""
    package = library / SONG_ID
    (package / "stems").mkdir(parents=True)
    contents = {}
    manifest_stems = {}
    for index, (name, size) in enumerate(stems.items()):
        data = bytes((index * 7 + i) % 251 for i in range(size))
        file = f"stems/{name}.flac"
        (package / file).write_bytes(data)
        contents[file] = data
        manifest_stems[name] = {"file": file, "sha256": hashlib.sha256(data).hexdigest()}
    manifest = {"schema": 1, "id": SONG_ID, "title": "Song Title", "artist": "Artist Name", "stems": manifest_stems}
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return contents


class ServedLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.library = self.tmp / "library"
        self.library.mkdir()
        self.contents = write_package(self.library, STEMS)
        quiet = mock.patch.object(SimpleHTTPRequestHandler, "log_message")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = transfer.make_server(self.library, "127.0.0.1", 0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"

    def fetch(self) -> tuple[dict, Path]:
        out = self.tmp / "out"
        out.mkdir(exist_ok=True)
        return transfer.fetch_run(self.base_url, SONG_ID, out), out

    def test_downloaded_files_are_identical(self):
        run, out = self.fetch()
        for file, data in self.contents.items():
            self.assertEqual((out / file).read_bytes(), data)
        manifest = (self.library / SONG_ID / "manifest.json").read_bytes()
        self.assertEqual((out / "manifest.json").read_bytes(), manifest)
        self.assertEqual([f["stem"] for f in run["files"]], list(STEMS))

    def test_hashes_match_and_are_reported(self):
        run, _ = self.fetch()
        self.assertTrue(run["all_match"])
        for f in run["files"]:
            self.assertTrue(f["match"])
            self.assertEqual(f["sha256"], hashlib.sha256(self.contents[f["file"]]).hexdigest())

    def test_totals_add_up_with_the_manifest_included(self):
        run, _ = self.fetch()
        manifest_bytes = len((self.library / SONG_ID / "manifest.json").read_bytes())
        self.assertEqual(run["manifest"]["bytes"], manifest_bytes)
        self.assertEqual([f["bytes"] for f in run["files"]], list(STEMS.values()))
        self.assertEqual(run["total_bytes"], manifest_bytes + sum(STEMS.values()))
        parts = run["manifest"]["seconds"] + sum(f["seconds"] for f in run["files"])
        self.assertGreaterEqual(run["total_seconds"], parts)
        self.assertAlmostEqual(run["mb_per_s"], run["total_bytes"] / 1e6 / run["total_seconds"])

    def test_hash_mismatch_is_reported_without_raising(self):
        path = self.library / SONG_ID / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["stems"]["backing"]["sha256"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        run, out = self.fetch()
        by_stem = {f["stem"]: f for f in run["files"]}
        self.assertFalse(by_stem["backing"]["match"])
        self.assertTrue(by_stem["lead"]["match"])
        self.assertTrue(by_stem["drums"]["match"])
        self.assertFalse(run["all_match"])
        self.assertTrue((out / "stems" / "drums.flac").exists())
        self.assertIn("MISMATCH in backing", transfer.format_run(1, 1, run))

    def test_a_path_outside_the_library_is_not_served(self):
        (self.tmp / "secret.txt").write_text("outside", encoding="utf-8")
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", f"/{SONG_ID}/../../secret.txt"):
            with self.subTest(path=path):
                conn = http.client.HTTPConnection("127.0.0.1", self.port)
                self.addCleanup(conn.close)
                conn.request("GET", path)
                response = conn.getresponse()
                body = response.read()
                self.assertEqual(response.status, 404)
                self.assertNotIn(b"outside", body)

    def test_the_server_refuses_writes(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        self.addCleanup(conn.close)
        conn.request("PUT", f"/{SONG_ID}/manifest.json", body=b"{}")
        self.assertEqual(conn.getresponse().status, 501)

    def test_cli_fetch_prints_runs_appends_a_record_and_deletes_the_files(self):
        results = self.tmp / "results.local.json"
        results.write_text(json.dumps([{"label": "earlier"}]), encoding="utf-8")
        out = self.tmp / "cli-out"
        stdout = io.StringIO()
        with mock.patch.object(transfer, "RESULTS", results), contextlib.redirect_stdout(stdout):
            transfer.main(["fetch", self.base_url, SONG_ID, "--runs", "2", "--out", str(out), "--label", "loopback"])
        printed = stdout.getvalue()
        self.assertIn("run 1/2: 3 stems + manifest", printed)
        self.assertIn("run 2/2:", printed)
        self.assertIn("total seconds over 2 runs: min", printed)
        records = json.loads(results.read_text(encoding="utf-8"))
        self.assertEqual(records[0], {"label": "earlier"})
        record = records[1]
        self.assertEqual(record["label"], "loopback")
        self.assertEqual(record["base_url"], self.base_url)
        self.assertEqual(record["song_id"], SONG_ID)
        self.assertTrue(record["timestamp"].endswith("+00:00"))
        self.assertEqual(len(record["runs"]), 2)
        self.assertEqual(len(record["runs"][0]["files"]), len(STEMS))
        self.assertEqual(list(out.iterdir()), [])

    def test_cli_fetch_keep_leaves_the_files(self):
        out = self.tmp / "cli-out"
        with mock.patch.object(transfer, "RESULTS", self.tmp / "results.local.json"), contextlib.redirect_stdout(io.StringIO()):
            transfer.main(["fetch", self.base_url, SONG_ID, "--out", str(out), "--keep"])
        (run_dir,) = out.iterdir()
        self.assertEqual((run_dir / "stems" / "lead.flac").read_bytes(), self.contents["stems/lead.flac"])


class Throughput(unittest.TestCase):
    def test_megabytes_are_decimal_and_megabits_are_eight_times(self):
        self.assertEqual(transfer.throughput(150_000_000, 60.0), {"mb_per_s": 2.5, "mbit_per_s": 20.0})

    def test_one_byte_per_microsecond(self):
        result = transfer.throughput(1_000_000, 1.0)
        self.assertAlmostEqual(result["mb_per_s"], 1.0)
        self.assertAlmostEqual(result["mbit_per_s"], 8.0)


class Spread(unittest.TestCase):
    def test_min_median_max_of_total_seconds(self):
        runs = [{"total_seconds": s} for s in (3.0, 1.0, 2.0)]
        self.assertEqual(transfer.format_spread(runs), "total seconds over 3 runs: min 1.000, median 2.000, max 3.000")


class Parser(unittest.TestCase):
    def test_serve_requires_bind(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            transfer.build_parser().parse_args(["serve", "library"])

    def test_serve_defaults_to_port_8765(self):
        args = transfer.build_parser().parse_args(["serve", "library", "--bind", "127.0.0.1"])
        self.assertEqual(args.port, 8765)


if __name__ == "__main__":
    unittest.main()
