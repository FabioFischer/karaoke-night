# Karaoke Night: plan

> v0.2 · 2026-11-05 · Living document: update it whenever a decision changes.

## Status

*Last updated: 2026-11-05*

- **Phase:** 0 (proofs of concept), not started.
- **Done:** plan, README, license, agent guidance (`CLAUDE.md`), first review of the plan (decisions recorded in §3, §5 and §6).
- **Next:**
  - spikes S2 (separation), S3 (REAPER), S4 (search and lyrics) and S6 (melody and key), which don't need a microphone;
  - S1 (latency) and S5 (transfer) once their prerequisites are ready (§6). Hardware purchases get reviewed before S1.

## 1. Goal

A self-hosted karaoke app for private use that plays the **original recording** of each song, split into stems: lead vocals, backing vocals, drums, bass, guitar, piano and other. Singers choose which stems stay on, and their live voices go through an effects chain in REAPER.

## 2. Key constraints

- **Latency.** The live voice path (mic → interface → effects → speakers) must stay at roughly 12 ms round trip or less to feel natural. Effects in that path must add no latency of their own: no look-ahead compressors, and pitch correction in low-latency mode.
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
| Separation | `audio-separator` cascade (RoFormer → Karaoke model → Demucs 6s). The exact models are chosen in Phase 0. | Best open-source quality available today. |
| Stem format | FLAC 16-bit/44.1 kHz, in a package with a `manifest.json`. | Lossless. Roughly 100–200 MB per song (to be measured). |
| Lyrics | LRCLIB, line-synced, **already in the alpha**. | The API is free, and karaoke without lyrics doesn't work. |
| Player | REAPER with a fixed session that stays open all night, driven by its built-in web interface (HTTP) and a resident Lua script. | The effects chain stays under full manual control in REAPER. |
| Storage | SQLite on the Stage (local library and history), plus files on disk. | No infrastructure to run. |
| Microphones | The REAPER template ships with 2 mic tracks from day one. | The beta needs two singers, and this avoids rebuilding the template. |
| Vocal effects | The effects chain is free and controlled by hand in REAPER on the Stage (for example pitch correction, reverb, delay, compression, doubling). The app never picks plugins. In the beta it only provides data that the chain may use (§6, Phase 2). | Effects need room to evolve, and REAPER is the right place to tune them. |
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

**REAPER's built-in web interface** (`localhost:8080`):

| Command | Purpose |
|---|---|
| `/_/TRANSPORT` | Playback state (stopped or playing) and position in seconds. The Stage polls it every 100–250 ms. |
| `/_/TRACK` | Track list. On startup, the Stage builds a name → index map, so no indexes are hard-coded. |
| `/_/SET/TRACK/{i}/MUTE/{0\|1}` | Turns a stem on or off. |
| `/_/1007`, `/_/1016`, `/_/40042` | Play, stop, and go to the start of the project. |

**Lua listener**, for anything the web interface doesn't cover. It runs in a `defer` loop, started by `__startup.lua` when REAPER opens.

- **Request:** the Stage sets `SET/EXTSTATE/karaoke/cmd/<URL-encoded JSON>`, for example `{"id": 42, "op": "load", "manifest": "C:\\...\\manifest.json"}`.
- **Reply:** the listener runs the command and writes `{"id": 42, "ok": true}` to `karaoke/ack`. The Stage reads it with `GET/EXTSTATE/karaoke/ack`.
- **Operations:**
  - `load`: removes the previous song's items, inserts each stem on the track of the same name, applies `gain_db` and sets the project end.
  - `transpose`: shifts the key on every stem **except drums**. Drums have no key, and pitch shifting only degrades them. It is sent at setup, before playback starts.

