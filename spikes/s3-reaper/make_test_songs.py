"""Write two test song packages, song-a and song-b, for spike S3.

Usage: python spikes/s3-reaper/make_test_songs.py [--seconds 240] [--out DIR]

Each package holds 7 stereo 16-bit 44.1 kHz WAV stems, one steady tone per stem,
and a manifest.json shaped like the draft in docs/PLAN.md section 5.2. The output
goes to spikes/s3-reaper/library/ by default, which is gitignored.

Standard library only.
"""

import argparse
import hashlib
import json
import math
import wave
from array import array
from datetime import datetime, timezone
from pathlib import Path

SAMPLE_RATE = 44_100
STEMS = ("lead", "backing", "drums", "bass", "guitar", "piano", "other")
# 7 stems at this amplitude peak at 0.7 full scale when summed.
AMPLITUDE = 0.1

# Integer frequencies, so a 1 s block holds whole cycles and repeats without a click.
SONGS = {
    "song-a": {
        "title": "Test Song A",
        "gain_db": 0.0,
        "tones_hz": {"lead": 440, "backing": 554, "drums": 100, "bass": 110,
                     "guitar": 330, "piano": 659, "other": 880},
    },
    "song-b": {
        "title": "Test Song B",
        "gain_db": -6.0,
        "tones_hz": {"lead": 392, "backing": 494, "drums": 150, "bass": 98,
                     "guitar": 294, "piano": 587, "other": 784},
    },
}


def tone_block(freq_hz: int) -> bytes:
    """One second of an interleaved stereo 16-bit sine, little-endian."""
    peak = AMPLITUDE * 32_767
    frames = array("h")
    for i in range(SAMPLE_RATE):
        value = int(peak * math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE))
        frames.extend((value, value))
    return frames.tobytes()


def write_wav(path: Path, freq_hz: int, n_frames: int) -> str:
    block = tone_block(freq_hz)
    whole, rest = divmod(n_frames, SAMPLE_RATE)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for _ in range(whole):
            w.writeframes(block)
        w.writeframes(block[: rest * 4])
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_song(out_dir: Path, song_id: str, spec: dict, seconds: float) -> dict:
    stems_dir = out_dir / song_id / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    n_frames = int(seconds * SAMPLE_RATE)
    stems = {}
    for stem in STEMS:
        relative = f"stems/{stem}.wav"
        digest = write_wav(out_dir / song_id / relative, spec["tones_hz"][stem], n_frames)
        stems[stem] = {"file": relative, "sha256": digest}
    manifest = {
        "schema": 1,
        "pipeline_version": 1,
        "id": song_id,
        "title": spec["title"],
        "artist": "Test Artist",
        "duration_s": n_frames / SAMPLE_RATE,
        "source": {"type": "test_tone", "generator": "make_test_songs.py"},
        "gain_db": spec["gain_db"],
        "stems": stems,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (out_dir / song_id / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=240.0, help="length of each song (default 240)")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("library"),
                        help="library folder (default spikes/s3-reaper/library)")
    args = parser.parse_args(argv)
    for song_id, spec in SONGS.items():
        write_song(args.out, song_id, spec, args.seconds)
        print(f"Wrote {(args.out / song_id / 'manifest.json').resolve()}")


if __name__ == "__main__":
    main()
