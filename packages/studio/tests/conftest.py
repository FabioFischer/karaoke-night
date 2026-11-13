from collections.abc import Callable

import numpy as np
import pytest
import soundfile as sf
from studio.audio import SAMPLE_RATE, to_pcm16
from studio.cli import Tools


def make_mix(seconds: float = 0.25, peak: float = 0.8, seed: int = 0) -> np.ndarray:
    """Stereo tones plus noise, rounded to 16 bits like the converted mix.wav."""
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    rng = np.random.default_rng(seed)
    noise = 0.05 * rng.standard_normal((2, len(t)))
    left = 0.5 * np.sin(2 * np.pi * 220 * t) + 0.3 * np.sin(2 * np.pi * 1_250 * t)
    right = 0.4 * np.sin(2 * np.pi * 330 * t) + 0.3 * np.sin(2 * np.pi * 2_100 * t)
    mix = np.stack([left + noise[0], right + noise[1]], axis=1)
    mix *= peak / np.max(np.abs(mix))
    return (to_pcm16(mix).astype(np.float32) / 32_768).astype(np.float32)


class FakeSeparator:
    """Stands in for audio-separator: every output is a fraction of the input.

    Lengths are off by a few frames, like real model outputs, and the names
    use the case and extra outputs the real models give.
    """

    def __init__(self, vocals_gain=0.6, lead_gain=0.7, drums_gain=0.1, extra=3):
        self.calls: list[tuple[str, str, np.ndarray]] = []
        self.vocals_gain = vocals_gain
        self.lead_gain = lead_gain
        self.drums_gain = drums_gain
        self.extra = extra

    def _longer(self, audio):
        tail = np.full((self.extra, audio.shape[1]), 0.25, dtype=audio.dtype)
        return np.concatenate([audio, tail])

    def __call__(self, step, model_file, audio):
        self.calls.append((step, model_file, audio.copy()))
        if step == "vocals":
            return {
                "Vocals": self._longer(audio * self.vocals_gain),
                "Instrumental": audio * 0.1,
            }
        if step == "karaoke":
            return {
                "Vocals": (audio * self.lead_gain)[: -self.extra],
                "Instrumental": audio,
            }
        # Vocals and Other are far off on purpose: the cascade must not use them.
        return {
            "Vocals": audio * 7.0,
            "Drums": self._longer(audio * self.drums_gain),
            "Bass": audio * 0.2,
            "Guitar": (audio * 0.15)[: -self.extra],
            "Piano": audio * 0.05,
            "Other": audio * 9.0,
        }


class FakeTools:
    """Outside tools for the pipeline: a download that writes placeholder bytes,
    a convert that writes the mix, the fake separator and a canned LRCLIB.

    calls records what each tool was called with. fail maps a tool name to the
    exception it raises.
    """

    def __init__(self, mix: np.ndarray, separator, lyrics_routes=None):
        self.mix = mix
        self.separator = separator
        self.lyrics_routes = lyrics_routes or {
            "/api/get": (200, {"syncedLyrics": "[00:01.00] Line one"})
        }
        self.calls: list[tuple] = []
        self.fail: dict[str, Exception] = {}

    def _call(self, *call) -> None:
        self.calls.append(call)
        if call[0] in self.fail:
            raise self.fail[call[0]]

    def find_spec(self, name: str):
        return object()

    def fetch_info(self, video_id: str) -> dict:
        self._call("fetch_info", video_id)
        raise AssertionError("no metadata fetch expected")

    def download(self, video_id: str, out_dir):
        self._call("download", video_id)
        path = out_dir / "download.webm"
        path.write_bytes(b"compressed audio")
        return path

    def convert(self, source, target) -> None:
        self._call("convert", source.name)
        sf.write(target, self.mix, SAMPLE_RATE, subtype="PCM_16")

    def make_runner(self, model_dir, work_dir):
        self._call("make_runner", model_dir)
        return self.separator

    def lyrics_get(self, path: str, params: dict):
        self._call("lyrics", path, dict(params))
        return self.lyrics_routes[path]

    def tools(self) -> Tools:
        return Tools(
            find_spec=self.find_spec,
            fetch_info=self.fetch_info,
            download=self.download,
            convert=self.convert,
            make_runner=self.make_runner,
            lyrics_get=self.lyrics_get,
        )


@pytest.fixture
def mix() -> np.ndarray:
    return make_mix()


@pytest.fixture
def fake_tools(mix) -> FakeTools:
    return FakeTools(mix, FakeSeparator())


@pytest.fixture
def synthetic_mix() -> Callable[..., np.ndarray]:
    return make_mix


@pytest.fixture
def fake_separator() -> Callable[..., FakeSeparator]:
    return FakeSeparator
