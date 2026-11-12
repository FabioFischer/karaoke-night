# S6: Melody and key

Runbook for spike S6 in [docs/PLAN.md](../../docs/PLAN.md) §6. The analysis runs on the Studio machine (NVIDIA GPU, CUDA) on packages that S2 wrote with combo B. Listening happens wherever the preview WAVs are copied to.

**What it answers:** which pitch tracker the beta uses on the lead stem (pYIN, torchcrepe or RMVPE), whether the f0 curve groups into notes that follow the sung melody well enough to score against (§6 Phase 2, items 2 and 7), whether the key per section is found where it changes (for the guide track and key automation of item 3), and how much time all of it adds next to separation.

**Pass criterion:** on 5 songs separated in S2, including one that changes key, the synth preview follows the sung melody by ear, the key change is found where it happens, and the analysis adds little time next to separation. Also try one song whose backing stem holds a single harmony line (`--stem backing`).

## Files

| File | What it is |
|---|---|
| `melody.py` | The command line tool: `track`, `notes`, `key`, `preview` and `all`. Its dependencies are declared inline (PEP 723), so `uv run` builds the environment. |
| `test_melody.py` | Unit tests on synthetic signals: Hz and MIDI conversion, note grouping (gaps, wide vibrato, held steps, a quick slide, short blips), pYIN on a synthetic sung line and on the semitone grid, key change detection and where the boundary falls, the agreement math (raw and octave folded), the common time grid, the levels of instrumental, lead and synth in the preview and the MIDI file, plus `all --trackers pyin` end to end on a placeholder package. CPU only, no network, no model downloads. |

Run the tests from the repo root:

```
PYTHONDONTWRITEBYTECODE=1 uv run --with numpy --with soundfile --with librosa python -m unittest discover -s spikes/s6-melody -v
```

## How the analysis works

Everything is written to `<pkg>/s6/`, inside the package folder, which lives in the gitignored library.

### 1. Tracking (`track`)

The stem (`lead` unless `--stem`) is downmixed to mono and resampled to 16 kHz, which is what CREPE and RMVPE expect; pYIN runs at the same rate so all three share one hop of 160 samples (10 ms). Each tracker's frames are then put on a **common grid**: frame `i` at `i * 0.01` s, from 0 to the end of the stem. Each grid time takes the nearest tracker frame; a grid time more than half a tracker hop from any frame is unvoiced. So the curves of different trackers line up frame by frame, whatever hop they ran with.

| Tracker | Library | Range | Voiced when | Confidence |
|---|---|---|---|---|
| `pyin` | `librosa.pyin`, frame 1024 samples, CPU | C2 to C6 (65.4 to 1046.5 Hz) | its own voiced flag (HMM) | voiced probability |
| `crepe` | `torchcrepe` 0.0.24, `full` model, Viterbi decoder, 3 frame mean on the pitch, CUDA when available | C2 to C6 | periodicity at least 0.21 (see below) | periodicity |
| `rmvpe` | `rmvpe-onnx` 0.2.3 (ONNX Runtime) | the model's own | salience at least 0.03 (the RVC default) | salience |

pYIN's pitch bins are 10 cents apart starting at its lowest frequency, so the range starts exactly on C2 to put the bins on the semitones (with 65.0 Hz the bins sat 0.8 cent below them, so a steady note read 0.8 cent flat).

CREPE's 0.21 comes from the torchcrepe README, where it is the periodicity threshold for removing unpitched regions of clean speech after a median filter on the periodicity; for silence that README suggests `threshold.Silence(-60)` instead. Here 0.21 is applied to the raw periodicity, with no filter, and the listening decides whether it holds on sung stems. torchcrepe also adds random dither of up to one 20 cent bin to every pitch, drawn from numpy's global random state, so two runs differed by about 9 cents (median) and up to about 30 cents on a placeholder line. The script seeds numpy before predicting, so runs repeat exactly, and averages the pitch over 3 frames, as the torchcrepe README does, which cut the spread on a steady synthetic tone from about 7.5 to about 4.3 cents (standard deviation).

Output per tracker (`<T>` is the tracker name, or `<tracker>-<stem>` for a stem other than lead):

- `f0-<T>.npz`: `times` (s), `f0` (Hz, NaN when unvoiced) and `confidence`, all on the grid.
- `track-<T>.json`: `seconds` (resampling plus inference, without the model load), `load_s` (the model load alone), `seconds_per_audio_minute`, `peak_vram_mb`, `voiced_fraction`, the device, the hops and the threshold.

Model weights:

