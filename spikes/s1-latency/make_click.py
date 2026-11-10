"""Write click.wav: 11 s of silence with a short click at exactly 1.000 s, 2.000 s ... 10.000 s.

Usage: python spikes/s1-latency/make_click.py

Standard library only. The output file is gitignored (*.wav).
"""

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 44_100
LENGTH_S = 11
CLICK_S = 0.005  # 5 ms burst
CLICK_HZ = 2_000
AMPLITUDE = 0.5


def main() -> None:
    frames = bytearray(SAMPLE_RATE * LENGTH_S * 2)  # 16-bit mono
    for second in range(1, LENGTH_S):
        start = second * SAMPLE_RATE
        for i in range(int(CLICK_S * SAMPLE_RATE)):
            # A cosine starts at its peak, so the click has a sharp onset that is easy to spot.
            value = int(AMPLITUDE * 32767 * math.cos(2 * math.pi * CLICK_HZ * i / SAMPLE_RATE))
            struct.pack_into("<h", frames, (start + i) * 2, value)

    out = Path(__file__).with_name("click.wav")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(frames))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
