# Karaoke Night: plan

> v0.3 · 2026-11-10 · Living document: update it whenever a decision changes.

## Status

*Last updated: 2026-11-11*

- **Phase:** 0 (proofs of concept) done. Phase 1 (alpha) is next.
- **Done:** plan, README, license, agent guidance (`CLAUDE.md`), first review of the plan (decisions recorded in §3, §5 and §6), S1 round 1, S3, S4, S2 and S3 round 2 (all pass), S5 (pass on the Studio side, the venue's downlink decides the time), S6 (pass, RMVPE chosen). Results in §6.
- **Next:**
  - milestone A1 (repository skeleton);
  - measure a package download on the venue's own network during the first night (A9);
  - S1 round 2 after an interface upgrade, which the beta's second mic needs anyway.

## 1. Goal

A self-hosted karaoke app for private use that plays the **original recording** of each song, split into stems: lead vocals, backing vocals, drums, bass, guitar, piano and other. Singers choose which stems stay on, and their live voices go through an effects chain in REAPER.

## 2. Key constraints

- **Latency.** The live voice path (mic → interface → effects → speakers) should feel natural to sing with. About 12 ms round trip is the usual reference, but S1 showed that around 25 ms can still be comfortable, so comfort decides. Effects in that path must add no latency of their own: no look-ahead compressors, and pitch correction in low-latency mode.
- **ASIO is single-client.** REAPER owns the audio device, so the app never opens it.
- **Original stems of well-known songs aren't publicly available,** because master recording rights are licensed case by case. Stems are produced with AI source separation instead. Separating lead from backing vocals is the least reliable step.
- **Streaming services don't provide audio.** Spotify's API is metadata only (the audio is DRM-protected). The practical sources are YouTube (via `yt-dlp`) and local files.
- **Separation needs an NVIDIA GPU to be fast,** while the playback machine should stay light and portable.

## 3. Decisions

| Topic | Decision | Why |
|---|---|---|
| Topology | Two roles. The **Studio** is a machine with an NVIDIA GPU that processes songs. The **Stage** is a Windows machine at the venue that plays them. | Separation needs a GPU, and the playback machine stays light and portable. |
| Studio ↔ Stage link | [Tailscale](https://tailscale.com) (private network) with HTTP and a token. | Works from any network without opening router ports. |
| Offline-first | The Stage plays anything already in its local library without internet. The Studio is needed only for new songs. | The venue's internet or the Studio may be unavailable. |
| Languages | Python 3.12 (with `uv`) for the Studio and the Stage. TypeScript, React and Vite for the UI. Lua for REAPER scripts. | The audio and ML ecosystem is Python. |
| Search | `ytmusicapi` (YouTube Music, "songs" filter), falling back to `yt-dlp ytsearch`. | Returns the official studio audio with a clean title, artist and duration. |
| Download | `yt-dlp` (best audio) with `ffmpeg`, on the Studio. | That's where the audio is processed. |
| Separation | `audio-separator` cascade: vocals with Mel-Band RoFormer Kim (`vocals_mel_band_roformer.ckpt`), lead from those vocals with the becruily Mel-Band karaoke model (`mel_band_roformer_karaoke_becruily.ckpt`), and instruments with Demucs `htdemucs_6s`. RoFormer overlap 2. Chosen in S2. | Best lead vs backing split of the three combinations tested, and the fastest. |
| Stem format | FLAC 16-bit/44.1 kHz, in a package with a `manifest.json`. | Lossless. About 25 to 30 MB per minute of song (S2). |
| Lyrics | LRCLIB, line-synced, **already in the alpha**. | The API is free, and karaoke without lyrics doesn't work. |
| Player | REAPER with a fixed session that stays open all night, driven by its built-in web interface (HTTP) and a resident Lua script. | The effects chain stays under full manual control in REAPER. |
| Storage | SQLite on the Stage (local library and history), plus files on disk. | No infrastructure to run. |
| Microphones | The REAPER template ships with 2 mic tracks from day one. | The beta needs two singers, and this avoids rebuilding the template. |
| Vocal effects | The effects chain is free and controlled by hand in REAPER on the Stage (for example pitch correction, reverb, delay, compression, doubling). The app never picks plugins. In the beta it only provides data that the chain may use (§6, Phase 2). | Effects need room to evolve, and REAPER is the right place to tune them. |
| Melody and key | RMVPE (`rmvpe-onnx`, on the CPU) on the lead stem, grouped into notes; key per section from the notes plus the instrumental's chroma, with Krumhansl-Kessler profiles over 16 s windows and a Viterbi smoothing. Chosen in S6. | Got the octave right where the other trackers jumped up on a strong held note, fewest chromatic artifacts, and about 0.5 s per minute of song. |
| Pitch correction | Alpha: chromatic mode (snap to the nearest semitone), which needs no song data. Beta: per-section key or target notes, from the melody analysis. | A fixed key sounds wrong in songs that change key. |
| Key change | Chosen at setup only. The key stays fixed while the song plays. | Simpler, and enough for singing. |
| Library sync | The Studio is the source of truth. The Stage mirrors the whole Studio library: it downloads what is missing and removes what the Studio no longer lists. Deleting a song means deleting it on the Studio. | Simple, and a song deleted by hand doesn't come back on the next sync. Disk use gets revisited if it becomes a problem. |
| Song identity | A song is identified by its source (the YouTube `video_id`). The same song from different uploads may end up duplicated, and is deleted by hand if needed. | Simple enough for now. |
| Studio availability | The Studio stays on during sessions. | New songs can be processed during the night. |
| Workflow | One PR per milestone. | Small, reviewable changes. |
| License | MIT, to be revisited if the app matures into a product. | Simple and permissive, and every dependency is permissive too. |

## 4. Architecture principles

1. **The Stage works offline.** A karaoke night can't depend on the internet. New songs are a bonus when the Studio is reachable.
2. **The stems add up to the original.** The last stem of each separation step is computed as the remainder:
   - `instrumental = mix − vocals`
   - `backing = vocals − lead`
   - `other = instrumental − (drums + bass + guitar + piano)`

   With every stem on, you hear the original recording without separation artifacts. The goal is to get as close to the original as possible, not bit-exact: writing each stem to 16-bit FLAC adds tiny differences, which are accepted. Tests compare against the original within a tolerance.
3. **Song packages are immutable and versioned** (`pipeline_version`). When a better model comes out, the library gets reprocessed.
4. **The player sits behind an interface.** Stage code talks to a `Player` interface (`load`, `play`, `stop`, `set_mute`, `transpose`, `position`), and REAPER is just one implementation of it. This leaves room to replace REAPER later.
5. **REAPER owns the audio.** The app never opens the audio device.

## 5. Architecture

```
STUDIO · machine with an NVIDIA GPU
  studio (FastAPI)
  ├─ job queue
  ├─ download: yt-dlp + ffmpeg
  ├─ separation: audio-separator (GPU)
  ├─ lyrics: LRCLIB
  └─ master library (song packages)
        ▲
        │  HTTP + token over Tailscale (new songs and sync)
        ▼
STAGE · Windows machine at the venue
  stage (FastAPI) ◄── HTTP / WebSocket ──► full-screen browser on the TV
  ├─ search: ytmusicapi (+ yt-dlp)
  ├─ local library: SQLite + packages
  ├─ session: state machine
  └─ REAPER bridge ── HTTP + ExtState ──► REAPER (fixed session)
                                           ├─ Lua listener (load song, transpose)
                                           ├─ Mic 1, Mic 2 with effects chains
                                           └─ Lead · Backing · Drums · Bass · Guitar · Piano · Other
                                                 │
                                                 ▼
                                    audio interface (ASIO) → speakers / PA
```

### 5.1 Song flow

1. **Search on the TV.** The Stage lists what's already in the library first (marked "ready"), then YouTube Music results (marked "process, ~2 min"). A library screen also lists every ready song.
2. **New song.** The Stage creates a job on the Studio, tracks its progress and downloads the package, with resume and hash checks.
3. **Setup.** The singer picks the active stems (presets *Karaoke*, *Backing only*, *Original*, or custom) and the key, in semitones. The key can't change once the song starts.
4. **Start.** The Stage tells REAPER to load the package and play.
5. **During the song.** Lyrics follow REAPER's playback position, and stems can be toggled live.
6. **End.** REAPER stops at the end of the project, and the screen returns to search.

### 5.2 Song package

```
library/<song_id>/
  manifest.json
  stems/lead.flac  backing.flac  drums.flac  bass.flac  guitar.flac  piano.flac  other.flac
  lyrics.lrc
```

Draft `manifest.json`:

```json
{
  "schema": 1,
  "pipeline_version": 1,
  "id": "song-title--artist-name--a1b2c3",
  "title": "Song Title",
  "artist": "Artist Name",
  "duration_s": 245.3,
  "source": { "type": "youtube_music", "video_id": "XXXXXXXXXXX" },
  "gain_db": -2.5,
  "stems": {
    "lead":    { "file": "stems/lead.flac",    "sha256": "…" },
    "backing": { "file": "stems/backing.flac", "sha256": "…" }
  },
  "lyrics": { "provider": "lrclib", "file": "lyrics.lrc", "synced": true, "offset_ms": 0 },
  "models": { "vocals": "…", "karaoke": "…", "instrumental": "htdemucs_6s" },
  "created_at": "2026-11-04T20:00:00Z"
}
```

- **`gain_db`:** the gain REAPER applies so that all songs play at the same loudness. The target LUFS is still open.
- **Clipping:** if any stem would exceed 0 dBFS when written to FLAC, all stems get the same reduction, compensated in `gain_db`. That way the stems still add up to the original.

### 5.3 Studio API

Every route requires `Authorization: Bearer <token>`.

| Method | Route | Description |
|---|---|---|
| `POST` | `/jobs` | Takes `{source, title, artist, album?, duration_s?}` and returns `{job_id, song_id}`. If a song from the same source (`video_id`) was already processed, it returns the finished package right away. |
| `GET` | `/jobs/{job_id}` | Status (`queued → downloading → separating → lyrics → packaging → done`, or `failed`) and progress. |
| `GET` | `/songs` | Master library listing. The Stage mirrors it: it downloads what is missing and removes what is no longer listed. |
| `GET` | `/songs/{song_id}/manifest` | The package manifest. |
| `GET` | `/songs/{song_id}/files/{path}` | A package file. Supports `Range`, so downloads can resume. |
| `DELETE` | `/songs/{song_id}` | Deletes a song from the master library. The Stage removes it on the next sync. |
| `DELETE` | `/songs` | Deletes the whole master library. |

### 5.4 Stage ↔ REAPER

**REAPER's built-in web interface** (`127.0.0.1:8080`):

| Command | Purpose |
|---|---|
| `/_/TRANSPORT` | Playback state (stopped or playing) and position in seconds. The Stage polls it every 100–250 ms. |
| `/_/TRACK` | Track list. On startup, the Stage builds a name → index map, so no indexes are hard-coded. |
| `/_/SET/TRACK/{i}/MUTE/{0\|1}` | Turns a stem on or off. |
| `/_/1007`, `/_/1016`, `/_/40042` | Play, stop, and go to the start of the project. |

**Lua listener**, for anything the web interface doesn't cover. It runs in a `defer` loop, started by `__startup.lua` when REAPER opens.

- **Request:** the Stage sets `SET/EXTSTATE/karaoke/cmd/<URL-encoded JSON>`, for example `{"id": 42, "op": "load", "manifest": "C:\\...\\manifest.json"}`.
- **Reply:** the listener runs the command and writes `{"id": 42, "ok": true}` to `karaoke/ack`. The Stage reads it with `GET/EXTSTATE/karaoke/ack` and matches the `id`.
- **One command at a time:** `cmd` and `ack` are single slots, so the Stage sends the next command only after the previous ack.
- **Operations:**
  - `load`: removes the previous song's items, inserts each stem on the track of the same name and applies `gain_db`. The project end follows the stems, as long as nothing else on the timeline runs longer (an item left on a mic track would).
  - `transpose`: shifts the key on every stem **except drums**. Drums have no key, and pitch shifting only degrades them. It is sent at setup, before playback starts.

**Session template:** "stop playback at end of project" is enabled, the mic tracks are armed with input monitoring on, and the stem tracks have input monitoring off (tracks created with REAPER's defaults inherit the user's default, which can be on). The effects chain on the mic tracks is left to the person running REAPER (§3).

### 5.5 Repository layout

```
karaoke-night/
├─ README.md
├─ LICENSE
├─ CLAUDE.md                    # guidance for AI coding agents
├─ docs/
│  └─ PLAN.md                   # this file
├─ pyproject.toml               # uv workspace
├─ packages/
│  ├─ core/                     # shared models (manifest, contracts), lightweight
│  ├─ studio/                   # Studio: jobs, download, separation, lyrics (torch, audio-separator)
│  └─ stage/                    # Stage: local server, library, REAPER bridge, session
├─ web/                         # React + Vite + TS: TV screen (in the beta, also the phone page)
├─ reaper/
│  ├─ scripts/                  # __startup.lua, karaoke_listener.lua, lib/json.lua
│  └─ template/                 # base session (.RPP) + effects chain guide
└─ .github/workflows/ci.yml     # ruff + pytest + tsc
```

`core` is kept separate so the Stage never has to install torch.

---

## 6. Execution plan

### Phase 0: proofs of concept

The goal is to remove the big risks before building the app. Each spike has a criterion that decides whether we keep the plan or change course. The spike scripts were removed from the repository after Phase 0; the links below point to the last commit that had them.

**Prerequisites:**
- [x] A test set of ~20 songs that reflects what people will actually sing. Include duets with constant harmony, songs heavy on backing vocals, and live recordings that are the best-known version. Keep it in `songs.local.txt` at the repository root, which is gitignored.
- [x] A dynamic microphone and an audio interface connected to the Stage machine.
- [x] Tailscale installed on the Studio and the Stage, on the same account.

| # | Spike | Where | How | Pass criterion |
|---|---|---|---|---|
| S1 | Latency | Stage + interface | Run first with the interface on hand, and again after any interface upgrade, to compare. Run LatencyMon. Test REAPER with the interface's native ASIO driver and with ASIO4ALL, at buffers of 64, 128 and 256, with the effects chain on. Measure the real round trip by recording a click, through the air with the mic or with a loopback cable. | Comfortable singing with pitch correction on. About 12 ms is the reference, but comfort decides. Otherwise, plan B: the interface's direct monitoring plus reverb/delay only (100% wet) from REAPER. |
| S2 | Separation | Studio | Run 5 test songs through 2–3 model combinations. Measure time and VRAM, listen to lead vs backing, and check that the stems add up to the original. | Acceptable quality at about 3 min per song or less. This spike picks the models. |
| S3 | REAPER | Stage | Lua listener loading 7 stems, with play, stop and mute driven from a Python script. | Loads in under 2 s, without interrupting mic audio during the swap. |
| S4 | Search and lyrics | Any | Run `ytmusicapi` + `yt-dlp` and LRCLIB on the 20 test songs. Don't penalize "live" versions, which in some genres are the best-known recordings. | The right version is in the top 3 for at least 18 of 20. Measure LRCLIB coverage. |
| S5 | Transfer | Stage on a remote network | Download a ~150 MB package from the Studio over Tailscale, simulating a remote network with a phone hotspot. | Under ~1 min per package. |
| S6 | Melody and key | Studio | On 5 songs separated in S2, including one that changes key, run pitch tracking (f0) on the lead stem with 2–3 trackers (for example pYIN, torchcrepe, RMVPE). Group the curve into notes, and detect the key per section from the melody plus the instrumental's harmony (chroma). Play the notes back as a synth over the instrumental and compare by ear. Also try a backing stem that holds a single harmony line. | The notes follow the sung melody well enough to score against, key changes are found where they happen, and it adds little time next to separation. This spike picks the tracker for the beta. |

**S1, round 1 (2026-11-10): pass.** Runbook and click generator in [`spikes/s1-latency/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f/spikes/s1-latency).

- An entry-level 1-preamp interface with no native ASIO driver (ASIO4ALL over the Windows class driver) measured about 24 ms round trip at 128 samples and 22 ms at 64. WASAPI exclusive was worse (about 30 ms). Most of the delay is fixed by the driver path, so smaller buffers barely help. A re-measurement after the setup fixes below gave the same ~24 ms at 128 samples, with much cleaner sound.
- Singing still felt comfortable, even with a free pitch correction plugin that adds about 1000 samples (about 23 ms). The plan keeps the effects in the live path. The official chain is defined later, preferring zero-latency pitch correction.
- The idle LatencyMon run was green (highest DPC under 1 ms). 128 samples is the working buffer.
- Hiss, low volume and a robotic, doubled voice all came from the setup, not from the hardware:
  - the interface was on a USB hub: plugged straight into the machine, the hiss dropped and the volume went up a lot;
  - Windows had the interface at a different sample rate from REAPER's 44.1 kHz, so ASIO4ALL was resampling. Plugging the interface into another USB port makes Windows see a new device, with the default sample rate and ASIO4ALL's default buffer (512 samples, about 57 ms round trip), so both have to be set again;
  - the voice was monitored twice (REAPER's input monitoring plus another monitoring path), which sounds doubled and robotic.
- These become setup rules for the template and the first-night checklist (A6, A9).

**S3, round 1 (2026-11-11): pass.** Listener, driver and runbook in [`spikes/s3-reaper/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f/spikes/s3-reaper). Two generated test songs, 7 WAV stems of 240 s each, on the Stage machine of S1.

- **Load:** 80 loads alternating two songs, with the mic monitored and someone singing into it: no audible dropout, and no failed load. Command to ack round trip: median 64 to 84 ms, max 101 ms (the first, cold load took 164 ms). The listener's own work: 12 to 19 ms. The 2 s target has a wide margin.
- **Mute per stem, transpose +2 while playing:** both worked by ear, with no crackle. CPU peaked at 3% with 6 stems pitch shifted.
- **Position:** `TRANSPORT` polled every 100 ms answered every ~95 ms (median), each step within about 15 ms of the wall clock (33 ms worst case while transposed), with under 7 ms of drift over 8 s. Enough for line-synced lyrics.
- **The web interface listens on IPv4 only** (`0.0.0.0:8080`). On Windows, `localhost` tries IPv6 first and every request waits about 2 s, so the Stage must use `127.0.0.1`.
- Confirmed against the running REAPER: the request encoding (`;`, `/` and `\` inside the JSON survive) and the escaping of `GET/EXTSTATE` replies. Tracks without a stem name, like the mic track, kept their items.
- For A6: new stem tracks inherited input monitoring on from the user's defaults (see the session template in §5.4), and stem tracks are found by name only, so a user track named like a stem would be overwritten. Both are template decisions for A6.

**S4, round 1 (2026-11-11): pass.** Script and runbook in [`spikes/s4-search/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f/spikes/s4-search). 21 test songs typed by hand (typos kept), in more than one language, with duets, live recordings and songs that have many covers. ytmusicapi 1.12.3, yt-dlp 2026.10.26.

- **Search:** the right version was in the ytmusicapi top 3 for 21 of 21 songs, and first for 20. The one miss at #1 was a different song by the same artist, with the right one at #2. Typos in titles and artist names didn't hurt. Two of the chosen versions are live recordings, and both ranked first. Covers, tributes, karaoke tracks and lullaby versions fill ranks 2 and 3, so the TV must show artist, album and duration to tell them apart.
- **yt-dlp as the fallback:** its #1 is usually the official music video, often with a different length or mix from the studio audio. That confirms ytmusicapi as the main source. Search with flat extraction needs no JS runtime; downloading does (`yt-dlp[default]` with its challenge solver, plus Deno), which worked on the Studio.
- **Lyrics:** LRCLIB had line-synced lyrics for 21 of 21 chosen versions, all on the first request (`/api/get` with title, artist, album and duration). Ranks 2 and 3 had far less (13 and 9 synced), as expected for covers.
- **For A7:** look up lyrics for the version the singer picks, not for the first result. `/api/get` can answer 200 with an empty record (no synced, plain or instrumental lyrics), so treat that like a miss and fall back to `/api/search`, keeping the best hit within the duration tolerance (2 s, the same as `/api/get`).
- **Times:** median per query of 0.64 s (ytmusicapi), 0.82 s (yt-dlp) and 0.29 s (LRCLIB).

**S2, round 1 (2026-11-11): pass.** Script and runbook in [`spikes/s2-separation/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f/spikes/s2-separation). Five test songs from S4 (a pop song with stacked lead vocals, a rock song with two-part harmonies, a live duet in constant harmony, a male and female duet, a disco song heavy on backing vocals), each through three combinations, all with `htdemucs_6s` for the instruments. audio-separator 0.48.0, torch 2.14.1 (CUDA 13.0) on the Studio.

| Combination | Vocals | Karaoke (lead) | Time, share of song length |
|---|---|---|---|
| A | BS-RoFormer viperx (`model_bs_roformer_ep_317_sdr_12.9755`) | Mel-Band aufr33/viperx | 0.77 to 0.79 |
| **B (chosen)** | **Mel-Band RoFormer Kim** | **Mel-Band becruily** | **0.55 to 0.57** |
| C | Mel-Band RoFormer Kim | BS-RoFormer frazer-becruily | 0.65 to 0.67 |

- **Listening (on the Stage, through REAPER):** all stems on sounds the same as the original release. B splits lead from backing best. On the song with stacked lead vocals, A and C sent nearly all of the chorus to backing, with voices slipping between layers, while B kept the main voice in lead and put the extra layers in backing, with a little lead left in the chorus, which is acceptable. On the two-part harmonies B was spot on. On the live duet the audience went to backing and the lead was good; what hurt was the live mix itself, so picking a studio recording when one exists is a choice made when the song is chosen, not in code.
- **Time, combination B:** 92 s for a 2:40 song up to 204 s for a 6:14 song, about 2 min 10 s for a 4 min song. Vocals and karaoke take about 40% each, Demucs about 12%. Model loading adds 2 to 3 s per model. The RoFormer default overlap is 4: overlap 2 halved the time with no audible difference (listened on one song), so overlap 2 is the setting.
- **VRAM:** peak memory reserved by PyTorch was 3.7 GB for the vocals step and 5.3 GB for the becruily karaoke step (Demucs 0.8 GB). That excludes the CUDA context, so a GPU with 8 GB should be enough; to be confirmed if a smaller GPU is ever used.
- **Stems add up to the mix:** on all 15 runs, the sum of the 7 written FLAC stems times `gain_db` matched the mix within 3.4 to 4.3 LSB at most, with the residual RMS between -90 and -93 dBFS. This only proves the package bookkeeping (subtraction order, alignment, `gain_db`, FLAC rounding), not the quality of the split, which only listening shows.
- **audio-separator rescales loud outputs on its own:** every output stem above full scale is scaled down to it, each by its own factor, before it is written. That shifted level between stems (drums came out about 1.8 dB low on one song, and the difference landed in `other`) and the sum still matched, so the null test can't see it. The fix: each model gets its input at -6 dB and its outputs are scaled back up, so nothing reaches full scale inside the library. A step that still reaches it is reported (none of the 15 did). On combination B the stems then needed a common `gain_db` of 0.4 to 2.5 dB to fit in 16 bits, as §5.2 planned.
- **audio-separator 0.48.0 needs `audioread`**, which it doesn't declare, and the `[gpu]` or `[cpu]` extra for onnxruntime. Downloading the source audio took 9 to 15 s per song.
- **Package size:** 64 to 130 MB for songs of 2:40 to 6:14, about 25 to 30 MB per minute.

**S3, round 2 (2026-11-11): pass.** The S2 packages (FLAC) loaded with the S3 listener and driver unchanged. Command to ack round trip: 80 to 182 ms on a warm REAPER, 276 ms for the first load after REAPER started. The listener stopped answering once while tracks were being edited by hand; it worked again after REAPER was restarted. The cause wasn't found.

**S5, round 1 (2026-11-11): pass on the Studio side, with a caveat.** Script and runbook in [`spikes/s5-transfer/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f/spikes/s5-transfer). The largest S2 package (135.7 MB, a 6:14 song) served by Python's static file server on the Studio and downloaded by the Stage over Tailscale, one file after the other, 3 runs per network. The sha256 of every stem matched the manifest on all 9 runs.

| Network on the Stage | Tailscale path | Time per package (min / median / max) | Median throughput |
|---|---|---|---|
| Home LAN, same network as the Studio | direct | 24.8 / 28.2 / 32.0 s | 38.5 Mbit/s |
| Phone hotspot (4G) | direct, through the home router's public address | 52.0 / 86.3 / 100.0 s | 12.6 Mbit/s |

- **The criterion is a rate:** about 150 MB in a minute is 20 Mbit/s sustained. Over the hotspot only the first run got there (20.9 Mbit/s), and throughput fell on each run after it (12.6, then 10.9).
- **The Studio's internet upload is not the limit:** three 50 MB uploads from the Studio to a public speed test measured 53 to 57 Mbit/s. The hotspot's mobile link was the bottleneck, so the time depends on the venue's downlink, which a phone hotspot only approximates. Measuring it at the venue goes into the first night (A9).
- **On the LAN, about 40 Mbit/s is the ceiling** even with both machines at home; the Studio is on Wi-Fi. Not investigated, since the LAN only matters for preloading the library.
- **Tailscale goes direct even from the mobile network:** `tailscale ping` answered through the relay (DERP) on the first ping only, then through the home router's public address, and stayed direct for every run. No relay slowdown to plan around.
- **What it means for a new song during the night:** about 2 min of separation (S2) plus 1 to 1.5 min of transfer over a mobile link like this one. The library is preloaded at home, so only songs asked for that night take this path. Opus just for transfer (§8) stays the fallback if the venue's network turns out slower.
- Each file is a new TCP connection here (8 per package). The Stage downloader in A5 can reuse one connection, which can only help.

**S6, round 1 (2026-11-12): pass, RMVPE chosen.** Script and runbook in [`spikes/s6-melody/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f/spikes/s6-melody). Six songs separated with combination B: the five from S2 plus a rock song with a well-known modulation before its last chorus. Three trackers on the lead stem, the backing of the two-part harmony song as an extra. torchcrepe 0.0.24 on the GPU, pYIN (librosa 1.0.0) and RMVPE (`rmvpe-onnx` 0.2.3) on the CPU, all on a common 10 ms grid. Notes and keys were judged by ear on the Stage, from a synth preview over the instrumental and from the note MIDI files in REAPER next to the stems.

| Tracker | Where | Time per minute of song | Notes per song |
|---|---|---|---|
| pYIN | CPU | 0.9 to 1.2 s | 276 to 497 |
| torchcrepe (`full`) | GPU, 6.3 GB reserved | 1.9 to 2.0 s | 262 to 493 |
| **RMVPE** | **CPU** | **0.4 to 0.6 s** | **284 to 505** |

- **Timing and key change:** note timing is consistent with the singing, and the modulation is found where it happens (at 3:23, confirmed by ear). The instrumental's chroma adds about 5 s per song. Everything together is under a tenth of the separation time.
- **Octave errors on strong held notes:** at the start of one chorus, a long, loud note sung with some drive, pYIN and torchcrepe both read an octave too high with high confidence (0.71 and 0.80), so no confidence rule would have caught it. RMVPE got the octave right and kept it as one 1.6 s note. Isolated octave jumps (one note up an octave and back) were rare for all three, 0 to 2 per song.
- **Chromatic artifacts:** runs of three or more notes moving by semitones, mostly at phrase endings where the voice gets quieter. Fewest with RMVPE on the modulating song (6, against 17 for pYIN and 13 for torchcrepe). After the vibrato fix below they no longer stood out by ear, so the confidence-based "hold the previous note" rule that was considered is not needed now; it stays an idea for the beta if scoring shows the need.
- **Agreement between trackers:** on frames all three call voiced, 83 to 94% are within 50 cents of each other, with a median difference of 5 to 15 cents. Folding octaves changes little, since octave errors are rare.
- **Vibrato was the first big problem, and it was in the note grouping, not the trackers:** a note with ±50 cents of vibrato or more turned into a burst of short notes, alternating semitones at ±80 cents. Splitting against a pitch smoothed over 190 ms (about one vibrato period) keeps vibrato up to ±100 cents in one note, and a held step still splits at the step. Slow slides of 250 ms or more between two notes still leave notes in between.
- **Notes where nobody sings:** a few short notes come from residue in the lead stem. Harmless for pitch correction, since there is no voice to correct there. Scoring has to ignore them (§8).
- **Key per section needs strong smoothing:** with 8 s windows and a low switch cost, songs that never change key came out with up to 14 sections, mostly neighbor keys sharing most notes (a minor key and its relative major, or a fifth away). 16 s windows and a switch cost of 6 left those songs with 1 to 3 sections and still found the real modulation, 2 s later than with 8 s. The section between the verse key and its relative major merged into one, which changes nothing for pitch correction since both keys have the same notes. Whether the remaining sections in a disco song are real wasn't checked.
- **torchcrepe adds random dither** of up to ±20 cents per frame (`bins_to_cents`); the spike fixes the seed and applies torchcrepe's mean filter so runs repeat.
- **Backing with a single harmony line:** torchcrepe and RMVPE agreed on 88% of frames within 50 cents on the two-part harmony song. Not judged by ear yet; that belongs with beta item 2.
- **For the beta (Phase 2, items 2 and 3):** RMVPE runs on the CPU in about 2 s per song, so it adds nothing noticeable to a job and could even run on the Stage. The model is about 360 MB, downloaded once.

### Phase 1: alpha

The alpha is a test run, not a finished event. The TV mirrors the Stage screen (over HDMI or screen casting) and is driven with the Stage's keyboard and mouse. There is no queue.

| Milestone | Deliverable | Done when |
|---|---|---|
| A1 | Repository skeleton: uv workspace, Vite, and ruff, pytest and tsc in GitHub Actions. | CI is green on the first PR. |
| A2 | `core`: pydantic model of the manifest, with validation and tests. | Tests pass for valid and invalid manifests. |
| A3 | Studio pipeline CLI: `studio process <url\|file>` produces a complete package. | All 20 test songs are processed, and their stems add up to the original. |
| A4 | Studio API: FastAPI with jobs, library, files and deletion, running as a service. | The Stage creates a job and downloads the package over Tailscale. |
| A5 | Stage library: SQLite, resumable downloads with hash checks, and a sync that mirrors the Studio library (adds and removes). | With no internet, the Stage lists and plays synced songs. A song deleted on the Studio disappears from the Stage after the next sync. |
| A6 | REAPER bridge: session template (pitch correction in chromatic mode), Lua listener, and the Python `Player` (load, play, stop, mute, transpose, position, end of song). | A song plays from start to end driven only by the Stage. |
| A7 | Search: `ytmusicapi` with a `yt-dlp` fallback, showing library matches first. | Same criterion as S4. |
| A8 | TV UI: search or library → setup (stems + key) → playing with lyrics → end → back to search. | A first-time user picks a song and sings without help. |
| A9 | First night: one shortcut opens REAPER, the Stage and the full-screen browser, plus a setup checklist (interface plugged straight into the machine, Windows sample rate matching REAPER's, a single monitoring path). | A full karaoke test session at the venue. |

### Phase 2: beta

Proposed order:

1. **Two microphones:** each mic gets a role (lead or backing), and the role decides which original vocal stems are muted.
2. **Lyrics and melody analysis** on the Studio. Together they become `pipeline_version` 2, and the library is reprocessed.
   - Lyrics split into lead and backing, with WhisperX alignment.
   - Melody: pitch tracking on the lead stem gives the expected notes and the key per section. Backing gets the same only when it holds a single harmony line; several voices at once aren't reliable. The tracker is RMVPE, from S6.
3. **Pitch correction guided by the song:** when it loads a song, the Lua listener writes a guide MIDI track with the expected notes and a key automation, both already transposed. Whether the pitch correction plugin uses them stays a choice made in REAPER. Target notes must allow singing an octave up or down.
4. **Pre-performance:** before singing, the app plays a short excerpt with its lyrics on screen, so the singer can adjust stem volumes, the key and the lyrics offset. The offset is saved per song on the Stage.
5. **Phone companion page** (local network, joined with a QR code): live lyrics and adding songs to the queue.
6. **Recordings and portal:** the portal runs on Vercel (Next.js + Blob), with share links. Mics are recorded dry (before effects) for scoring, and with effects for sharing.
7. **Post-song scoring:** pitch accuracy, timing and presence. Pitch accuracy compares the dry recording's pitch with the expected melody (transposed), ignoring octaves, with a tolerance of about 50 cents. It runs after the song, so nothing has to happen in real time. A live pitch display on the TV is out of scope for now: the app can't open the audio device, so it would have to run inside REAPER.
8. **Suggestions and playlists:**
   - suggestions from a low-cost LLM based on play history;
   - Spotify playlist import (Development Mode requires Premium and allows at most 5 users);
   - a decision between a shareable queue link and a Telegram bot for chat-based requests.
9. **Cloud Studio (plan B):** an on-demand GPU worker with the same API, so sessions don't depend on the Studio machine being on.

---

## 7. Hardware recommendations

| Item | Recommendation |
|---|---|
| Microphones | Cardioid dynamic (for example Shure SM58 or Behringer XM8500): they resist feedback and need no phantom power. If wireless, prefer analog UHF with XLR outputs; 2.4 GHz digital systems add a few ms. |
| Audio interface | At least 2 mic preamps and a native ASIO driver (for example Behringer UMC204HD or Focusrite Scarlett 2i2). Without a native driver it still works through ASIO4ALL, at about 22–24 ms round trip (S1). |
| Speakers | Wired from the interface output. **Never Bluetooth**, which adds 100–200 ms of delay. Enough power to hear the voice over the music without running at full volume. For larger rooms, a PA with a line input. |
| Stage machine | High-performance power plan, plugged in, validated with LatencyMon. |
| Studio machine | A CUDA-capable NVIDIA GPU. S2 peaked at 5.3 GB reserved by PyTorch (combination B), so 8 GB of VRAM should be enough. |

## 8. Risks

| Risk | Mitigation |
|---|---|
| High latency on the Stage machine (DPC or drivers) | Run S1 early. Plan B uses direct monitoring. Use an interface with a native ASIO driver. |
| Other singers notice the latency more than the maintainer did in S1 | Ask the singers on the first night (A9). Plan B and a native-driver interface remain options. |
| Studio offline, or no internet at the venue | Preload the library. In the beta, a cloud Studio. |
| YouTube changes and `yt-dlp` breaks | Keep `yt-dlp` up to date (recent versions need a JS runtime such as Deno). Accept local files as a source. |
| Poor lead vs backing separation (duets) | The "no vocals" preset always works. Pick models in S2 and reprocess when better ones appear. |
| Song missing from LRCLIB, or out of sync | Unsynced lyrics as a fallback. In the beta, offset adjustment in the pre-performance and automatic alignment. |
| Melody tracking misled by backing residue in the lead stem, melisma, or rap and spoken parts | Ignore sections with low confidence or no pitch, and don't score them. RMVPE (S6) got the octave right on a strong held note where the others jumped up. Ignore notes where the singer is silent. |
| Mics pick up the original backing vocals from the speakers, which skews scoring | Dynamic mics and speakers in front of the singers. Measure it when scoring is built. |
| Feedback with a PA | Dynamic mics, speakers in front of the singers, low cut and a gate in the effects chain. |
| Slow upload from the Studio, or a slow downlink at the venue | FLAC by default. S5 measured 53 to 57 Mbit/s of upload from the Studio, and a phone hotspot as the bottleneck (12.6 Mbit/s median). If the venue's network is that slow, high-bitrate Opus just for transfer. |

## 9. Open decisions

- [x] Separation models (from S2: combination B, see §3)
- [x] Pitch tracker and key detection method (from S6: RMVPE, see §3)
- [ ] Loudness target between songs (LUFS)
- [ ] Hardware recommendations confirmed (from S1 and sound tests)
