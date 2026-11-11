"""Drive REAPER for spike S3 through its built-in web interface.

Usage: python spikes/s3-reaper/driver.py [--host H] [--port P] <command> ...

Transport and mute go straight to the web interface. load, transpose, ping and
quit go to karaoke_listener.lua through ExtState: the command is written to
karaoke/cmd and the reply is read back from karaoke/ack, matched by id.

Protocol reference: the top comment of reaper_www_root/main.js in the REAPER
install folder.

Standard library only.
"""

import argparse
import itertools
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

SECTION = "karaoke"
STEMS = ("lead", "backing", "drums", "bass", "guitar", "piano", "other")
ACTIONS = {"play": 1007, "stop": 1016, "start": 40042}

# TRACK flag bits, from main.js.
FLAG_MUTED = 8
FLAG_ARMED = 64
FLAG_MONITORING = 128

_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\"}


def unescape(value: str) -> str:
    """Decode the \\n, \\t and \\\\ escapes REAPER puts in EXTSTATE replies.

    One pass, left to right: replacing one escape at a time would read the
    encoded backslash in "\\\\n" as the start of a newline.
    """
    out = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value) and value[i + 1] in _ESCAPES:
            out.append(_ESCAPES[value[i + 1]])
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _lines(reply: str, command: str) -> list[list[str]]:
    return [line.split("\t") for line in reply.split("\n") if line.split("\t")[0] == command]


def parse_tracks(reply: str) -> list[dict]:
    tracks = []
    for tok in _lines(reply, "TRACK"):
        flags = int(tok[3])
        tracks.append({
            "index": int(tok[1]),
            "name": tok[2],
            "flags": flags,
            "muted": bool(flags & FLAG_MUTED),
            "armed": bool(flags & FLAG_ARMED),
            "monitoring": bool(flags & FLAG_MONITORING),
        })
    return tracks


def track_index_map(tracks: list[dict]) -> dict[str, int]:
    """Lowercase track name to web interface index. Index 0 is the master."""
    mapping: dict[str, int] = {}
    for track in tracks:
        if track["index"] != 0:
            mapping.setdefault(track["name"].lower(), track["index"])
    return mapping


def parse_transport(reply: str) -> dict | None:
    for tok in _lines(reply, "TRANSPORT"):
        return {
            "playstate": int(tok[1]),
            "position": float(tok[2]),
            "repeat": tok[3] != "0",
            "position_str": tok[4],
        }
    return None


def parse_extstate(reply: str, section: str, key: str) -> str | None:
    for line in reply.split("\n"):
        tok = line.split("\t", 3)
        if len(tok) == 4 and tok[:3] == ["EXTSTATE", section, key]:
            return unescape(tok[3])
    return None


def encode_command(command: dict) -> str:
    # Several commands share one request separated by ";", and "/" splits the
    # path, so every special char in the value is percent-encoded.
    value = quote(json.dumps(command, separators=(",", ":")), safe="")
    return f"SET/EXTSTATE/{SECTION}/cmd/{value}"


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": len(values), "min": min(values), "median": statistics.median(values), "max": max(values)}


def cadence(samples: list[tuple[float, int, float]]) -> dict:
    """Compare position deltas with wall clock deltas.

    samples are (wall_s, playstate, position_s). Only consecutive playing
    samples count. step_error is position delta minus wall delta per poll, and
    drift is its sum.
    """
    pairs = [(a, b) for a, b in zip(samples, samples[1:]) if a[1] == 1 and b[1] == 1]
    intervals = [(b[0] - a[0]) * 1000 for a, b in pairs]
    errors = [((b[2] - a[2]) - (b[0] - a[0])) * 1000 for a, b in pairs]
    return {
        "playing_samples": sum(1 for _, state, _ in samples if state == 1),
        "intervals_ms": summarize(intervals),
        "step_error_ms": summarize(errors),
        "drift_ms": sum(errors),
    }


class Reaper:
    def __init__(self, host: str, port: int, timeout_s: float = 2.0):
        self.base = f"http://{host}:{port}/_/"
        self.timeout_s = timeout_s
        self._ids = itertools.count(int(time.time() * 1000) % 1_000_000_000)

    def request(self, commands: str) -> str:
        with urllib.request.urlopen(self.base + commands, timeout=self.timeout_s) as reply:
            return reply.read().decode("utf-8", errors="replace")

    def tracks(self) -> list[dict]:
        return parse_tracks(self.request("TRACK"))

    def send(self, command: dict, timeout_s: float, poll_s: float = 0.01) -> tuple[dict, float]:
        """Send a listener command and wait for its ack. Returns (ack, round trip ms)."""
        command = {"id": next(self._ids), **command}
        start = time.perf_counter()
        self.request(encode_command(command))
        while time.perf_counter() - start < timeout_s:
            raw = parse_extstate(self.request(f"GET/EXTSTATE/{SECTION}/ack"), SECTION, "ack")
            if raw:
                ack = json.loads(raw)
                if ack.get("id") == command["id"]:
                    return ack, (time.perf_counter() - start) * 1000
            time.sleep(poll_s)
        raise TimeoutError(f"no ack for {command} after {timeout_s} s (is the listener running?)")


