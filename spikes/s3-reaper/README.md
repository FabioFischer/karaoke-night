# S3: REAPER bridge

Runbook for spike S3 in [docs/PLAN.md](../../docs/PLAN.md) §6. It happens in REAPER on the Stage machine, driven from a terminal on the same machine.

**What it answers:** can a Python process drive a REAPER session that stays open, through the web interface (§5.4) and a resident Lua listener: load a song's 7 stems, play, stop, mute stems one by one, transpose, and follow the playback position, without disturbing the live mic?

**Pass criterion:** a song loads in under 2 s, and the monitored mic keeps sounding with no dropout while songs are swapped. Also checked: mute per stem, transpose +2 while playing with no crackle (and how much CPU it costs), and how steadily the reported position follows the clock.

## Files

| File | What it is |
|---|---|
| `karaoke_listener.lua` | The ReaScript started inside REAPER. It runs `load`, `transpose`, `ping` and `quit`, sent through ExtState. |
| `lib/json.lua` | [rxi/json.lua](https://github.com/rxi/json.lua) v0.1.2 (MIT, notice in the file header), vendored unmodified from commit `dbf4b2d`. |
| `make_test_songs.py` | Writes two test packages, `song-a` and `song-b`, under `library/` (gitignored). |
| `driver.py` | The command line driver. Talks to REAPER at `http://127.0.0.1:8080/_/`. |
| `test_driver.py` | Unit tests for the driver's parsing and encoding, and for the package writer. |

Everything is Python standard library. Run the tests from the repo root with `python -m unittest discover -s spikes/s3-reaper -v`.

## How the bridge works

- **Transport, tracks and mute** go straight to the web interface: `TRANSPORT`, `TRACK`, `SET/TRACK/<i>/MUTE/<0|1>`, and actions `1007` (play), `1016` (stop), `40042` (go to start). Track indexes come from `TRACK`, where index 0 is the master, so none are hard-coded.
- **Everything else** goes to the listener. The driver sends `SET/EXTSTATE/karaoke/cmd/<JSON>`, with every special character in the JSON percent-encoded (the web interface splits requests on `;` and paths on `/`). The listener checks `karaoke/cmd` on every defer cycle (about 30 times a second), deletes it, runs the command and writes `{"id": …, "op": …, "ok": true, "ms": …}` (or `"ok": false, "error": …`) to `karaoke/ack`. The driver polls `GET/EXTSTATE/karaoke/ack` until the `id` matches. `GET/EXTSTATE` encodes newlines, tabs and backslashes as `\n`, `\t` and `\\`, which the driver decodes before reading the JSON.
- **`load`** reads the manifest and, for each stem (lead, backing, drums, bass, guitar, piano, other), finds the track with that name (case does not matter), creates it at the end if it is missing, deletes the items on that track only, and inserts the stem at position 0 with its full length and `gain_db` as item volume. Tracks with other names, such as the mic tracks, are never touched. It is one undo step.
- **`transpose <n>`** sets the pitch of every stem item to `n` semitones, except drums. It is absolute: `transpose 2` twice is still +2, and `transpose 0` resets.
- `ms` in the ack is the time the listener spent on the command. The round trip the driver prints adds the HTTP requests and the wait for the next defer cycle.

## What you need

- The Stage machine with REAPER, the audio interface and the mic set up as in S1 (same USB port, 44.1 kHz, 128 samples).
- A terminal with Python 3.11 or later in the repo folder.
- About 600 MB of free disk for the two 240 s test songs (`--seconds 60` writes about 150 MB).

## 1. Enable the web interface

1. *Options → Preferences → Control/OSC/web → Add*.
2. *Control surface mode*: **Web browser interface**. Port **8080**. Leave the username and password empty, and the default interface as it is.
3. *OK*, then open `http://127.0.0.1:8080` in a browser: REAPER's remote page should load. Close it.

Use `127.0.0.1`, not `localhost`. On Windows `localhost` resolves to the IPv6 address `::1` first, and when the server listens on IPv4 only every request waits about 2 s before it falls back to `127.0.0.1`. That alone would fail the 2 s load criterion and slow `watch` to one poll every 2 s.

## 2. Generate the test songs

```
python spikes/s3-reaper/make_test_songs.py
```

This writes `spikes/s3-reaper/library/song-a/` and `song-b/`, each with 7 stereo WAV stems of a steady tone (a different tone per stem, and different tones in each song) and a `manifest.json`. Song A has `gain_db` 0, song B has -6, so the item volume shows whether the gain was applied. The 7 tones summed peak at about -3 dBFS.

## 3. Prepare the session

1. Open the session with the **Mic** track from S1: armed, input monitoring on, effects chain on.
2. The stem tracks (*Lead*, *Backing*, *Drums*, *Bass*, *Guitar*, *Piano*, *Other*) can exist already, or the listener creates the missing ones at the end of the track list on the first load.
3. Put any short item on the Mic track (a recording or the S1 click), shorter than the test songs. It must still be there after every load. Delete it when the load checks are done: items on the Mic track count toward the end of the project, so one that runs past the song keeps playback going after the song ends, and *Stop at end of project* (next step) stops there instead.
4. Check that *Preferences → Audio → Playback → Stop/repeat playback at end of project* is on, as §5.4 asks.
5. Open *View → Performance Meter* and keep it visible.

## 4. Start the listener

1. *Actions → Show action list → New action → Load ReaScript*, pick `spikes/s3-reaper/karaoke_listener.lua`, then *Run*.
2. In the terminal: `python spikes/s3-reaper/driver.py ping`. It prints the ack and the round trip.

Errors from the script appear in REAPER's *ReaScript console output* window. Running the script again while it runs makes REAPER ask whether to terminate the running instance; say yes to restart it. `driver.py quit` stops it cleanly.

## 5. Driver commands

```
python spikes/s3-reaper/driver.py tracks                  # index, name, muted/armed/monitoring
python spikes/s3-reaper/driver.py play | stop | start     # 1007, 1016, 40042, then prints TRANSPORT
python spikes/s3-reaper/driver.py mute <stem> <0|1>       # for example: mute lead 1
python spikes/s3-reaper/driver.py load <manifest.json>
python spikes/s3-reaper/driver.py transpose <n>           # semitones, absolute, drums excluded
python spikes/s3-reaper/driver.py ping | quit
python spikes/s3-reaper/driver.py watch [--seconds 10] [--interval 0.1]
python spikes/s3-reaper/driver.py bench [--runs 10]       # alternates song-a and song-b
```

Common options go before the command: `--host` (default `127.0.0.1`), `--port` (default 8080), `--timeout` (seconds to wait for an ack and for each HTTP reply, default 5).

## 6. Measure

**Load time.**

1. With the transport stopped: `driver.py bench --runs 10`. Write down min, median and max of the round trip and of the listener's own `ms`.
2. After the last load, check the tracks: 7 stem items at position 0, each as long as the song, item volume 0 dB (song A) or -6 dB (song B) in the item properties (*F2*). The Mic track item is still there. *Ctrl+Z* once undoes the whole load.

**Mic during swaps.** Talk or sing into the monitored mic without pause while `bench --runs 10` runs, once stopped and once playing (`driver.py play`, then `bench`). Any gap, click or dropout in the mic fails the criterion. Also watch the Performance Meter for spikes during the loads.

**Mute per stem.** `driver.py load …/song-a/manifest.json`, then `play`. Mute each stem with `mute <stem> 1` and listen for its tone dropping out; `tracks` shows it as muted. Unmute with `mute <stem> 0`.

**Transpose while playing.** With song A playing, note the CPU in the Performance Meter, then `driver.py transpose 2`. Listen for crackles or a gap at the switch. Every tone except the drums tone should go up 2 semitones (the lead from 440 Hz to about 494 Hz). Note the CPU with the pitch shift on, then `transpose 0`. The pitch shift uses the project's default pitch shift mode (*File → Project settings*); write down which mode it was.

**Position cadence.** With a song playing: `driver.py watch --seconds 30`. It polls `TRANSPORT` every 100 ms and reports the poll interval, the position step minus the wall clock step per poll, and the total drift. Lyrics follow this position (§5.1), so large steps or drift matter.

| Symptom | Likely cause |
|---|---|
| `Connection refused` | The web interface is off, or on another port (step 1). |
| `no ack … after 5 s` | The listener is not running, or stopped on an error (check the ReaScript console). |
| Ack with `"ok": false` | The listener ran the command and it failed: the error says why, for example a manifest it cannot open. |
| `no track named …` on `mute` | No track with that name yet. Load a song first, or check `tracks`. |

## Recording the results

Results name the hardware, so they stay out of git. Copy the template into `spikes/s3-reaper/results.local.md`, which is gitignored. Only a generic summary goes into `docs/PLAN.md`.

```markdown
# S3 results: <date>

REAPER version:
Machine (CPU, RAM, Windows build):
Audio device / buffer:
Pitch shift mode:

## Load time (bench --runs 10)
| Transport | Round trip min / median / max (ms) | Listener ms min / median / max |
|---|---|---|
| Stopped | | |
| Playing | | |

Items correct (position 0, full length, gain on song B)? Mic item intact? One undo step?

## Mic during swaps
| Transport | Dropout or click? | Performance Meter spike? |
|---|---|---|
| Stopped | | |
| Playing | | |

## Mute per stem
| Stem | Mute works? | Notes |
|---|---|---|
| lead | | |
| backing | | |
| drums | | |
| bass | | |
| guitar | | |
| piano | | |
| other | | |

## Transpose +2 while playing
Crackle or gap at the switch?
Drums unchanged?
CPU before / after:

## Position cadence (watch --seconds 30)
Poll interval ms min / median / max:
Step error ms min / median / max:
Drift over 30 s (ms):

## Verdict
Pass / fail, and why:
```
