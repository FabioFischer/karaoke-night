# S2: Separation

Runbook for spike S2 in [docs/PLAN.md](../../docs/PLAN.md) §6. The separation runs on the Studio machine (NVIDIA GPU, CUDA). Listening happens on the Stage, in REAPER, with the S3 driver.

**What it answers:** which models to use for the separation cascade of §4, how long a song takes, how much VRAM each step needs, and whether lead and backing vocals come apart well enough to sing over. It also checks that the 7 stems add up to the original (§4, principle 2).

**Pass criterion:** acceptable quality at about 3 minutes per song or less. This spike picks the models: 5 test songs, each through combos A, B and C.

## Files

| File | What it is |
|---|---|
| `separate.py` | The command line tool: `download`, `separate` and `null`. Its dependencies are declared inline (PEP 723), so `uv run` builds the environment. |
| `test_separate.py` | Unit tests with a fake separator: the remainder math, length alignment, the headroom against the library's own rescaling, the clipping rule, the manifest and the null test on a FLAC round trip. No GPU, no network. |

Run the tests from the repo root:

```
PYTHONDONTWRITEBYTECODE=1 uv run --with numpy --with soundfile python -m unittest discover -s spikes/s2-separation -v
```

## How the cascade works

Three models run one after the other (§4). Each step keeps one output, picked **by stem name**, and computes the rest as a remainder:

| Step | Model input | Output kept | Remainder |
|---|---|---|---|
| 1. vocals | the mix | `vocals` | `instrumental = mix - vocals` |
| 2. karaoke | the vocals | `vocals` (the lead voice) | `backing = vocals - lead` |
| 3. instruments (`htdemucs_6s`) | the instrumental | `drums`, `bass`, `guitar`, `piano` | `other = instrumental - (drums + bass + guitar + piano)` |

