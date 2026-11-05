# Karaoke Night: plan

> v0.1 · 2026-11-04 · Living document: update it whenever a decision changes.

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
| Workflow | One PR per milestone. | Small, reviewable changes. |
| License | MIT, to be revisited if the app matures into a product. | Simple and permissive, and every dependency is permissive too. |

## 4. Architecture principles

1. **The Stage works offline.** A karaoke night can't depend on the internet. New songs are a bonus when the Studio is reachable.
2. **The stems add up to the original.** The last stem of each separation step is computed as the remainder:
   - `instrumental = mix − vocals`
   - `backing = vocals − lead`
   - `other = instrumental − (drums + bass + guitar + piano)`

   With every stem on, you hear the exact original recording, with no separation artifacts.
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

1. **Search on the TV.** The Stage lists what's already in the library first (marked "ready"), then YouTube Music results (marked "process, ~2 min").
2. **New song.** The Stage creates a job on the Studio, tracks its progress and downloads the package, with resume and hash checks.
3. **Setup.** The singer picks the active stems (presets *Karaoke*, *Backing only*, *Original*, or custom) and the key, in semitones.
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
| `POST` | `/jobs` | Takes `{source, title, artist, album?, duration_s?}` and returns `{job_id, song_id}`. If the song already exists, it returns the finished package right away. |
| `GET` | `/jobs/{job_id}` | Status (`queued → downloading → separating → lyrics → packaging → done`, or `failed`) and progress. |
| `GET` | `/songs` | Master library listing, used for sync. |
| `GET` | `/songs/{song_id}/manifest` | The package manifest. |
| `GET` | `/songs/{song_id}/files/{path}` | A package file. Supports `Range`, so downloads can resume. |

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
  - `transpose`: shifts the key on every stem **except drums**. Drums have no key, and pitch shifting only degrades them.

**Session template:** "stop playback at end of project" is enabled, and the mic tracks are armed with input monitoring on.

### 5.5 Repository layout

```
karaoke-night/
├─ README.md
├─ LICENSE
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
- [ ] A test set of ~20 songs that reflects what people will actually sing. Include duets with constant harmony, songs heavy on backing vocals, and live recordings that are the best-known version. Keep the list outside the repository.
- [ ] A dynamic microphone and an audio interface connected to the Stage machine.
- [ ] Tailscale installed on the Studio and the Stage, on the same account.

| # | Spike | Where | How | Pass criterion |
|---|---|---|---|---|
| S1 | Latency | Stage + interface | Run LatencyMon. Test REAPER with the interface's native ASIO driver and with ASIO4ALL, at buffers of 64, 128 and 256, with the effects chain on. Measure the real round trip with a loopback cable (output → free input) recording a click. | Around 12 ms or less measured, and comfortable singing with pitch correction on. Otherwise, plan B: the interface's direct monitoring plus reverb/delay only (100% wet) from REAPER. |
| S2 | Separation | Studio | Run 5 test songs through 2–3 model combinations. Measure time and VRAM, listen to lead vs backing, and check that the stems add up to the original. | Acceptable quality at about 3 min per song or less. This spike picks the models. |
| S3 | REAPER | Stage | Lua listener loading 7 stems, with play, stop and mute driven from a Python script. | Loads in under 2 s, without interrupting mic audio during the swap. |
| S4 | Search and lyrics | Any | Run `ytmusicapi` + `yt-dlp` and LRCLIB on the 20 test songs. Don't penalize "live" versions, which in some genres are the best-known recordings. | The right version is in the top 3 for at least 18 of 20. Measure LRCLIB coverage. |
| S5 | Transfer | Stage on a remote network | Download a ~150 MB package from the Studio over Tailscale, simulating a remote network with a phone hotspot. | Under ~1 min per package. |

### Phase 1: alpha

| Milestone | Deliverable | Done when |
|---|---|---|
| A1 | Repository skeleton: uv workspace, Vite, and ruff, pytest and tsc in GitHub Actions. | CI is green on the first PR. |
| A2 | `core`: pydantic model of the manifest, with validation and tests. | Tests pass for valid and invalid manifests. |
| A3 | Studio pipeline CLI: `studio process <url\|file>` produces a complete package. | All 20 test songs are processed, and their stems add up to the original. |
| A4 | Studio API: FastAPI with jobs, library and files, running as a service. | The Stage creates a job and downloads the package over Tailscale. |
| A5 | Stage library: SQLite, resumable downloads with hash checks, and a "preload library" action. | With no internet, the Stage lists and plays preloaded songs. |
| A6 | REAPER bridge: session template, Lua listener, and the Python `Player` (load, play, stop, mute, transpose, position, end of song). | A song plays from start to end driven only by the Stage. |
| A7 | Search: `ytmusicapi` with a `yt-dlp` fallback, showing library matches first. | Same criterion as S4. |
| A8 | TV UI: search → setup (stems + key) → playing with lyrics (with offset adjustment) → end → back to search. | A first-time user picks a song and sings without help. |
| A9 | First night: one shortcut opens REAPER, the Stage and the full-screen browser, plus a setup checklist. | A full karaoke session at the venue. |

Optional in the alpha: a **simple queue on the TV**. While one song plays, the next one is already picked and gets processed and downloaded.

### Phase 2: beta

Proposed order:

1. **Two microphones:** each mic gets a role (lead or backing), and the role decides which original vocal stems are muted.
2. **Lyrics split into lead and backing:** WhisperX alignment on the Studio. This becomes `pipeline_version` 2, and the library is reprocessed.
3. **Phone companion page** (local network, joined with a QR code): live lyrics and adding songs to the queue.
4. **Recordings and portal:** the portal runs on Vercel (Next.js + Blob), with share links.
5. **Post-song scoring:** pitch accuracy, timing and presence.
6. **Suggestions and playlists:**
   - suggestions from a low-cost LLM based on play history;
   - Spotify playlist import (Development Mode requires Premium and allows at most 5 users);
   - a decision between a shareable queue link and a Telegram bot for chat-based requests.
7. **Cloud Studio (plan B):** an on-demand GPU worker with the same API, so sessions don't depend on the Studio machine being on.

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
| Song missing from LRCLIB, or out of sync | Offset adjustment on the TV, saved per song. Unsynced lyrics as a fallback. Automatic alignment in the beta. |
| Feedback with a PA | Dynamic mics, speakers in front of the singers, low cut and a gate in the effects chain. |
| Slow upload from the Studio | FLAC by default. If needed, high-bitrate Opus just for transfer. |

## 9. Open decisions

- [ ] Separation models (from S2)
- [ ] Loudness target between songs (LUFS)
- [ ] Hardware recommendations confirmed (from S1 and sound tests)
