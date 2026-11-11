"""Serve the song library over HTTP and time package downloads, for spike S5.

Usage:
  python spikes/s5-transfer/transfer.py serve <library_dir> --bind <ip> [--port 8765]
  python spikes/s5-transfer/transfer.py fetch <base_url> <song_id> [--runs N] [--out DIR] [--label TEXT] [--keep]

serve is a read-only static file server (GET and HEAD only) rooted at the
library folder. It has no token: in this spike the tailnet is the boundary.

fetch downloads <base_url>/<song_id>/manifest.json, then every stem file the
manifest lists, one after the other, streaming each to disk while it computes
its sha256. A hash that does not match the manifest is reported, and the run
goes on. Every fetch appends one record to results.local.json next to this file.

1 MB is 10^6 bytes and 1 Mbit is 10^6 bits.

Standard library only.
"""

import argparse
import functools
import hashlib
import json
import shutil
import statistics
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.local.json"
DEFAULT_PORT = 8765
CHUNK = 1 << 20


def make_server(library_dir: Path, bind: str, port: int) -> ThreadingHTTPServer:
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(library_dir))
    return ThreadingHTTPServer((bind, port), handler)


def throughput(num_bytes: int, seconds: float) -> dict:
    return {
        "mb_per_s": num_bytes / 1e6 / seconds,
        "mbit_per_s": num_bytes * 8 / 1e6 / seconds,
    }


def download(url: str, dest: Path) -> dict:
    """Stream url to dest. seconds runs from the request to the last byte."""
    digest = hashlib.sha256()
    num_bytes = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with urllib.request.urlopen(url) as response, dest.open("wb") as out:
        while chunk := response.read(CHUNK):
            digest.update(chunk)
            out.write(chunk)
            num_bytes += len(chunk)
    seconds = time.perf_counter() - start
    return {"bytes": num_bytes, "seconds": seconds, "sha256": digest.hexdigest()}


def fetch_run(base_url: str, song_id: str, out_dir: Path) -> dict:
    """One download of a whole package into out_dir. Totals include the manifest."""
    package_url = f"{base_url.rstrip('/')}/{quote(song_id)}"
    start = time.perf_counter()
    manifest_path = out_dir / "manifest.json"
    manifest = {"file": "manifest.json", **download(f"{package_url}/manifest.json", manifest_path)}
    stems = json.loads(manifest_path.read_text(encoding="utf-8"))["stems"]
    files = []
    for name, stem in stems.items():
        got = download(f"{package_url}/{quote(stem['file'])}", out_dir / stem["file"])
        files.append({
            "stem": name,
            "file": stem["file"],
            **got,
            **throughput(got["bytes"], got["seconds"]),
            "expected_sha256": stem["sha256"],
            "match": got["sha256"] == stem["sha256"],
        })
    total_seconds = time.perf_counter() - start
    total_bytes = manifest["bytes"] + sum(f["bytes"] for f in files)
    return {
        "manifest": manifest,
        "files": files,
        "total_bytes": total_bytes,
        "total_seconds": total_seconds,
        **throughput(total_bytes, total_seconds),
        "all_match": all(f["match"] for f in files),
    }


def format_run(index: int, runs: int, run: dict) -> str:
    mismatched = [f["stem"] for f in run["files"] if not f["match"]]
    verdict = "all match" if not mismatched else f"MISMATCH in {', '.join(mismatched)}"
    lines = [
        f"run {index}/{runs}: {len(run['files'])} stems + manifest, "
        f"{run['total_bytes'] / 1e6:.3f} MB in {run['total_seconds']:.3f} s, "
        f"{run['mb_per_s']:.2f} MB/s, {run['mbit_per_s']:.1f} Mbit/s, sha256 {verdict}",
        f"  {'manifest':<8} {run['manifest']['file']:<20} {run['manifest']['bytes'] / 1e6:>9.3f} MB "
        f"{run['manifest']['seconds']:>8.3f} s",
    ]
    for f in run["files"]:
        lines.append(
            f"  {f['stem']:<8} {f['file']:<20} {f['bytes'] / 1e6:>9.3f} MB {f['seconds']:>8.3f} s "
            f"{f['mb_per_s']:>8.2f} MB/s {f['mbit_per_s']:>8.1f} Mbit/s  {'match' if f['match'] else 'MISMATCH'}"
        )
    return "\n".join(lines)


def format_spread(runs: list[dict]) -> str:
    seconds = [r["total_seconds"] for r in runs]
    return (
        f"total seconds over {len(seconds)} runs: min {min(seconds):.3f}, "
        f"median {statistics.median(seconds):.3f}, max {max(seconds):.3f}"
    )


def append_result(path: Path, record: dict) -> None:
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    records.append(record)
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def cmd_serve(args: argparse.Namespace) -> None:
    server = make_server(args.library_dir, args.bind, args.port)
    print(f"serving {args.library_dir.resolve()} at http://{args.bind}:{args.port}/ (Ctrl+C stops)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def cmd_fetch(args: argparse.Namespace) -> None:
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    runs = []
    for index in range(1, args.runs + 1):
        out_dir = Path(tempfile.mkdtemp(prefix=f"s5-run{index}-", dir=args.out))
        run = fetch_run(args.base_url, args.song_id, out_dir)
        run["out_dir"] = str(out_dir)
        if not args.keep:
            shutil.rmtree(out_dir)
        runs.append(run)
        print(format_run(index, args.runs, run))
    print(format_spread(runs))
    append_result(RESULTS, {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "label": args.label,
        "base_url": args.base_url,
        "song_id": args.song_id,
        "runs": runs,
    })
    print(f"appended to {RESULTS}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the song library and time package downloads, for spike S5.")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="serve a library folder over HTTP, read-only")
    serve.add_argument("library_dir", type=Path)
    serve.add_argument("--bind", required=True, help="address to listen on, such as the Tailscale IP")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    serve.set_defaults(func=cmd_serve)

    fetch = sub.add_parser("fetch", help="download a package N times and time each run")
    fetch.add_argument("base_url", help="for example http://100.x.y.z:8765")
    fetch.add_argument("song_id")
    fetch.add_argument("--runs", type=int, default=1, help="default 1")
    fetch.add_argument("--out", type=Path, help="parent folder for the run folders (default: the system temp folder)")
    fetch.add_argument("--label", default="", help="free text stored with the record, such as the network used")
    fetch.add_argument("--keep", action="store_true", help="keep the downloaded files")
    fetch.set_defaults(func=cmd_fetch)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