**Session template:** "stop playback at end of project" is enabled, and the mic tracks are armed with input monitoring on. The effects chain on the mic tracks is left to the person running REAPER (§3).

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
├─ spikes/                      # Phase 0: throwaway proofs of concept
└─ .github/workflows/ci.yml     # ruff + pytest + tsc
```

`core` is kept separate so the Stage never has to install torch.

---

## 6. Execution plan

### Phase 0: proofs of concept

The goal is to remove the big risks before building the app. Each spike has a criterion that decides whether we keep the plan or change course.

**Prerequisites:**
- [ ] A test set of ~20 songs that reflects what people will actually sing. Include duets with constant harmony, songs heavy on backing vocals, and live recordings that are the best-known version. Keep it in `spikes/songs.local.txt`, which is gitignored.
- [ ] A dynamic microphone and an audio interface connected to the Stage machine.
- [ ] Tailscale installed on the Studio and the Stage, on the same account.

| # | Spike | Where | How | Pass criterion |
|---|---|---|---|---|
| S1 | Latency | Stage + interface | Run first with the interface on hand, and again after any interface upgrade, to compare. Run LatencyMon. Test REAPER with the interface's native ASIO driver and with ASIO4ALL, at buffers of 64, 128 and 256, with the effects chain on. Measure the real round trip with a loopback cable (output → free input) recording a click. | Around 12 ms or less measured, and comfortable singing with pitch correction on. Otherwise, plan B: the interface's direct monitoring plus reverb/delay only (100% wet) from REAPER. |
| S2 | Separation | Studio | Run 5 test songs through 2–3 model combinations. Measure time and VRAM, listen to lead vs backing, and check that the stems add up to the original. | Acceptable quality at about 3 min per song or less. This spike picks the models. |
| S3 | REAPER | Stage | Lua listener loading 7 stems, with play, stop and mute driven from a Python script. | Loads in under 2 s, without interrupting mic audio during the swap. |
| S4 | Search and lyrics | Any | Run `ytmusicapi` + `yt-dlp` and LRCLIB on the 20 test songs. Don't penalize "live" versions, which in some genres are the best-known recordings. | The right version is in the top 3 for at least 18 of 20. Measure LRCLIB coverage. |
| S5 | Transfer | Stage on a remote network | Download a ~150 MB package from the Studio over Tailscale, simulating a remote network with a phone hotspot. | Under ~1 min per package. |
| S6 | Melody and key | Studio | On 5 songs separated in S2, including one that changes key, run pitch tracking (f0) on the lead stem with 2–3 trackers (for example pYIN, torchcrepe, RMVPE). Group the curve into notes, and detect the key per section from the melody plus the instrumental's harmony (chroma). Play the notes back as a synth over the instrumental and compare by ear. Also try a backing stem that holds a single harmony line. | The notes follow the sung melody well enough to score against, key changes are found where they happen, and it adds little time next to separation. This spike picks the tracker for the beta. |

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
| A9 | First night: one shortcut opens REAPER, the Stage and the full-screen browser, plus a setup checklist. | A full karaoke test session at the venue. |

### Phase 2: beta

Proposed order:

1. **Two microphones:** each mic gets a role (lead or backing), and the role decides which original vocal stems are muted.
2. **Lyrics and melody analysis** on the Studio. Together they become `pipeline_version` 2, and the library is reprocessed.
   - Lyrics split into lead and backing, with WhisperX alignment.
   - Melody: pitch tracking on the lead stem gives the expected notes and the key per section. Backing gets the same only when it holds a single harmony line; several voices at once aren't reliable. The tracker comes from S6.
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
| Audio interface | At least 2 mic preamps and a native ASIO driver (for example Behringer UMC202HD/UMC204HD or Focusrite Scarlett 2i2). |
| Speakers | Wired from the interface output. **Never Bluetooth**, which adds 100–200 ms of delay. For larger rooms, a PA with a line input. |
| Stage machine | High-performance power plan, plugged in, validated with LatencyMon. |
| Studio machine | A CUDA-capable NVIDIA GPU. VRAM requirements get confirmed in S2. |

## 8. Risks

| Risk | Mitigation |
|---|---|
| High latency on the Stage machine (DPC or drivers) | Run S1 early. Plan B uses direct monitoring. Use an interface with a native ASIO driver. |
| Studio offline, or no internet at the venue | Preload the library. In the beta, a cloud Studio. |
| YouTube changes and `yt-dlp` breaks | Keep `yt-dlp` up to date (recent versions need a JS runtime such as Deno). Accept local files as a source. |
| Poor lead vs backing separation (duets) | The "no vocals" preset always works. Pick models in S2 and reprocess when better ones appear. |
| Song missing from LRCLIB, or out of sync | Unsynced lyrics as a fallback. In the beta, offset adjustment in the pre-performance and automatic alignment. |
| Melody tracking misled by backing residue in the lead stem, melisma, or rap and spoken parts | Ignore sections with low confidence or no pitch, and don't score them. Pick the tracker in S6. |
| Mics pick up the original backing vocals from the speakers, which skews scoring | Dynamic mics and speakers in front of the singers. Measure it when scoring is built. |
| Feedback with a PA | Dynamic mics, speakers in front of the singers, low cut and a gate in the effects chain. |
| Slow upload from the Studio | FLAC by default. If needed, high-bitrate Opus just for transfer. |

## 9. Open decisions

- [ ] Separation models (from S2)
- [ ] Pitch tracker and key detection method (from S6)
- [ ] Loudness target between songs (LUFS)
- [ ] Hardware recommendations confirmed (from S1 and sound tests)