The other outputs (the vocals model's `instrumental` or `other`, the karaoke model's `instrumental`, Demucs's own `vocals` and `other`) are ignored. Because every last stem is a remainder, the 7 stems add up to the mix whatever the models do; only 16-bit rounding is left (see the null test below).

Details that matter for the numbers:

- **Names, not order.** audio-separator names each output file `<input>_(<Stem>)_<model>.wav`, and the script reads the stem from that name. If a model has no `vocals` output, `separate` stops and lists the names the model gave.
- **Length.** Model outputs can differ from the mix by a few samples. Each output is trimmed or padded with silence to the mix length before it is subtracted.
- **No rounding between steps.** Each model gets its input as a 32-bit float WAV, and audio-separator writes its outputs in the input's format, so nothing is rounded or clipped until the final FLACs.
- **Headroom against the library's normalization.** audio-separator scales down every output stem that peaks above its `normalization_threshold`, each stem on its own, before writing it; an MDXC (RoFormer) model does the same to its input. The script sets the threshold to 1.0 (the library default is 0.9). A kept output scaled on its own would come back at the wrong level, and the remainder after it (`instrumental`, `backing` or `other`) would absorb the difference. So each model gets its input at half level (`headroom` 0.5, -6 dB, in `timings.json`) and its kept outputs are doubled back before the remainders are taken; outputs that stay under full scale at half level are never touched. A fixed factor is not a guarantee: a model can give an output louder than its input. `timings.json` records per step the peak of the input and of the kept outputs as the library saw them (`input_peak`, `output_peak`), and `reached_threshold` is true when the input went above 1.0 or a kept output reached it; `separate` then prints a warning, and that step's stems are off in level (the sum still holds). Demucs's own `vocals` and `other` are not checked, since they are ignored.
- **Clipping (§5.2).** If any stem would clip in 16 bits (remainders can), all 7 stems are scaled by the same factor so the loudest peaks just under full scale, and `gain_db` in the manifest gets the compensation: the stems played at `gain_db` still add up to the mix. `gain_db` is rounded up to 3 decimals and the stems are scaled by that rounded value, so the manifest holds the exact gain and the loudest peak lands at most 0.001 dB under the ceiling. Without clipping, `gain_db` is 0.

## The combos

| Combo | Vocals model | Karaoke model |
|---|---|---|
| A | `model_bs_roformer_ep_317_sdr_12.9755.ckpt` | `mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt` |
| B | `vocals_mel_band_roformer.ckpt` | `mel_band_roformer_karaoke_becruily.ckpt` |
| C | `vocals_mel_band_roformer.ckpt` | `bs_roformer_karaoke_frazer_becruily.ckpt` |

All three use `htdemucs_6s.yaml` for the instruments. `--vocals-model`, `--karaoke-model` and `--instruments-model` replace one model of the combo; `audio-separator --list_models` lists the file names. When you override a model, pass `--out` too, so the package does not land on top of the combo's.

## Setup on the Studio

1. **NVIDIA driver** recent enough for CUDA 13.0: `nvidia-smi` must show `CUDA Version: 13.0` or higher. The script installs PyTorch built for CUDA 13.0 (`cu130`) from `download.pytorch.org`; nothing else from CUDA needs installing.
2. **uv** ([docs](https://docs.astral.sh/uv/)). It downloads Python 3.12 or later by itself. The first `uv run` downloads more than 2 GB of packages (the CUDA build of PyTorch alone is about 2 GB), so put `UV_CACHE_DIR` on a drive with room if the system drive is small.
3. **ffmpeg** on `PATH`. `download` uses it to convert the audio, and audio-separator refuses to start without it.
4. **deno** on `PATH`. yt-dlp needs a JavaScript runtime to solve YouTube's challenges; without one, downloads fail on the "n challenge".
5. **A model folder on a data drive.** Models download on first use: `htdemucs_6s` is about 55 MB, and the RoFormer models are much larger. Pass `--model-dir <folder>` to `separate`, or set `AUDIO_SEPARATOR_MODEL_DIR` (it wins over `--model-dir`, and the folder must exist). The default is `~/.cache/audio-separator-models`.
6. Check the environment once: `uv run spikes/s2-separation/separate.py --help`.

## Commands

Run from the repo root.

```
uv run spikes/s2-separation/separate.py download <video_id> [--out DIR]
uv run spikes/s2-separation/separate.py separate <mix.wav> --combo A|B|C [--model-dir DIR] [--overlap N] [--out DIR]
uv run spikes/s2-separation/separate.py null <manifest.json> [--mix mix.wav]
```

- **`download`** takes the best audio of a YouTube Music video, converts it with ffmpeg to a 44.1 kHz 16-bit stereo `mix.wav`, and writes `source.json` next to it (video_id, title, artist, duration from yt-dlp). Default folder: `spikes/s2-separation/library/downloads/<video_id>/`.
- **`separate`** runs the cascade on a `mix.wav` from `download` (it reads the `source.json` next to it) and writes the package to `spikes/s2-separation/library/<song_id>-<combo>/`:

  ```
  manifest.json      §5.2 format, no lyrics in this spike
  stems/lead.flac  backing.flac  drums.flac  bass.flac  guitar.flac  piano.flac  other.flac
  timings.json       time, peak VRAM, overlap and levels per step, versions, gain_db, null test
  ```

  It prints the timings and the null test at the end. The stems are FLAC 16-bit 44.1 kHz stereo, with paths relative to the manifest, so the package loads with the S3 driver as it is.

  `--overlap N` sets how many overlapping windows the RoFormer (MDXC) models of the vocals and karaoke steps average over. Without it the library uses the model's own `num_overlap` from its YAML, or 8 when the YAML has none. Each window advances by `chunk / N`, so the number of model passes, and the time of those two steps, grows with N: a lower N is faster, with possibly more artifacts at the window edges, which only listening tells. The value each step ran with is in `timings.json` (`overlap`) and printed with the timings; it is `null` for Demucs, which `--overlap` does not change. When you compare overlaps, pass `--out` too, so the packages do not land on top of each other.
- **`null`** reads the FLACs back, sums them, applies `gain_db` and compares with the mix (by default the one recorded in `timings.json`). It prints the largest error (in dBFS and in 16-bit steps, LSB) and the RMS of the residual in dBFS. `separate` runs it at the end.

**What the null test should show.** Each stem is rounded to the nearest 16-bit step, so the sum is off by at most half a step per stem: 3.5 LSB for 7 stems, times the gain when `gain_db` is above 0. On synthetic signals the tests measured a largest error of 2 LSB (about -84 dBFS) and a residual RMS of about -93 dBFS with `gain_db` 0. A residual far above that (say -60 dBFS or louder) means a bug, not rounding.

**What the null test does not show.** The remainders make the stems add up by construction, so a clean null test only proves the package bookkeeping: the subtraction order, the length alignment, `gain_db` and the FLAC writing. It says nothing about the separation itself: a stem shifted in time, voice leaking into the instruments, or lead and backing swapped all pass it. Only listening (below) shows those.

**Timing.** `seconds` is the wall clock of a step, model load included; `load_seconds` is the load alone. The first run of each model also downloads it, so time a song only after each combo has run once. `peak_vram_mb` is PyTorch's peak reserved memory during the step (`null` when CUDA is not available). It leaves out the CUDA context and anything outside PyTorch's allocator, so for GPU planning compare it with the memory `nvidia-smi` shows while a step runs.

## Listening on the Stage

1. Copy the package folders (`manifest.json` and `stems/`, `timings.json` is optional) from the Studio to the Stage, for example into `spikes/s2-separation/library/` there, which is gitignored.
2. Start REAPER with the S3 listener, as in the [S3 runbook](../s3-reaper/README.md) steps 1, 3 and 4.
3. Load and play a package:

   ```
   python spikes/s3-reaper/driver.py load spikes/s2-separation/library/<song_id>-A/manifest.json
   python spikes/s3-reaper/driver.py play
   ```

4. Listen with every stem on: it should sound like the original.
5. **Backing alone:** `mute lead 1`. Listen for lead voice left in the backing, and for backing parts that are missing.
6. **Lead alone:** unmute lead, then mute every other stem (`mute backing 1`, `mute drums 1`, and so on). Listen for backing voices and instruments left in the lead.
7. **Karaoke:** unmute everything, then mute only `lead`. This is what a singer gets.
8. Load the same song from the other combos (`<song_id>-B`, `<song_id>-C`) and repeat at the same spots. Each load replaces the stems, but mutes stay as they were, so reset them with `mute <stem> 0` first.

## Recording the results

Results name the songs, so they stay out of git. Copy the template into `spikes/s2-separation/results.local.md`, which is gitignored. Only a generic summary goes into `docs/PLAN.md`.

```markdown
# S2 results: <date>

GPU (model, VRAM) and driver:
torch / CUDA (from timings.json):
audio-separator version:

## Per song and combo
| Song | Combo | Audio s | Vocals s | Karaoke s | Instruments s | Total s | Peak VRAM MB (v / k / i) | gain_db | Null max LSB | Null RMS dBFS |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | A | | | | | | | | | |
| 1 | B | | | | | | | | | |
| 1 | C | | | | | | | | | |

## Listening notes
| Song | Combo | Lead alone (backing or instruments left in it?) | Backing alone (lead left in it? parts missing?) | Karaoke (mute lead): singable? | Other artifacts |
|---|---|---|---|---|---|
| 1 | A | | | | |

## Verdict
Combo picked, and why:
Time per song against the 3 min target:
```