- **torchcrepe** ships its weights inside the package (`full.pth`, about 89 MB in the uv cache), so nothing downloads.
- **rmvpe-onnx** downloads `rmvpe.onnx` from the Hugging Face repo `lj1995/VoiceConversionWebUI` on first use and copies it to `<model-dir>/rmvpe.onnx` (default `~/.cache/s6-melody-models`); Hugging Face also keeps its own copy in its cache (`~/.cache/huggingface`, or `HF_HOME`). The download happens before the clock starts. The package checks the file's SHA-256 every time it loads it, so `load_s` includes reading the whole file once.
- **RMVPE runs on the CPU.** `rmvpe-onnx` depends on the CPU build of `onnxruntime`, and the GPU build (`onnxruntime-gpu`) is a separate package that would replace it and needs its own CUDA libraries. The `device` field says which ONNX Runtime provider ran. If RMVPE is the pick and its CPU time matters, moving it to the GPU is a follow-up.
- `peak_vram_mb` is PyTorch's peak reserved memory, so it exists only for `crepe` on CUDA; it leaves out the CUDA context (compare with `nvidia-smi`, as in S2).

### 2. Notes (`notes`)

The f0 curve becomes notes in five steps:

1. Unvoiced frames are dropped (each tracker's own voicing above).
2. The pitch, in semitones, is median smoothed over 19 frames (190 ms), about one vibrato period at 5 to 6 Hz, so the smoothed curve stays near the center of the vibrato. A median, unlike a mean, keeps a held step sharp.
3. A note ends at a gap longer than 30 ms (shorter gaps are bridged), or when the smoothed pitch stays more than 0.7 semitone away from the note's smoothed median for 3 frames. On synthetic curves, vibrato of ±0.8 semitone at 5 to 6 Hz stays one note, and a held step of a semitone or more splits at the step.
   A slide between two held notes gives just those two notes when it is quick (4 semitones in 200 ms); a slower one (4 semitones in 250 ms or more) also leaves a note on the pitches it passes, for example 60, 61, 63, 64 for a slide from 60 to 64. Telling those apart from short real notes with vibrato needs more than a speed threshold, so it is left to the listening.
4. Notes shorter than 80 ms are dropped.
5. Each note gets `midi` = the median pitch rounded to a semitone, and `cents` = how far the median sits from it.

Output: `notes-<T>.json` (`onset`, `offset` in s, `midi`, `cents`, `confidence` as the mean over the note) and `notes-<T>.mid` (one track at 120 BPM, so the times line up with the song in any DAW).

### 3. Key (`key`)

1. **Chroma of the instrumental:** `drums + bass + guitar + piano + other`, mono, at 22.05 kHz, `librosa.feature.chroma_cqt` with a hop of 2048 samples (93 ms).
2. **Sliding windows** of `--window` seconds every `--hop` seconds (defaults 8 and 2). Per window: the chroma frames summed, and a pitch class histogram of the notes weighted by how long each note lasts inside the window. Each is normalized to sum 1 and they are mixed as `melody_weight * melody + (1 - melody_weight) * chroma` (`--melody-weight`, default 0.5). A window without notes uses the chroma alone.
3. **Correlation** of each window with the 24 Krumhansl-Kessler profiles (12 major, 12 minor).
4. **Smoothing:** a Viterbi path over the windows that maximizes the summed correlation minus `--switch-penalty` (default 1.0) per key change, so a single odd window does not flip the key.
5. **Sections:** consecutive windows with the same key; a boundary sits halfway between the centers of the last window of one key and the first of the next. `score` is the mean correlation of the key over its windows (1.0 is a perfect match).

`keys-<T>.json` holds the combined answer and also `melody_only` (melody weight 1) and `chroma_only` (melody weight 0), and the command prints all three, so the run shows which source finds the key. The boundary of a key change is only as precise as the windows: expect it within about one window of the real change.

### 4. Preview (`preview`)

A 44.1 kHz 16-bit stereo WAV, `preview-<T>.wav`: the instrumental at the package's `gain_db`, plus a triangle wave playing the notes at their rounded MIDI pitch (10 ms attack, 30 ms release). The synth's level is set to the instrumental's RMS plus `--synth-db` (default 0 dB), so the line sits on top of the mix. `--with-lead` adds the tracked stem at `gain_db + --lead-db` (default -12 dB) and writes `preview-<T>-with-lead.wav`, so the synth can be checked against the voice. If the sum would go above -1 dBFS, the whole preview is scaled down to it.

### 5. All (`all`)

`all` computes the chroma once, then runs `track`, `notes`, `key` and `preview` for each tracker in `--trackers` (default `pyin,crepe,rmvpe`), and writes `summary.json`: per tracker the timings, `voiced_fraction`, `note_count`, the sections and the melody-only and chroma-only answers, plus `chroma_seconds` and the **pairwise agreement** between trackers. The agreement uses only the grid frames that every tracker of the run calls voiced: per pair, `median_abs_cents` (median absolute difference) and `within_50` (share of those frames within 50 cents). An octave error counts as 1200 cents there; since scoring ignores the octave, `median_abs_cents_folded` and `within_50_folded` take the difference modulo an octave (folded to at most 600 cents), and the command prints both. `all` writes no `--with-lead` preview; run `preview --with-lead` for the A/B.

## Setup on the Studio

1. **NVIDIA driver** for CUDA 13.0, as in S2: the script installs PyTorch and torchaudio built for `cu130` from `download.pytorch.org`.
2. **uv** ([docs](https://docs.astral.sh/uv/)). The first `uv run` builds a new environment of more than 2 GB (the CUDA build of PyTorch), separate from S2's, so keep `UV_CACHE_DIR` where S2 had it.
3. **The S2 packages** of combo B, under `spikes/s2-separation/library/<song_id>-B/`.
4. **A model folder** on a data drive for `rmvpe.onnx` (about 360 MB), passed as `--model-dir`.
5. Check the environment once: `uv run spikes/s6-melody/melody.py check`. It imports torch, torchcrepe and rmvpe_onnx and prints their versions, the CUDA device and the ONNX Runtime providers.

## Commands

Run from the repo root.

```
uv run spikes/s6-melody/melody.py all <pkg> [--trackers pyin,crepe,rmvpe] [--stem lead] [--model-dir DIR]
    [--window S] [--hop S] [--melody-weight W] [--switch-penalty P]
uv run spikes/s6-melody/melody.py track <pkg> --tracker pyin|crepe|rmvpe [--stem lead] [--model-dir DIR]
uv run spikes/s6-melody/melody.py notes <pkg> --tracker T [--stem lead]
uv run spikes/s6-melody/melody.py key <pkg> --tracker T [--stem lead] [--window S] [--hop S]
    [--melody-weight W] [--switch-penalty P]
uv run spikes/s6-melody/melody.py preview <pkg> --tracker T [--stem lead] [--with-lead]
    [--synth-db DB] [--lead-db DB]
uv run spikes/s6-melody/melody.py check
```

`<pkg>` is the package folder (the one holding `manifest.json`). Each command reads what the one before it wrote, so after one `all`, `key` can run again with other window or penalty values without tracking again. Time a song only after one full run: the first downloads the RMVPE model, and the first pYIN and chroma in a fresh environment also compile librosa's numba code (on a placeholder package pYIN took 6.2 s on the first run and 2.0 s on the next).

On the song with a single harmony line in the backing stem: `all <pkg> --stem backing`. Its files carry the stem in the name (`f0-crepe-backing.npz`, ...), so they do not overwrite the lead's.

## Listening

Copy the `s6/preview-*.wav` files to any machine with headphones.

1. **Per tracker, `preview-<T>.wav`:** does the synth follow the sung melody you know? Listen for wrong notes, octave jumps, notes missing where the voice sings (low voicing, breathy or quiet parts), and notes where nobody sings (backing voices or instruments left in the lead stem).
2. **A/B with the voice:** `preview <pkg> --tracker <T> --with-lead`. The synth and the quiet lead should move together; a synth note that starts late or ends early shows the timing.
3. **Fast passages and ornaments:** melisma and slides should not turn into a cloud of short notes, and long held notes with vibrato should stay one note.
4. **Key:** at the time `key` prints for the change, the song should really modulate. Compare the combined, melody-only and chroma-only answers.

## Recording the results

Results name the songs, so they stay out of git. Copy the template into `spikes/s6-melody/results.local.md`, which is gitignored. Only a generic summary goes into `docs/PLAN.md`.

```markdown
# S6 results: <date>

GPU (model, VRAM) and driver:
torch / CUDA, ONNX Runtime provider (from track-*.json):

## Timing per song (summary.json)
| Song | Audio s | Chroma s | pYIN s (load) | CREPE s (load, VRAM MB) | RMVPE s (load) |
|---|---|---|---|---|---|
| 1 | | | | | |

## Tracking per song
| Song | Tracker | Voiced % | Notes | Agreement (pair: median cents / within 50) |
|---|---|---|---|---|
| 1 | pyin | | | |

## Listening notes
| Song | Tracker | Follows the melody? | Wrong or octave notes | Missing or extra notes | Fast parts and vibrato |
|---|---|---|---|---|---|
| 1 | pyin | | | | |

## Key
| Song | Real key changes (time, keys) | Combined | Melody only | Chroma only |
|---|---|---|---|---|
| 1 | | | | |

## Backing with one harmony line
Song, tracker, does it follow:

## Verdict
Tracker picked, and why:
Key method (combined, chroma only, melody only) and window, hop, penalty:
Time added per song next to separation:
```