def _fmt(stats: dict) -> str:
    if not stats["n"]:
        return "n=0"
    return f"n={stats['n']} min={stats['min']:.1f} median={stats['median']:.1f} max={stats['max']:.1f}"


def cmd_tracks(reaper: Reaper, args) -> None:
    for t in reaper.tracks():
        marks = [m for m, on in (("muted", t["muted"]), ("armed", t["armed"]), ("monitoring", t["monitoring"])) if on]
        print(f"{t['index']:>3}  {t['name']:<20} {' '.join(marks)}")


def cmd_action(reaper: Reaper, args) -> None:
    reaper.request(str(ACTIONS[args.command]))
    print(parse_transport(reaper.request("TRANSPORT")))


def cmd_mute(reaper: Reaper, args) -> None:
    mapping = track_index_map(reaper.tracks())
    index = mapping.get(args.stem.lower())
    if index is None:
        sys.exit(f"no track named {args.stem!r}; tracks: {', '.join(mapping)}")
    reaper.request(f"SET/TRACK/{index}/MUTE/{args.value}")
    print(f"track {index} ({args.stem}) mute={args.value}")


def _print_ack(ack: dict, rtt_ms: float) -> None:
    print(f"{json.dumps(ack)}  round trip {rtt_ms:.1f} ms")


def cmd_load(reaper: Reaper, args) -> None:
    _print_ack(*reaper.send({"op": "load", "manifest": str(args.manifest.resolve())}, args.timeout))


def cmd_transpose(reaper: Reaper, args) -> None:
    _print_ack(*reaper.send({"op": "transpose", "semitones": args.semitones}, args.timeout))


def cmd_simple(reaper: Reaper, args) -> None:
    _print_ack(*reaper.send({"op": args.command}, args.timeout))


def cmd_watch(reaper: Reaper, args) -> None:
    samples = []
    start = time.perf_counter()
    next_poll = start
    while time.perf_counter() - start < args.seconds:
        transport = parse_transport(reaper.request("TRANSPORT"))
        if transport:
            samples.append((time.perf_counter(), transport["playstate"], transport["position"]))
        next_poll += args.interval
        time.sleep(max(0.0, next_poll - time.perf_counter()))
    result = cadence(samples)
    print(f"samples: {len(samples)}, playing: {result['playing_samples']}")
    print(f"poll interval ms (wall clock): {_fmt(result['intervals_ms'])}")
    print(f"position step minus wall step, ms: {_fmt(result['step_error_ms'])}")
    print(f"drift over the run: {result['drift_ms']:.1f} ms")


def cmd_bench(reaper: Reaper, args) -> None:
    manifests = [args.library / "song-a" / "manifest.json", args.library / "song-b" / "manifest.json"]
    round_trips, listener = [], []
    for run in range(args.runs):
        manifest = manifests[run % 2]
        ack, rtt_ms = reaper.send({"op": "load", "manifest": str(manifest.resolve())}, args.timeout)
        if not ack.get("ok"):
            sys.exit(f"run {run + 1}: load failed: {ack}")
        round_trips.append(rtt_ms)
        listener.append(ack["ms"])
        print(f"run {run + 1:>2} {manifest.parent.name}: round trip {rtt_ms:.1f} ms, listener {ack['ms']:.1f} ms")
    print(f"round trip ms: {_fmt(summarize(round_trips))}")
    print(f"listener ms:   {_fmt(summarize(listener))}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Drive REAPER for spike S3 through its web interface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds to wait for a listener ack, and for each HTTP reply (default 5)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("tracks", help="list tracks with their web interface index").set_defaults(func=cmd_tracks)
    for name, action in ACTIONS.items():
        sub.add_parser(name, help=f"run action {action}").set_defaults(func=cmd_action)

    mute = sub.add_parser("mute", help="mute (1) or unmute (0) a stem track by name")
    mute.add_argument("stem")
    mute.add_argument("value", choices=("0", "1"))
    mute.set_defaults(func=cmd_mute)

    load = sub.add_parser("load", help="ask the listener to load a song package")
    load.add_argument("manifest", type=Path)
    load.set_defaults(func=cmd_load)

    transpose = sub.add_parser("transpose", help="set the pitch of every stem except drums, in semitones")
    transpose.add_argument("semitones", type=int)
    transpose.set_defaults(func=cmd_transpose)

    sub.add_parser("ping", help="round trip to the listener").set_defaults(func=cmd_simple)
    sub.add_parser("quit", help="stop the listener").set_defaults(func=cmd_simple)

    watch = sub.add_parser("watch", help="poll TRANSPORT and compare position with the wall clock")
    watch.add_argument("--seconds", type=float, default=10.0)
    watch.add_argument("--interval", type=float, default=0.1, help="poll interval in seconds (default 0.1)")
    watch.set_defaults(func=cmd_watch)

    bench = sub.add_parser("bench", help="alternate loads of song-a and song-b and time them")
    bench.add_argument("--runs", type=int, default=10)
    bench.add_argument("--library", type=Path, default=Path(__file__).with_name("library"))
    bench.set_defaults(func=cmd_bench)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(Reaper(args.host, args.port, args.timeout), args)


if __name__ == "__main__":
    main()
