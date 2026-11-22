# Karaoke Night: plan

> v0.5 · 2026-11-14 · Living document: update it whenever a decision changes.

## Status

*Last updated: 2026-11-22*

- **Phase:** 0 (proofs of concept) done. Phase 1 (alpha) in progress.
- **Done:** plan, README, license, agent guidance (`CLAUDE.md`), first review of the plan (decisions recorded in §3, §5 and §6), S1 rounds 1 and 2 (round 2 with a native ASIO interface: 64 samples is the working buffer), S3, S4, S2 and S3 round 2 (all pass), S5 (pass on the Studio side, the venue's downlink decides the time), S6 (pass, RMVPE chosen). Results in §6. Milestone A1 (repository skeleton: uv workspace, Vite, CI); the spike scripts left the repository with it. Milestone A2 (`core` manifest model, §5.2). Milestone A3 (Studio pipeline CLI, results in §6 Phase 1). Milestone A4 (Studio API, results in §6 Phase 1). Milestone A5 (Stage library, §5.6, results in §6 Phase 1). Milestone A6 (REAPER bridge, §5.4, results in §6 Phase 1). Milestone A7 (search, §5.1, `stage search`, results in §6 Phase 1). Milestone A8a (Stage API, §5.8, `stage serve`, results in §6 Phase 1). Milestone A8b (the TV screens on the §5.7 routes in their alpha subset, over the §5.8 API, results in §6 Phase 1). The beta's web screens (§5.7), the target A7 and A8 build toward. The public access design (§5.9): the Studio API runs only during an event, and the public page lives on Vercel, fed by a snapshot the Stage publishes. Milestone A10 (public access, §5.9, results in §6 Phase 1).
- **In progress:** milestone A9 (first night): the night scripts (`scripts/`) and the runbook (`docs/NIGHT.md`) are done and checked live (results in §6 Phase 1); the full test session at the venue is left. Milestone B1, the first of the beta (Phase 2): pause, volumes and reordering.
- **In progress (beta):** milestone B2, the pre-performance (§6 Phase 2): built, its live check left.
- **Next:**
  - the A8 criterion with someone who has never used the app, at the first night (A9);
  - measure a package download on the venue's own network during the first night (A9, `docs/NIGHT.md`);
  - count the public page's edge requests and store commands on the first public night (§8).

## 1. Goal

A self-hosted karaoke app for private use that plays the **original recording** of each song, split into stems: lead vocals, backing vocals, drums, bass, guitar, piano and other. Singers choose which stems stay on, and their live voices go through an effects chain in REAPER.

## 2. Key constraints

- **Latency.** The live voice path (mic → interface → effects → speakers) should feel natural to sing with. About 12 ms round trip is the usual reference, but S1 found singing comfortable from about 11 ms up to about 50 ms with pitch correction on, so comfort decides. Effects in that path must add no latency of their own: no look-ahead compressors, and pitch correction in low-latency mode.
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
| Storage | In the alpha the Stage library is the folder of packages on disk, with no database. SQLite comes with the play history. | No infrastructure to run, and nothing to keep in step with the folders until there is data that is not in them. |
| Microphones | The REAPER template ships with 2 mic tracks from day one. | The beta needs two singers, and this avoids rebuilding the template. |
| Vocal effects | The effects chain is free and controlled by hand in REAPER on the Stage (for example pitch correction, reverb, delay, compression, doubling). The app never picks plugins. In the beta it only provides data that the chain may use (§6, Phase 2). | Effects need room to evolve, and REAPER is the right place to tune them. |
| Melody and key | RMVPE (`rmvpe-onnx`, on the CPU) on the lead stem, grouped into notes; key per section from the notes plus the instrumental's chroma, with Krumhansl-Kessler profiles over 16 s windows and a Viterbi smoothing. Chosen in S6. | Got the octave right where the other trackers jumped up on a strong held note, fewest chromatic artifacts, and about 0.5 s per minute of song. |
| Pitch correction | Alpha: chromatic mode (snap to the nearest semitone), which needs no song data. Beta: per-section key or target notes, from the melody analysis. | A fixed key sounds wrong in songs that change key. |
| Key change | Chosen at setup only. The key stays fixed while the song plays. | Simpler, and enough for singing. |
| Library sync | The Studio is the source of truth. The Stage mirrors the whole Studio library: it downloads what is missing and removes what the Studio no longer lists. Deleting a song means deleting it on the Studio. | Simple, and a song deleted by hand doesn't come back on the next sync. Disk use gets revisited if it becomes a problem. |
| Song identity | A song is identified by its source (the YouTube `video_id`). The same song from different uploads may end up duplicated, and is deleted by hand if needed. | Simple enough for now. |
| Studio availability | The Studio API runs only during an event: started for it by hand or over SSH, with its firewall rule, and stopped after. | Events are scheduled, so a server listening the rest of the time is only exposure and wasted resources. New songs can still be processed during the night. |
| Public access | During an event, anyone with the link opens `/karaoke` on Vercel to add songs to the playlist, remove any song not playing and follow the lyrics. The Stage stays the owner of the state: it publishes a snapshot to a store on Vercel and reads the requests the public leaves there (§5.9). Nothing from the internet reaches the Stage, and no code is needed in the link. | The Stage keeps working offline, the number of phones does not load the machine playing the audio, and a malformed request is only something the Stage discards. |
| Web screens | Three routes around one shared playlist: `/karaoke` (home), `/karaoke/stage` (setup and the song playing) and `/karaoke/stage/results` (§5.7). This is the beta's target; the alpha screens use the same routes and grow into it. A product version would be redesigned. | The beta's flow is known now, so the alpha screens don't have to be rewritten for it. |
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
4. **The player sits behind an interface.** Stage code talks to a `Player` interface (`load`, `play`, `stop`, `set_mute`, `transpose`, `position`, `ended`), and REAPER is just one implementation of it (`ReaperPlayer`, in `packages/stage/src/stage/player.py`). This leaves room to replace REAPER later.
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
  ├─ local library: package folders
  ├─ session: state machine
  ├─ public link: publishes the snapshot, reads the requests ──► PUBLIC (below)
  └─ REAPER bridge ── HTTP + ExtState ──► REAPER (fixed session)
                                           ├─ Lua listener (load song, transpose)
                                           ├─ Mic 1, Mic 2 with effects chains
                                           └─ Lead · Backing · Drums · Bass · Guitar · Piano · Other
                                                 │
                                                 ▼
                                    audio interface (ASIO) → speakers / PA

PUBLIC · Vercel, during an event (§5.9)
  store (Redis): snapshot (Stage writes) · requests (public writes, Stage reads)
  functions ◄── HTTPS ──► phones on /karaoke (the event's link)
```

Every connection to Vercel starts at the Stage or at a phone: nothing on the internet connects to the Stage or the Studio.

### 5.1 Song flow

1. **Search on the TV.** The Stage lists what's already in the library first (marked "ready"), then YouTube Music results (marked "process, ~2 min"). A library screen also lists every ready song.
   - **Library matches** need no network: a song matches when every word of the query appears in its title or artist, even as part of a longer word, with case and accents ignored (Unicode NFKD without combining marks, then casefold).
   - **Online results:** up to 10 from `ytmusicapi` (`filter="songs"`, which returns about 20 whatever its limit says, so the Stage keeps the first 10). If it fails or finds nothing, a flat `yt-dlp` search (`ytsearch10:`) takes its place: it lists hits without resolving each video, so it needs no JS runtime, and it has no album and no separate artist, so the channel stands in for the artist.
   - **No duplicates:** an online result whose `video_id` is already in the library (manifest `source.video_id`) shows once, as the library song, after the songs the words matched, so a typo that misses the library still finds it.
   - **Without internet** the library matches come back with a notice that the online search failed, never an error.
   - **Each result** carries the full title, the artist (all of them, joined with ", "), the album, the duration, the largest thumbnail the source gave, the `video_id` (what `POST /jobs` needs, §5.3) and, for a library song, its `song_id`. Missing values stay empty. Library songs have no thumbnail yet (§9, thumbnails offline). `stage search "<text>"` prints them, one line each.
2. **New song.** The Stage creates a job on the Studio, tracks its progress and downloads the package, with resume and hash checks.
3. **Setup.** The singer picks the active stems (presets *Karaoke*, *Backing only*, *Original*, or custom) and the key, in semitones. The key can't change once the song starts.
4. **Start.** The Stage tells REAPER to load the package and play.
5. **During the song.** Lyrics follow REAPER's playback position, and stems can be toggled live.
6. **End.** REAPER stops at the end of the project, and the screen goes to the results page, which leads to the next song in the playlist.

The screens that carry this flow are in §5.7.

### 5.2 Song package

```
library/<song_id>/
  manifest.json
  stems/lead.flac  backing.flac  drums.flac  bass.flac  guitar.flac  piano.flac  other.flac
  lyrics.lrc
```

`manifest.json` (schema 1). The pydantic model lives in `packages/core/src/core/manifest.py`:

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

- **`gain_db`:** the gain REAPER applies on playback. Today it holds the headroom compensation from S2 (see Clipping below). Whether it also brings every song to one loudness target is still open, and so is that target in LUFS.
- **`source`:** where the audio came from, by `type`. `{"type": "youtube_music", "video_id": "…"}` for a YouTube Music video, and `{"type": "file", "sha256": "…"}` for a local audio file, with the sha256 of the file as given (before any conversion). Any other `type` is rejected.
- **`lyrics`:** optional. It is absent when LRCLIB has no lyrics for the song or the song is instrumental.
- **Unknown fields are ignored**, not rejected, so an older Stage keeps reading a manifest that a newer `pipeline_version` wrote (for example one that adds melody data). They are also dropped on write: a Stage that loads and then writes a newer manifest loses those fields. Note for A5: the Stage library must account for this, for example by keeping the downloaded `manifest.json` as is.
- **Paths stay inside the package:** every `file` (stems and lyrics) is relative to the package directory. Empty, absolute (POSIX or Windows), drive-relative, UNC and `..` paths are rejected.
- **`sha256`:** exactly 64 lowercase hex characters, the hex digest of the file.
- **No required stem set:** the model accepts any stem names and any number of stems.
- **`gain_db` and `duration_s`** must be finite numbers: NaN and infinity are rejected.
- **`created_at`** must carry a time zone; a date without one is rejected. It is stored in UTC with whole seconds, so it is always written as `YYYY-MM-DDTHH:MM:SSZ`, as S2 wrote it: another offset is converted to UTC and fractional seconds are dropped.
- **Clipping:** if any stem would exceed 0 dBFS when written to FLAC, all stems get the same reduction, compensated in `gain_db`. That way the stems still add up to the original.

### 5.3 Studio API

FastAPI, started with `studio serve` (`--host` defaults to `0.0.0.0`, `--port` to `8790`, `--library` and `--model-dir` as in `studio process`). It refuses to start without the token in the `STUDIO_TOKEN` environment variable, or without the `separation` extra. Every route requires `Authorization: Bearer <token>`; a missing or wrong token gets 401. The request and response models live in `packages/core/src/core/studio_api.py`, so the Stage client shares them.

| Method | Route | Description |
|---|---|---|
| `POST` | `/jobs` | Takes `{source: {type: "youtube_music", video_id}, title, artist, album?, duration_s?}` and returns `{job_id, song_id}`. Only YouTube Music sources are accepted; local files stay with `studio process`. The `video_id` must be 11 characters of `A-Z`, `a-z`, `0-9`, `_` and `-`, the rule `studio process` uses, or the request gets 422 at once; packages already in the library are not checked. `song_id` comes from the given title and artist plus the `video_id`, as the CLI computes it, and the Studio fetches no metadata of its own. Without `duration_s`, the lyrics lookup uses the audio length. |
| `GET` | `/jobs/{job_id}` | `{job_id, song_id, status, step, attempt, retry_at, error}`. 404 for an unknown job. |
| `GET` | `/songs` | Master library listing, one `{id, title, artist, duration_s, pipeline_version, created_at}` per package. The Stage mirrors it: it downloads what is missing and removes what is no longer listed. |
| `GET` | `/songs/{song_id}/manifest` | The `manifest.json` bytes as stored on disk, not written back through the model (which would drop unknown fields, §5.2). |
| `GET` | `/songs/{song_id}/files/{path}` | A file the manifest lists (a stem or the lyrics); any other path, or a listed file missing on disk, is 404. Supports `Range`, so downloads can resume. |
| `DELETE` | `/songs/{song_id}` | Deletes a song from the master library: 204, or 404 for an unknown song. The Stage removes it on the next sync. |
| `DELETE` | `/songs` | Deletes the whole master library and returns `{deleted: [...], busy: [...]}`. |

- **Statuses:** `queued → downloading → separating → lyrics → packaging → done`, or `failed`. `step` is set only while `separating`: `vocals`, `karaoke` or `instruments`. `attempt` counts the runs started so far (0 while a new job waits its turn).
- **Queue:** in memory, one job at a time, since the GPU takes one song at a time. Jobs are lost when the Studio restarts; the library stays the source of truth.
- **Retries:** only a failure while downloading is tried again, up to 3 times, after 2, 10 and 30 minutes. While it waits, the job is `queued` with `retry_at` set and `error` holding the last error, until the next attempt starts and clears both, and the jobs behind it run in the meantime. After the last retry fails, or after a failure in any later stage, the job is `failed`.
- **One job per video:** if a package from the same `video_id` is already in the library, `POST /jobs` returns a job that is already `done`, with that package's id, whatever title it was given. If a job for that `video_id` is queued, waiting for a retry or running, it returns that job. A failed job does not count.
- **Only library ids:** routes that take a `song_id` act only on the ids of the library listing, never on a path built from the URL, so nothing outside the library can be reached. Folders starting with `.` (builds and deletions in flight), folders without a `manifest.json` and folders whose `manifest.json` the model rejects (logged as a warning, once until the file changes) are not listed, so one broken package does not take the rest of the library down.
- **Deleting:** the package folder is first renamed to a name starting with `.`, then removed. On Windows a folder holding an open file cannot be renamed, so `DELETE /songs/{song_id}` answers 409 and the package stays whole; `DELETE /songs` lists it under `busy`.
- **Leftovers:** a server stopped mid job leaves its build folder (`.<song_id>.<random>.tmp`), and a removal that could not finish leaves its trash folder (`.<song_id>.<random>.deleting`), where `<random>` is exactly 8 characters of `a-z`, `0-9` and `_`. `studio serve` removes the folders of exactly those two shapes from the library when it starts, before it binds the port, and logs each one it removes or could not fully remove; nothing else is touched. Accepted cost: it cannot tell a leftover from a build still running, so two `studio serve`, or a `studio serve` and a `studio process`, must never run on the same library at once. A second `studio serve` removes the first one's build in flight even when it then fails to start because the port is in use.

### 5.4 Stage ↔ REAPER

**REAPER's built-in web interface** (`127.0.0.1:8080`):

| Command | Purpose |
|---|---|
| `/_/TRANSPORT` | Playback state (stopped or playing) and position in seconds. The Stage polls it every 100–250 ms. |
| `/_/TRACK` | Track list. On startup, the Stage builds a name → index map, so no indexes are hard-coded. |
| `/_/SET/TRACK/{i}/MUTE/{0\|1}` | Turns a stem on or off. |
| `/_/1007`, `/_/1016`, `/_/40042` | Play, stop, and go to the start of the project. |
| `/_/1008` | Pause, a toggle: sent only after `TRANSPORT` shows the state to leave (B1, §5.8). |
| `/_/SET/TRACK/{i}/VOL/{gain}` | A track's volume, as an absolute gain (B1, §5.8); `TRACK` gives it back in its 5th field. |
| `/_/SET/POS/{seconds}` | Moves playback to a position, for the excerpt of the setup (B2, §5.8). `main.js`: "Sets edit cursor position (seeking playback) to value_in_seconds"; it says nothing of a sign or an exponent, so the Stage writes a plain decimal with 3 places (`SET/POS/9.500`). |

`play` sends `40042` then `1007`, so a song always plays from its start; the excerpt sends `SET/POS` right after `play` for that reason. `load` sends `1016` before the listener command, so stems never land under a moving cursor with the previous song's mutes and key. The Stage builds the name → index map from `TRACK` when it connects and again after each `load` (which can create tracks), lowercase as the listener matches names, skipping the master (index 0); of two tracks with one name, the first wins.

**Lua listener** (`reaper/scripts/karaoke_listener.lua`), for anything the web interface doesn't cover. It runs in a `defer` loop, started when REAPER opens by one line in `Scripts/__startup.lua` of REAPER's resource folder: `dofile([[<clone>\reaper\scripts\karaoke_listener.lua]])`. It finds its own folder (for `lib/json.lua`) from the source of its own chunk (`debug.getinfo(1, "S").source`), which names the listener file both under `dofile` and when it runs as an action; the action context would name `__startup.lua`. Setup steps: `reaper/README.md`.

- **Request:** the Stage sets `SET/EXTSTATE/karaoke/cmd/<URL-encoded JSON>`, for example `{"id": 42, "op": "load", "manifest": "C:\\...\\manifest.json"}`.
- **Reply:** the listener runs the command and writes `{"id": 42, "op": "load", "ok": true, "ms": 12.3}` (or `"ok": false, "error": "…"`) to `karaoke/ack`. The Stage reads it with `GET/EXTSTATE/karaoke/ack` and matches the `id`; an ack with another id (the previous command's) is skipped. `GET/EXTSTATE` escapes newlines, tabs and backslashes as `\n`, `\t` and `\\`, decoded in one pass. A command the listener cannot decode gets `"ok": false` and an error, with its `id` when one can still be read, and the listener keeps running.
- **One command at a time:** `cmd` and `ack` are single slots, so the Stage sends the next command only after the previous ack. With no matching ack within 5 s, the Stage reports that the listener did not answer and says to restart it (S3 round 2 saw it stop answering once).
- **Operations:** `load`, `transpose`, `ping` (an empty round trip) and `quit` (stops the listener).
  - `load`: removes the previous song's items, inserts each stem on the track of the same name (creating the missing ones at the end, with input monitoring off) and applies `gain_db`. The project end follows the stems, as long as nothing else on the timeline runs longer (an item left on a mic track would).
  - `transpose`: shifts the key on every stem **except drums**. Drums have no key, and pitch shifting only degrades them. It is sent at setup, before playback starts.

**Session template** (`reaper/template/karaoke.RPP`, hand-written; REAPER fills in the rest with its defaults): project sample rate 44.1 kHz; `Mic 1` and `Mic 2` on mono inputs 1 and 2, armed, with input monitoring on; then the folder track `Music`, its fader at -8 dB, holding `Lead`, `Backing`, `Drums`, `Bass`, `Guitar`, `Piano` and `Other`, not armed, with input monitoring off. The stems are normalized close to full scale (§5.2, `gain_db`), so without that headroom the voice on top clips the master; the -8 dB is a starting point until a loudness target is chosen (§9). The listener finds the stem tracks by name, inside the folder or not; a stem track it has to create goes at the end of the track list, outside the folder and without its -8 dB, so a session must keep all 7 (a track created with REAPER's defaults inherits the user's default, which can be on, so the listener turns it off on the stem tracks it creates). No effects anywhere: the effects chain on the mic tracks is left to the person running REAPER (§3), and its guide comes from a guided test. "Stop/repeat playback at end of project" is a REAPER preference, not part of the project, so it is in the setup checklist of `reaper/README.md`.

**End of song**, to be confirmed live: the song has ended when playback goes from not stopped (playing, paused, recording or record paused) to stopped (playstate 0) without the Stage asking to stop. A stop made by hand in REAPER counts as the end too, paused or not; a pause does not, so `stage play` keeps following the song and goes on when playback resumes. How REAPER reports the stop at the end of the project (the position and playstate right at the stop) is not measured yet, so the manifest's `duration_s` plays no part in the rule, which lives in one function (`song_ended` in `player.py`).

**`stage play <song_id>`** (`--library DIR`, `--mute STEM ...`, `--transpose N`) loads a song of the library, mutes the stems given and unmutes every other one (so the previous song's mutes do not stay), sends the transpose (0 by default), plays from the start and prints the position once per second of the song, one line each, until the song ends (exit 0). Ctrl+C stops playback and exits 130. An unknown song id, an unreachable REAPER, a listener that does not answer and a failed command exit 1; an unknown stem in `--mute` is a usage error (exit 2) that lists the song's stems.

### 5.5 Repository layout

```
karaoke-night/
├─ README.md
├─ LICENSE
├─ CLAUDE.md                    # guidance for AI coding agents
├─ docs/
│  ├─ PLAN.md                   # this file
│  └─ NIGHT.md                  # runbook of a karaoke night
├─ pyproject.toml               # uv workspace
├─ packages/
│  ├─ core/                     # shared models (manifest, contracts), lightweight
│  ├─ studio/                   # Studio: jobs, download, separation, lyrics (torch, audio-separator)
│  └─ stage/                    # Stage: local server, library, REAPER bridge, session
├─ web/                         # React + Vite + TS: TV screens, and the public phone page (§5.9)
│  ├─ api/public/               # Vercel functions of the public page
│  └─ server/                   # what the functions share: the store, the answers
├─ reaper/
│  ├─ README.md                 # setup checklist, effects chain guide
│  ├─ scripts/                  # karaoke_listener.lua, lib/json.lua
│  └─ template/                 # base session (karaoke.RPP)
├─ scripts/                     # night scripts (PowerShell): stage-night, studio-start, studio-stop
└─ .github/workflows/ci.yml     # ruff + pytest + tsc
```

`core` is kept separate so the Stage never has to install torch.

### 5.6 Stage library

The folder is the library: one package per folder, named by its song id, as in §5.2, with `manifest.json` kept byte for byte as the Studio sent it (§5.2, unknown fields). There is no database in the alpha. `stage list` lists it with no network; `stage sync --studio URL` mirrors the Studio library (§3, Library sync), with the token in the `STUDIO_TOKEN` environment variable. Folders starting with `.` are never listed.

- **What a sync does:** a song the Studio lists and the Stage lacks is downloaded; one the Stage holds with a different `created_at` or `pipeline_version` is downloaded again (a reprocessed song keeps its id and gets a new `created_at`); one the Stage holds and the Studio no longer lists is removed. There is no guard on an empty or shrunk listing: an empty listing empties the Stage.
- **Downloads** go into `<library>/.<song_id>.partial/`, one file after the other over one kept-alive connection, and survive between runs. The downloaded manifest is kept there, and a later sync resumes from it while it still matches the listing (`created_at` and `pipeline_version`); otherwise the partial is discarded and the song starts over. A file already partly on disk is resumed with `Range: bytes=<size>-`: a 206 is appended, a 200 rewrites the file from zero, and a 416 (nothing left to send) asks for the whole file again.
- **Hash checks:** each stem is checked against its manifest `sha256` after its download, and a stem already whole in the partial is not fetched again. A stem that does not match is deleted, the song is not installed in that sync and is reported as failed, and the next sync downloads that stem again. Lyrics have no hash in the manifest, so they are downloaded without a check.
- **Install:** a new song is renamed from its partial to `<song_id>`. An update renames the old folder to a trash name, renames the new one in, then removes the old one, so the old version is replaced only once the new one is complete. If the new one cannot be renamed in, the old one is renamed back and the song is reported as failed; the complete partial is kept, so a later sync installs it. If the old one cannot be renamed back either, the song is missing until the next sync, which removes the trash folder and installs the partial.
- **Busy:** on Windows a folder holding an open file cannot be renamed, and REAPER keeps the stems of the loaded song open. A song that cannot be removed stays whole; on an update the old version stays in place and the complete partial is kept, so a later sync installs it. Both are reported as busy.
- **Song ids:** an id from the Studio becomes a folder name the Stage creates and deletes, so only the shape the Studio generates is accepted: slugs of `a-z`, `0-9` and `-` joined by `--`, then 6 hex digits (`song-title--artist-name--a1b2c3`). A song listed under any other id is skipped with a warning, and nothing is written or deleted under that id, not even a local folder of the same name.
- **Unreachable Studio:** if the listing fails (no connection, or an error answer such as 401), nothing in the library changes and `stage sync` exits with 1. A failure on one song (a file that fails to download or to match) does not stop the others, and the sync exits with 1 if any song failed.
- **Leftovers:** a sync removes the partials of songs the Studio no longer lists and its own trash folders (`.<song_id>.<8 hex digits>.deleting`) that a removal could not finish. No other folder is touched.
- **Output:** one line per song acted on (`added`, `updated`, `removed`, `busy` or `failed`, with the reason for the last two), then a line with the counts, the megabytes downloaded and the seconds taken.

### 5.7 Web screens (beta target)

The beta's screens, as the maintainer laid them out on 2026-11-14. A8b built them in their alpha subset: no preview of the original, no volumes, no pause, no reordering, no results report, and no stems toggled while the song plays; the key is limited to -6 to +6 semitones by the screen, not the API, and the screens speak Brazilian Portuguese. A7 and A8 use the same routes, data and session model, so the beta adds to the alpha screens instead of replacing them. A product version beyond the beta would be redesigned.

Every screen shares one **playlist**: the songs lined up for the night, in order, the first one being the current song. It lives in the Stage's memory while the service runs; a restart starts with an empty playlist. An entry not yet in the Stage library shows its processing state (the Studio job of §5.3, then the Stage's download).

| Route | Screen |
|---|---|
| `/karaoke` | Home: playlist, search and library. |
| `/karaoke/stage` | Stage mode: the song's setup, then the song playing. |
| `/karaoke/stage/results` | End of a performance. |

**Home (`/karaoke`)**

- With a playlist: the song playing, or the current one, with a prominent button to go to Stage mode. The upcoming songs sit in a side panel on the right, with reorder, remove and the processing state of each. While no song plays (the setup), the current song is listed there too, so a song that failed can be taken off.
- Search on YouTube: the user types a text and gets the first results, about 10. Each shows the full title, the thumbnail, a button to hear a random excerpt of the original recording (not processed) and a button to add it to the playlist.
- Library: the songs already downloaded, with the same options as the search results.

**Stage mode (`/karaoke/stage`), before the song starts**

- A progress bar while the current song is not ready on the Stage yet.
- The song's setup: music volume against mic volume, stems to mute (lead muted by default), and key up or down. Pitch correction stays in REAPER, by hand (§3).
- The excerpt (B2): a button plays about 20 s of the song in REAPER, from 3 s before the first synced lyric line (from the start without synced lyrics), with the setup's mutes and key, so the singer tries them and the volumes by ear before singing. While it plays, its lyrics follow it beside the settings, and a change of key or stems is heard at once. The button then stops it; once it ended, it offers to hear it again. It ends alone and stays in the setup.
- A prominent button to start the song.
- The playlist in a bar at the bottom, this song included, with reorder, remove, the processing state of each and a skip to the next song.

**Stage mode, while the song plays**

- The lyrics in real time fill the main area, line by line as the song goes.
- A bottom bar with pause, skip, and the music and voice volumes.

**Results (`/karaoke/stage/results`)**

- A report on the performance; what it shows is still to be defined (scoring is beta item 7).
- A bottom bar with the next song in the playlist and two buttons: play it at once keeping the current song's settings, or go to the setup of Stage mode first.

**What the screens need from the Stage**, beyond what the alpha has:

- `Player` gains `pause` (REAPER action 1008) and track volume (`/_/SET/TRACK/{i}/VOL/{v}`, on the `Music` folder for the music and on the mic tracks for the voice). Neither is tried live yet. Skip is a stop the Stage asks for, so it is not an end of song (§5.4), followed by the next `load`. `play()` goes to the start before it plays, so resuming after a pause must not call it: REAPER's pause action toggles, and A6 saw a second press resume playback.
- Fetching one song on demand (a Studio job, then the download of that one package), next to the full mirror of `stage sync`. Built in A8a (§5.8).
- Search results that carry the thumbnail and the `video_id` the job needs.
- Still open (§9): the preview of the original and thumbnails offline.

### 5.8 Stage API

FastAPI, started with `stage serve` (`--port` defaults to `8791`, `--library` as in the other `stage` commands, `--studio URL` with the token in the `STUDIO_TOKEN` environment variable, `--web DIR` defaulting to `web/dist` in the current folder). It listens on `127.0.0.1` only, since in the alpha the TV mirrors the Stage's own screen, and asks for no token. Without `--studio` or the token it still serves: songs of the library play, and a new song fails with `no_studio`. The routes live under `/api` and answer JSON; FastAPI's own docs are at `/api/docs`. A `GET` of any other path outside `/api` serves the built web: a file of it when one matches, else `index.html`, so a client route such as `/karaoke/stage` survives a reload. Without a built web (no `index.html` in `--web`) only the API is served.

A refusal answers `{error, detail}`: `error` is a stable code the screens put into words (in Portuguese, A8b), `detail` an English line for the logs. Every error has that shape, the routing's own too: a body or query the routes cannot read is `invalid_request` (422), a path no route serves `not_found` (404), and a route asked with a method it does not take `method_not_allowed` (405).

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/state` | `{session, playlist}`, which the screens poll about every 250 ms. `session` is `{state, entry_id, muted, key, position_s, duration_s, error, detail, paused, volumes, previewing, stems}`: `entry_id` is the first entry in setup and the song played afterwards, `duration_s` that entry's duration (the manifest's once it plays), `position_s` null in setup but during an excerpt (`previewing`, B2), `stems` the stem names of that entry's manifest in its order (what the setup offers to mute), filled in setup only: empty while playing and in the results, and while the package is not in the library or its manifest is invalid. Each poll reads that one manifest, never the whole library. `playlist` holds the entries in order. |
| `GET` | `/api/search?q=<text>` | `stage search` (§5.1): `{results, notice, detail}`, each result `{title, artist, origin, video_id, album, duration_s, thumbnail_url, song_id}`. `notice` is `offline` when the online search failed, with the English notice in `detail`. |
| `GET` | `/api/library` | `{songs}`: every song of the library, as a search result. |
| `GET` | `/api/library/{song_id}/lyrics` | `{status, lines}`, `status` being `synced`, `unsynced` or `none`, each line `{time_s, text}` (`time_s` null for unsynced lyrics). 404 `unknown_song`. |
| `POST` | `/api/playlist` | Takes a search or library result and appends it: 201 with the entry. A result whose `song_id` is in the library is `ready` at once, with the fields of its manifest; any other one needs a `video_id` a Studio job accepts (§5.3), or gets 422 `not_addable`. |
| `DELETE` | `/api/playlist/{entry_id}` | 204, 404 `unknown_entry`, or 409 `entry_playing` for the song playing. |
| `PUT` | `/api/session/settings` | `{muted, key}`: the stems to mute and the key in semitones. Setup only. |
| `POST` | `/api/session/start` | Plays the first entry with the settings. Setup only. |
| `POST` | `/api/session/stop` | Stops the song playing and shows the results. |
| `POST` | `/api/session/play_next` | From the results: removes the song played and plays the next one at once. |
| `POST` | `/api/session/setup_next` | From the results: removes the song played and goes to the setup of the next one. |
| `POST` | `/api/session/skip` | From the setup: removes the current song and sets up the next one. |
| `POST` | `/api/session/pause` | While playing and not paused: pauses REAPER where the song is (B1). |
| `POST` | `/api/session/resume` | While paused: plays on from where it paused, never from the start (B1). |
| `PUT` | `/api/session/volumes` | `{music_db, voice_db}`, in any state: the music's volume (the `Music` folder track) and the voices' (`Mic 1` and `Mic 2`, set together), in dB (B1). |
| `POST` | `/api/playlist/{entry_id}/move` | `{by}`, `-1` (one place up) or `1` (one place down): 200 with the state, 404 `unknown_entry`, or 409 `entry_current` for the session's own entry (B1). |
| `POST` | `/api/session/preview` | From the setup: plays the excerpt of the first entry with the settings, again from its start when one plays already (B2). |
| `POST` | `/api/session/preview/stop` | From the setup: stops the excerpt, staying in the setup; without one it does nothing (B2). |

The session routes answer the whole state, as `GET /api/state` does.

- **Entries:** `{id, state, step, progress, error, detail, song}`, `song` being the result it was added from, with the `song_id` the Studio gives once the job exists. The states: `queued` (waiting for the Studio, also while a download retry waits, with its last error in `detail`), `starting` (the job is `queued` with `attempt` 1 or more and no `retry_at`, the Studio importing its models, A4), `downloading`, `separating` (with `step`), `lyrics`, `packaging`, `fetching` (the download to the Stage, with `progress` `{files_done, files_total, bytes_done}`; the manifest has no file sizes, so the whole is counted in files, the stems and the lyrics), `ready`, and `failed` with `error` `no_studio`, `studio_error`, `job_failed`, `fetch_failed` or `internal_error` and the reason in `detail`. An entry that is not `failed` may carry the warning `error` `studio_unreachable`, with the reason in `detail`, while the worker waits to try again (below); the next step that works clears it.
- **Worker:** one thread polls the job of each entry every 2 s (at once after an add) and fetches the package of a `done` job: the download and install of one song from `stage sync` (§5.6: partial folder, resume, hash checks, the old version replaced only by a complete one). The other entries wait while it fetches. A song the library holds already in the listed version is ready without a download, and an update a loaded song blocks (busy) leaves the old version, which plays. A 404 on `GET /jobs/{id}`, what the Studio answers for a job it lost in a restart (§5.3), submits the job again: the Studio answers with a done job when the package exists, or starts it over. A Studio that does not answer (network down, Studio stopped, a timeout), while the worker submits, follows or fetches, keeps the entry in its state with the warning `studio_unreachable`, and the worker tries again on its next cycle; a fetch cut that way resumes from its partial folder. The entry fails only on an answer: the job `failed` (`job_failed`), an error answer such as 401 or 500 or an answer the Stage cannot read (`studio_error`), or a package that does not install, such as a stem that does not match its hash or an invalid manifest (`fetch_failed`). Adding a failed song again picks up the job the Studio may still be running, since it returns the active job of a video (§5.3). The worker checks an entry is still in the playlist before each step: a removed or skipped entry gets no job, and its download stops at the next chunk, leaving the partial folder for a later fetch or `stage sync`.
- **Session:** `setup → playing → results`. A setup starts with the lead muted and the key at 0. `start` loads the song, sets every stem of the manifest (muted or not, so the previous song's mutes do not stay), sends the key and plays; it is refused with `no_song` (empty playlist), `not_ready` (the entry is not ready, or its package left the library) or `wrong_state`. While the song plays, a thread reads the position about every 250 ms, and the song ends by the §5.4 rule (REAPER stopped without the Stage asking). `stop` is a stop the Stage asks for, so it is not an end by that rule: the session itself moves to the results. From the results, `play_next` keeps the muted stems and puts the key back at 0; it is refused with `no_song` or `not_ready`, changing nothing, when the next entry is missing or not ready. `setup_next` and `skip` go back to the defaults.
- **REAPER errors** answer 502: `reaper_unreachable` (its web interface does not answer), `listener_silent` (no ack within 5 s; restart the listener, §5.4) or `reaper_failed` (anything else, such as a missing stem track). A failed `start` stays in setup; a failed `stop` still moves to the results; a failed `play_next` has already removed the song played, so it leaves the setup of the next song, with the mutes kept and the key at 0; a failed position read keeps playing. The error also stays in `session.error` and `session.detail` until the next action or position read that works.
- **One REAPER command at a time:** the listener's `cmd` and `ack` are single slots (§5.4), so every `Player` call, from an action or from the position thread, goes through one lock.
- **Lyrics:** LRC lines `[mm:ss.xx] text` with 0 to 3 digits of fraction, in time order; a line with several stamps comes once per stamp, CRLF and a byte order mark are accepted, and tags such as `[ar:...]` are skipped. The manifest's `lyrics.offset_ms` is added to every time, so a positive offset shows the lines later. Unsynced lyrics come one line per line of text.
- **One library, one writer:** the worker writes into the library as `stage sync` does, so `stage serve` and `stage sync` must not run on the same library at once. Nothing enforces it.
- **Not in A8a** (beta, §5.7): the preview of the original, volumes, pause, reordering, the results report and stems toggled while the song plays. B1 adds pause, volumes and reordering (below).
- **Pause (B1):** the session stays `playing` and carries `paused` (true or false), which follows REAPER: every position read takes it from the playstate (2 or 6 paused, 1 or 5 not), so a pause or a resume made in REAPER by hand shows too, within one read. `pause` uses REAPER's pause action (1008, which toggles), `resume` plays on from the paused position: `play()` goes to the start (§5.4), so resuming never calls it. A pause is not an end of song (§5.4); the position thread keeps reading, and the position stays where it paused. Since 1008 toggles, the Stage reads REAPER's playstate first: `pause` sends it only while playback moves (playing or recording) and `resume` only while paused, so a pause never resumes and a resume never pauses; neither sends it while REAPER is stopped, since what 1008 does then is not verified (REAPER's `main.js` does not describe it; its own `index.html` sends it from the pause button). `pause` then reads REAPER at once, so `paused` and the position where REAPER paused change together; when that read fails, the error shows and the next read sets both. `stop` while paused moves to the results as it does while playing; to skip a song that plays, the screen's stop button is the skip (the maintainer's choice: it goes to the results). `pause` outside playing, or while paused, and `resume` while not paused are refused with `wrong_state`.
- **Volumes (B1):** `session.volumes` is `{music_db, voice_db}`, read from REAPER's track list when the Stage connects (the `Music` folder track and `Mic 1`), or null until then. They belong to the night, not to a song: a `load` does not touch track volumes, so they stay from one song to the next until changed. The Stage sets them with `SET/TRACK/{i}/VOL/{gain}`, the absolute gain (1 is 0 dB) computed from dB; the voice sets `Mic 1` and `Mic 2` to the same value. As with the key, the API takes any number and the screen keeps the range (music and voice from -30 to +6 dB); REAPER errors answer 502 as other REAPER calls. Filled in by B1a, where the contract left a gap: "when the Stage connects" is when `stage serve` starts and, while the volumes are still unknown (REAPER may still be opening then, since the night script starts both at once), after each song starts; a failed read leaves them null and is not a session error. The gain goes out as a plain decimal with 6 places (-8 dB is `SET/TRACK/3/VOL/0.398107`), never with a sign (relative, in dB) or an exponent, and without REAPER's `e` or `g` suffixes (`main.js`: an `e` ends the touch state, and touch states auto-expire in 8 s; a `g` ignores ganging). What the touch state changes in a session without automation is not verified. A volume is read as a gain from the 5th field of `TRACK` and given in dB rounded to hundredths, as REAPER shows it; below about -150 dB, which REAPER shows as -inf, it is -150. Nothing guards the number (the maintainer's choice): `Infinity` and `NaN`, which Python's JSON reader takes, go out as `VOL/inf` and `VOL/nan` (what REAPER makes of them is not verified) and show as a null volume, and a value too large for a gain answers 500 without sending anything. The volumes shown change only once all three tracks took the new value; on a REAPER error they stay as they were.
- **Reordering (B1):** `move` shifts an entry one place up or down among the entries behind the session's own entry (the first entry in setup, the song played in playing and results), which never moves: moving it is `entry_current`, and moving an entry up from right behind it, or down from the end, changes nothing and answers 200. A move by place, not to an index, so moves sent at once from several phones never fight over a position. `by` must be the integer -1 or 1 (not `true`, `1.0` or `"1"`), or the move is `invalid_request`; an unknown entry is checked before `entry_current`. A move holds the session's lock, as a remove does, so the session's own entry cannot change under it.
- **Excerpt (B2):** a state inside setup, as a pause is inside playing: the session stays `setup` and carries `previewing`, and while it is true `position_s` follows the excerpt (it is null otherwise in setup). `preview` is refused as `start` is (`no_song`, `not_ready`, `wrong_state`); it loads the song, sets every stem's mute, sends the key, plays and seeks (§5.4) to 3 s before the first synced line with text (a stamp without text is not one), or to 0 without synced lyrics or when the lyrics file cannot be read (a warning in the log), and reads the volumes while they are unknown, as `start` does. Its own position thread, under the same lock, stops REAPER once the position reaches 20 s past that start, or ends the excerpt when REAPER stopped on its own (a song shorter than the excerpt); either way the session stays in the setup, with `previewing` false and `position_s` null. An excerpt never moves to the results: the §5.4 end of song rule is read only while `playing`. `preview` during an excerpt plays it again from its start. `preview/stop` in the setup without an excerpt does nothing and answers as a stop does, since a click can land just after the excerpt ended on its own; outside the setup it is `wrong_state`. While it plays, `settings` also sends every stem's mute and the key to REAPER at once; on a REAPER error the settings are kept all the same, since `start` sends them again. `start` during an excerpt plays the song from 0 as always (its `load` stops the excerpt). `skip`, and removing the setup's own entry (from the TV or a phone), stop the excerpt first; a REAPER error then ends the excerpt and refuses the action, which works when tried again. A failed `preview` stays in the setup without an excerpt, and a failed `preview/stop` still ends it. The public snapshot carries no anchor and no lyrics during an excerpt (§5.9: both only while `playing`).

### 5.9 Public access (A10)

During an event, the public reaches the karaoke through a link to `/karaoke` on Vercel. The Stage keeps owning the playlist and the session (§5.8); Vercel only relays between it and the phones.

- **What the public can do:** see the playlist and the song playing, search (the library and online, §5.1), add a song, remove any entry except the one playing (the rule of `DELETE /api/playlist`, §5.8), move an entry one place up or down (B1, the rule of `move`, §5.8), and follow the lyrics of the song playing. Setup, start, stop, skip and the results stay on the Stage's own screens (`/karaoke/stage`), which are never published. The link carries no code: the page answers only while the Stage publishes, that is, during an event.
- **Store:** a Redis database on Vercel (Upstash, from Vercel's marketplace) holds two things:
  - the **snapshot**, written only by the Stage: the playlist, the session (its state, the entry playing and the anchor below), the lyrics of the song playing, the library listing (written again only when it changes) and the time it was published;
  - the **requests**, written by the public functions: add, remove, move (B1) and search, each with an id.
- **Stage side:** `stage serve` publishes the snapshot on every change and every few seconds, and reads the requests about once a second. It applies each one as its own API would (an add is checked as `POST /api/playlist` checks it, a move as `POST /api/playlist/{entry_id}/move` does, §5.8) and writes the answer under the request's id: the entry added, or the refusal code. A search runs `stage search` and writes its results the same way. The Stage reaches the store over HTTPS with a token kept in an environment variable, as `STUDIO_TOKEN` is; without it, the Stage serves only its own screens, as before A10.
- **Public side:** a build of `web/` with only the public screen, plus a few functions that read the snapshot, leave a request and read its answer. The functions hold the store's token; the browser never sees it.
- **Lyrics on the phone:** the snapshot carries an anchor, the position of the song at one instant, not a position per poll. The phone computes the position from the anchor and its own clock and moves through the lines on its own, so a slow poll delays only the events (start, end), not the lines. Neither device's clock has to be right: the Stage states the anchor's instant in the store's clock (Redis `TIME`), and the phone corrects its own clock against the same store clock through an uncached time route (the snapshot's answer is cached, so the time it would carry is stale). The Stage sends a new anchor on every event and every few seconds, which also corrects the drift between the audio clock and the wall clock (under 7 ms over 8 s in S3). Expected: the lines start on the phone 1 to 2 s after the song does, then follow it; to be measured in A10. The TV keeps its local lyrics (§5.8).
- **Polling and cost:** phones poll the snapshot every 2 s, and the function's answer is cached at Vercel's edge for 1 s (`Vercel-CDN-Cache-Control: max-age=1`), so the store sees about one read per second per region however many phones there are. Estimate for a 3 hour event with 20 phones, not measured: about 30,000 store commands (Upstash's free tier allows 500,000 a month), about 11,000 function runs and about 108,000 edge requests (third party sources give Vercel Hobby 1,000,000 of each a month; to be confirmed on Vercel's pricing page). The edge requests grow with the phones and the poll rate, so they are the number to watch.
- **Latency of a request:** an add, a remove or a move shows in the playlist 2 to 3 s after it is sent (the request read within about a second, a snapshot published, a phone poll). A public search takes the Stage's search time (1.1 to 1.6 s in A7) plus that round trip, about 3 s in all.
- **When the Stage is off or offline:** the page shows the last snapshot with a notice that the karaoke is offline, from the time the snapshot carries. The Stage's own screens and playback never depend on the internet (§4, principle 1).
- **Requests left in the store** while the Stage was off are discarded when `stage serve` starts.
- **Public screen:** one route, `/karaoke`, laid out for a phone, with three tabs at the bottom: *Agora* (the song playing, its lyrics following the anchor), *Fila* (the playlist, with remove, and up and down from B1) and *Buscar* (search and the library, with add). The operator's screens (§5.7) are not in the public build.
- **Vercel project:** its root is `web/`. The public build is the same React app built in a public mode, with its functions in `web/api/public/`. Vercel deploys it from GitHub: every merge on `main` goes to production, and a pull request gets a preview. The store's credentials exist only in the Production environment, so a preview never touches an event. The link's domain and the credentials stay out of the repository.

**Contract between the Stage and Vercel.** Both sides follow it; A10a builds the Stage side, A10b the Vercel side.

Store keys (Redis, prefix `karaoke:`), all JSON:

| Key | Written by | Value |
|---|---|---|
| `karaoke:snapshot` | Stage, on every change and at least every 3 s | `{version: 1, published_at_ms, session, playlist, lyrics}` |
| `karaoke:library` | Stage, at start and when the library changes | `{published_at_ms, songs}`, each song a search result as `GET /api/library` gives it (§5.8) |
| `karaoke:requests` | public functions (`RPUSH`); the Stage takes them from the head | `{id, at_ms, op: "add", song}`, `{id, at_ms, op: "remove", entry_id}`, `{id, at_ms, op: "move", entry_id, by}` (B1) or `{id, at_ms, op: "search", q}`; `at_ms` is when the function stored it, in milliseconds of its own clock (`Date.now()`) |
| `karaoke:answer:<id>` | Stage, expiring after 120 s | add: `{ok: true, entry_id}` or `{ok: false, error}`; remove and move: `{ok: true}` or `{ok: false, error}`; search: `{ok: true, results, notice}`; any of them: `{ok: false, error: "expired"}` or `{ok: false, error: "invalid_request"}` |

- **Times** (`published_at_ms`, `anchor.at_ms`) are milliseconds in the store's clock. The Stage learns its offset from Redis `TIME` (half the round trip as the correction) and refreshes it every minute. The first offset takes the faster of two `TIME` round trips, since the first request also pays for DNS, TCP and TLS. Upstash's command reference lists `TIME`; it is not tried against a real store yet.
- **What a change is:** anything in the snapshot but `published_at_ms` and the anchor's numbers. The Stage looks for one every 0.5 s, so a new anchor alone (a new position read) waits for the refresh of at most 3 s, while a new state, entry, playlist or lyrics goes out within 0.5 s. Nothing is published before the Stage knows the store's clock.
- **`session`:** `{state, entry_id, duration_s, paused, anchor}` (`paused` from B1; the volumes stay on the Stage). `anchor` is `{position_s, at_ms}` while playing, paused or not, `at_ms` being the moment REAPER reported that position (not the moment of publishing), and null otherwise. The Stage takes that moment halfway through the position read; for a song just started, the anchor is position 0 at the moment REAPER's play returned. While `paused`, `position_s` is where REAPER paused (the Stage reads it right after the pause) and the phone holds it without advancing it, so a phone that opens the page during a pause still shows the line; on a resume, the anchor is that position at the moment REAPER's resume returned. A null anchor during a pause was the first design; B1b measured a phone then overshooting the pause by about 2 to 3 s.
- **`playlist`:** the entries of `GET /api/state` (§5.8) without `detail`: `{id, state, step, progress, error, song}`. The detail is English text for the logs, and an unreachable Studio's detail carries its address. The session's own `error` and `detail` (REAPER errors) are not published either.
- **`lyrics`:** while playing, the lyrics of the song playing as `GET /api/library/{song_id}/lyrics` gives them, plus its `song_id`: `{song_id, status, lines}`; null otherwise. It is null while playing too when the package left the library or its lyrics file cannot be read (logged on the Stage), so a phone treats null as no lyrics in any state.
- **Requests:** an `add` carries a search or library result as `POST /api/playlist` takes it; the Stage applies each request as its own API would (refusals `not_addable`, `unknown_entry`, `entry_playing`, and `entry_current` for a `move`, whose `by` must be `-1` or `1`, or it is `invalid_request`), and a search runs `stage search` (`notice` `offline` when the online search failed). A request the Stage cannot read gets `{ok: false, error: "invalid_request"}` when it has an id, and is dropped otherwise; a request without a numeric `at_ms` is one it cannot read. **Requests expire:** when the store's clock, as the Stage knows it, is more than 15,000 ms past a request's `at_ms` as the Stage takes it, the Stage answers `{ok: false, error: "expired"}` and does not apply it, a search included. Both sides count the same 15 s from different instants: the phone from the answer to its POST, the Stage from `at_ms`, which comes before it. So the Stage can still apply a request after the phone gave up on it, by up to the POST's latency, not by 15 s; that is acceptable, since the phone's text tells the singer to check the queue. `at_ms` comes from the Vercel function's clock and the age is measured in the store's clock. That the two are close enough is an assumption, not measured: both are NTP-synced servers, and a difference of a few seconds would only move when a request expires. When `stage serve` starts it deletes `karaoke:requests`, and reads none until that delete worked. A request is answered at most once and may get no answer at all: when the store fails after the Stage took it (an add, a remove or a move is then applied but not answered), or when applying it fails on a bug of the Stage (logged). The phone's 15 s limit covers both. Searches run on 2 threads of their own, so a slow one holds neither the requests behind it nor the snapshot.
- **Stage configuration:** the Upstash REST URL and token in the environment variables `KARAOKE_STORE_URL` and `KARAOKE_STORE_TOKEN`. Without both, `stage serve` publishes nothing and reads no requests, as before A10, and says so on stderr. A store that does not answer never stops playback or the local screens: the Stage logs it and tries again on its next cycle.

Public routes (Vercel functions, under `/api/public/`; the store's credentials are read only there):

| Method | Route | Answer |
|---|---|---|
| `GET` | `/api/public/state` | The snapshot as stored, or `{version: 1, snapshot: null}` before the first one. Cached at the edge for 1 s. |
| `GET` | `/api/public/library` | The library as stored, or `{songs: []}`. Cached at the edge for 5 s. |
| `GET` | `/api/public/time` | `{now_ms}` from Redis `TIME`. Never cached. The phone calls it at start and every minute, and keeps `offset = now_ms - (sent + received) / 2`. |
| `POST` | `/api/public/requests` | Takes `{op: "add", song}`, `{op: "remove", entry_id}`, `{op: "move", entry_id, by}` with `by` `-1` or `1` (B1) or `{op: "search", q}`, gives it an id and `at_ms` (`Date.now()`), stores only those fields and answers 202 `{id}`; any other body is 422 `{error: "invalid_request"}`. |
| `GET` | `/api/public/requests/{id}` | The answer, or 404 `{error: "pending"}` while there is none. Never cached. The phone polls it every second and gives up after 15 s. |

- **Offline:** the phone treats the Stage as offline when `published_at_ms` is more than 10 s behind its corrected clock, or when there is no snapshot.
- **Phone reordering (B1):** the *Fila* tab shows up and down buttons on the entries behind the current one, sending `move`; the entry gets to its place on the next snapshot.
- **Phone polling:** the snapshot every 2 s, the library when the *Buscar* tab opens. While playing and not paused, the position is `anchor.position_s + (corrected now - anchor.at_ms) / 1000`, never past `duration_s`.

Filled in by A10b, where the contract left a gap:

- **Caching headers:** the cached routes answer `Vercel-CDN-Cache-Control: max-age=1` (`max-age=5` for the library), which only Vercel's edge reads and does not forward, and `Cache-Control: public, max-age=0, must-revalidate` for the browser. The uncached routes and every error answer `Cache-Control: no-store`.
- **Store failures:** missing credentials, a store that does not answer or an error answer give 503 `{error: "store_unavailable"}` on every route, logged in Vercel. The phone shows it as the other refusals, in Portuguese.
- **Request bodies:** `song` must be a JSON object and `entry_id` and `q` strings; the song is stored as sent, for the Stage to check. Fields beyond the contract's are dropped. The id is a random UUID. A body, or an id in the path, the function cannot read or write back (bad percent encoding, a song nested too deep) is 422 `invalid_request`.
- **Unanswered requests:** after 15 s the phone shows the code `no_answer`, a generic "could not complete" in Portuguese; the request may still be applied later, so it says to check the playlist. An add, a remove or a move (B1) whose POST is lost (no connection, or cut after 5 s) shows `no_answer` too, since the function may have stored it. A search that gets no answer or `expired` shows `search_no_answer` instead, which only says to try again. While it waits, a poll lost on the network, cut after 5 s, or answered `store_unavailable` is retried: the request is already stored.
- **Phone calls:** every call of the phone gives up after 5 s as `network`, so a hung poll never freezes the page. The phone words some codes its own way (`network`, `http_error`, `entry_playing`), since the public reaches the Stage through the internet and cannot act on it, and never shows the English detail.
- **Before the clock is known:** until the first answer of the time route, the phone uses its own clock as it is.

Filled in by B1b, where the contract left a gap:

- **The position while paused:** the phone shows the paused anchor's `position_s` (never past `duration_s`) and does not move it on, so a phone that opens the page while paused shows the right line. A snapshot that plays without an anchor, paused or not, has no position: the phone holds the last one it showed for the same entry, or shows no current line when it showed none for that entry (never the previous song's position).
- **A move lost on the way:** a move whose POST is lost shows `no_answer`, as an add or a remove does, since the function may have stored it.
- **The volume steps on the TV:** a minus and a plus per volume, 2 dB a step, rounded to whole dB; a step from a value REAPER holds outside -30 to +6 dB lands back inside the range.
- **Known and accepted by the maintainer, left as they are:**
  - A quick double click on pause or resume is not guarded against beyond the button being disabled while its call runs; the live test checks what REAPER does with it.
  - The reorder arrows can move an entry twice. On the TV, after a move the entry sits where the neighbor was, so a second quick click lands on the neighbor's arrow and can undo the move. On the phone, the button comes back when the Stage answers, before the snapshot with the new order arrives (about 1.5 s later), so a second tap in that window moves the entry again.

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

**S1, round 1 (2026-11-10): pass.** Runbook and click generator in [`spikes/s1-latency/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f3320a73af0f6b65f020c23b19c75cce30/spikes/s1-latency).

- An entry-level 1-preamp interface with no native ASIO driver (ASIO4ALL over the Windows class driver) measured about 24 ms round trip at 128 samples and 22 ms at 64. WASAPI exclusive was worse (about 30 ms). Most of the delay is fixed by the driver path, so smaller buffers barely help. A re-measurement after the setup fixes below gave the same ~24 ms at 128 samples, with much cleaner sound.
- Singing still felt comfortable, even with a free pitch correction plugin that adds about 1000 samples (about 23 ms). The plan keeps the effects in the live path. The official chain is defined later, preferring zero-latency pitch correction.
- The idle LatencyMon run was green (highest DPC under 1 ms). 128 samples is the working buffer.
- Hiss, low volume and a robotic, doubled voice all came from the setup, not from the hardware:
  - the interface was on a USB hub: plugged straight into the machine, the hiss dropped and the volume went up a lot;
  - Windows had the interface at a different sample rate from REAPER's 44.1 kHz, so ASIO4ALL was resampling. Plugging the interface into another USB port makes Windows see a new device, with the default sample rate and ASIO4ALL's default buffer (512 samples, about 57 ms round trip), so both have to be set again;
  - the voice was monitored twice (REAPER's input monitoring plus another monitoring path), which sounds doubled and robotic.
- These become setup rules for the template and the first-night checklist (A6, A9).

**S1, round 2 (2026-11-22): pass.** An interface with two mic preamps and a native ASIO driver, wired to active studio monitors with balanced cables, on the same Stage machine, plugged in, at 44.1 kHz. The click was recorded through the air, with the mic 30 cm from a speaker (round 1's runbook).

- **REAPER must request the buffer size.** With *Request block size* off, REAPER ran at 512 samples whatever the driver's own control panel said, so the first measurements (about 34 to 45 ms) and an earlier test by ear "at 64" were really at 512. With it on, the menu bar shows the size in use.
- **Round trip:** about 11 ms at 64 samples (REAPER reports 2.9/3.4 ms) and about 23 ms at 256 (7.3/8.5 ms), against round 1's 22 ms at 64. 128 was not measured: both ends were comfortable.
- **Pitch correction:** Graillon 3 Free reports 1075 samples, and REAPER showed it as a whole number of buffers: 1088 at 64 (about 25 ms) and 1280 at 256 (about 29 ms). The voice with it comes back in about 36 ms at 64 and about 52 ms at 256. Singing was comfortable at both, with and without it. The click test never shows a plugin's delay: the recording takes the input before the track's effects.
- **Load test at 64 samples:** a full night started by `stage-night.ps1` (`stage serve`, the browser in full screen, the public store, the Studio up), Graillon on both mic tracks and effects changed by hand while singing, 5 songs in a row, and a new song separated and downloaded (98 MB in about 84 s) while another played. No crackle. Only one song was transposed, for under a minute, and not during the download.
- **64 samples is the working buffer;** 256 is the fallback for a machine that crackles. The setup rules are in `reaper/README.md`.

**S3, round 1 (2026-11-11): pass.** Listener, driver and runbook in [`spikes/s3-reaper/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f3320a73af0f6b65f020c23b19c75cce30/spikes/s3-reaper). Two generated test songs, 7 WAV stems of 240 s each, on the Stage machine of S1.

- **Load:** 80 loads alternating two songs, with the mic monitored and someone singing into it: no audible dropout, and no failed load. Command to ack round trip: median 64 to 84 ms, max 101 ms (the first, cold load took 164 ms). The listener's own work: 12 to 19 ms. The 2 s target has a wide margin.
- **Mute per stem, transpose +2 while playing:** both worked by ear, with no crackle. CPU peaked at 3% with 6 stems pitch shifted.
- **Position:** `TRANSPORT` polled every 100 ms answered every ~95 ms (median), each step within about 15 ms of the wall clock (33 ms worst case while transposed), with under 7 ms of drift over 8 s. Enough for line-synced lyrics.
- **The web interface listens on IPv4 only** (`0.0.0.0:8080`). On Windows, `localhost` tries IPv6 first and every request waits about 2 s, so the Stage must use `127.0.0.1`.
- Confirmed against the running REAPER: the request encoding (`;`, `/` and `\` inside the JSON survive) and the escaping of `GET/EXTSTATE` replies. Tracks without a stem name, like the mic track, kept their items.
- For A6: new stem tracks inherited input monitoring on from the user's defaults (see the session template in §5.4), and stem tracks are found by name only, so a user track named like a stem would be overwritten. Both are template decisions for A6.

**S4, round 1 (2026-11-11): pass.** Script and runbook in [`spikes/s4-search/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f3320a73af0f6b65f020c23b19c75cce30/spikes/s4-search). 21 test songs typed by hand (typos kept), in more than one language, with duets, live recordings and songs that have many covers. ytmusicapi 1.12.3, yt-dlp 2026.10.26.

- **Search:** the right version was in the ytmusicapi top 3 for 21 of 21 songs, and first for 20. The one miss at #1 was a different song by the same artist, with the right one at #2. Typos in titles and artist names didn't hurt. Two of the chosen versions are live recordings, and both ranked first. Covers, tributes, karaoke tracks and lullaby versions fill ranks 2 and 3, so the TV must show artist, album and duration to tell them apart.
- **yt-dlp as the fallback:** its #1 is usually the official music video, often with a different length or mix from the studio audio. That confirms ytmusicapi as the main source. Search with flat extraction needs no JS runtime; downloading does (`yt-dlp[default]` with its challenge solver, plus Deno), which worked on the Studio.
- **Lyrics:** LRCLIB had line-synced lyrics for 21 of 21 chosen versions, all on the first request (`/api/get` with title, artist, album and duration). Ranks 2 and 3 had far less (13 and 9 synced), as expected for covers.
- **For A7:** look up lyrics for the version the singer picks, not for the first result. `/api/get` can answer 200 with an empty record (no synced, plain or instrumental lyrics), so treat that like a miss and fall back to `/api/search`, keeping the best hit within the duration tolerance (2 s, the same as `/api/get`).
- **Times:** median per query of 0.64 s (ytmusicapi), 0.82 s (yt-dlp) and 0.29 s (LRCLIB).

**S2, round 1 (2026-11-11): pass.** Script and runbook in [`spikes/s2-separation/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f3320a73af0f6b65f020c23b19c75cce30/spikes/s2-separation). Five test songs from S4 (a pop song with stacked lead vocals, a rock song with two-part harmonies, a live duet in constant harmony, a male and female duet, a disco song heavy on backing vocals), each through three combinations, all with `htdemucs_6s` for the instruments. audio-separator 0.48.0, torch 2.14.1 (CUDA 13.0) on the Studio.

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

**S5, round 1 (2026-11-11): pass on the Studio side, with a caveat.** Script and runbook in [`spikes/s5-transfer/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f3320a73af0f6b65f020c23b19c75cce30/spikes/s5-transfer). The largest S2 package (135.7 MB, a 6:14 song) served by Python's static file server on the Studio and downloaded by the Stage over Tailscale, one file after the other, 3 runs per network. The sha256 of every stem matched the manifest on all 9 runs.

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

**S6, round 1 (2026-11-12): pass, RMVPE chosen.** Script and runbook in [`spikes/s6-melody/`](https://github.com/FabioFischer/karaoke-night/tree/148dd6f3320a73af0f6b65f020c23b19c75cce30/spikes/s6-melody). Six songs separated with combination B: the five from S2 plus a rock song with a well-known modulation before its last chorus. Three trackers on the lead stem, the backing of the two-part harmony song as an extra. torchcrepe 0.0.24 on the GPU, pYIN (librosa 1.0.0) and RMVPE (`rmvpe-onnx` 0.2.3) on the CPU, all on a common 10 ms grid. Notes and keys were judged by ear on the Stage, from a synth preview over the instrumental and from the note MIDI files in REAPER next to the stems.

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

The alpha is a test run, not a finished event. The TV mirrors the Stage screen (over HDMI or screen casting) and is driven with the Stage's keyboard and mouse. The first night (A9) has no queue of participants: songs are picked at the Stage. Public access to the queue and the playlist comes after it, in A10.

A7 and A8 already follow the beta screens (§5.7): the same routes, search results with the fields the beta shows, and a session built around a playlist. What they leave out waits for the beta: the preview, volumes, pause, reordering and the results report.

| Milestone | Deliverable | Done when |
|---|---|---|
| A1 | Repository skeleton: uv workspace, Vite, and ruff, pytest and tsc in GitHub Actions. | CI is green on the first PR. |
| A2 | `core`: pydantic model of the manifest, with validation and tests. | Tests pass for valid and invalid manifests. |
| A3 | Studio pipeline CLI: `studio process <url\|file>` produces a complete package. | All 20 test songs are processed, and their stems add up to the original. |
| A4 | Studio API (§5.3): FastAPI with jobs, library, files and deletion, started with `studio serve`. | The Stage creates a job and downloads the package over Tailscale. |
| A5 | Stage library (§5.6): the folder of packages, with no SQLite (it comes with the play history), resumable downloads with hash checks, and `stage sync`, which mirrors the Studio library (adds, updates and removes), plus `stage list`. | With no internet, the Stage lists and plays synced songs. A song deleted on the Studio disappears from the Stage after the next sync. |
| A6 | REAPER bridge: session template (mic and stem tracks, no effects), Lua listener, the Python `Player` (load, play, stop, mute, transpose, position, end of song) and `stage play`. The pitch correction is no longer in the template: the effects chain guide comes from a guided test of pitch correction options. | A song plays from start to end driven only by the Stage. |
| A7 | Search: `ytmusicapi` with a `yt-dlp` fallback, showing library matches first. Up to about 10 results, each with what the §5.7 search list shows: full title, thumbnail, artist, album, duration and `video_id`. | Same criterion as S4. |
| A8 | TV UI on the §5.7 routes, around a playlist: home (search or library, add to the playlist) → Stage mode setup (stems + key) → playing with lyrics → results (next song, or its setup). Two PRs: A8a, the Stage API (§5.8) and `stage serve`; A8b, the screens. | A first-time user picks a song and sings without help. |
| A9 | First night: one shortcut opens REAPER, the Stage and the full-screen browser (`scripts/stage-night.ps1`), plus a setup checklist (interface plugged straight into the machine, Windows sample rate matching REAPER's, a single monitoring path). One command, run by hand or over SSH, starts the Studio API for the event with its firewall rule (`scripts/studio-start.ps1`), and another stops it and removes the rule (`scripts/studio-stop.ps1`, §3, Studio availability). The runbook is `docs/NIGHT.md`. | A full karaoke test session at the venue. |
| A10 | Public access (§5.9): the Stage publishes a snapshot to a store on Vercel and applies the requests left there; a public `/karaoke` on Vercel shows the playlist, searches, adds and removes songs, and follows the lyrics. The separation stays on the Studio (it needs the GPU, its jobs take minutes, and `yt-dlp` needs deno). | A participant on a phone, outside the venue's network, adds a song, sees it in the playlist and follows its lyrics. |

**A3 (2026-11-12): pass.** All 21 test songs processed on the Studio with `studio process`, from their YouTube Music video ids. Every package loads with the §5.2 model, all 147 stem hashes match, every stem is 44.1 kHz stereo 16-bit, and every song has synced LRCLIB lyrics. The stems add up to the mix on every song: the largest sample error was between -76.6 and -80.8 dBFS (3.0 to 4.9 steps of 16 bits) and the residual RMS between -89.5 and -93.0 dBFS. The timed runs took 96 to 259 s per song, download included, and the library came to 2.1 GB.

- **Port check.** Run twice on the same mix, the A3 stems differ from each other exactly as much as they differ from the S2 package of that song: lead and backing (RoFormer) by about -97 dBFS, under one 16-bit step, and the instruments (Demucs) by 17 to 29 dB of SNR. Demucs is not repeatable from run to run, which S2 never measured, so `gain_db` moves a little too (2.19 to 2.64 dB on one song). The stems still add up, because `other` is the remainder. Reprocessing a song gives slightly different instrument stems; seeding Demucs or turning off its random shifts is an option if that ever matters.
- **Lyrics** are written as LRCLIB sends them: 2 of the 21 came with CRLF line endings, so the lyrics reader must accept both.
- **Downloads** were blocked by YouTube now and then (§8): all 4 blocked songs downloaded on a later try, the last about an hour after the batch.

**A4 (2026-11-13): pass.** `studio serve` ran on the Studio over the A3 library (21 packages), and the Stage drove it with `curl` over Tailscale, from the home network.

- **Access:** no token and a wrong token both got 401; `/docs`, `%2E%2E` as a song id and `..%2F` in a file path got 404; a malformed video id and a `file` source got 422.
- **Dedup:** `POST /jobs` with the video id of a song already in the library, under another title and artist, answered with a job already `done` for the existing package (POST and GET together in 0.33 s). A second POST of a song being processed returned the running job.
- **A new song (3:59)** went `queued → downloading → separating (vocals, karaoke, instruments) → packaging → done`; `lyrics` lasted less than the 2 s polling interval. The job took 1544 s because the GPU was shared with another application at 99% use the whole time (vocals 1121 s, karaoke 354 s, instruments 18 s), so this run says nothing about the A3 times. Before `downloading`, the job showed `queued` with `attempt` 1 for 36 s while torch and audio-separator were imported; A8 should show that as "starting", not "waiting".
- **Download:** the package (118 MB, 7 stems plus synced lyrics) came down in 19.2 s, one file after the other, and all 7 stem hashes matched the manifest. A stem cut off after its first 1 MB (a `Range` request) and then resumed with `curl -C -` got 206 and the same hash.
- **Delete:** with a stem being downloaded, `DELETE /songs/{song_id}` answered 409 and the package stayed whole; once the download stopped, 204, the song left `/songs` and its manifest answered 404. No dot folder was left in the library.
- **Not run live:** the download retries (proven with fakes only, since a YouTube block cannot be caused on purpose) and `DELETE /songs` (it would have deleted the library).

**A5 (2026-11-13): pass.** `studio serve` on the Studio over the 21 package library, `stage sync` on the Stage over Tailscale, from the home network, into an empty Stage library.

- **Interrupted and resumed:** the first sync was killed after 45 s, with 2 songs installed and a third one in its `.partial` folder (25,194,247 bytes). The next sync installed the other 19 and downloaded 1,930,187,676 bytes, exactly the size of those 19 minus what the partial already held, in 401 s (38.5 Mbit/s, the same LAN ceiling as S5). All 147 stem hashes of the Stage library matched their manifests. A third sync found 21 songs unchanged in 0.3 s.
- **Add, update, remove:** a temporary package (a copy of one song under another id) was added on the next sync (130.1 MB in 35.3 s), and downloaded again as `updated` once its `created_at` changed on the Studio. With that package loaded in REAPER, deleting it on the Studio and syncing reported it `busy` and kept it whole (exit 0); after REAPER loaded another song, the next sync removed it. No dot folder was left on either machine.
- **No internet:** with the Stage's Wi-Fi off (internet and Studio both unreachable), `stage list` listed the library and a synced song loaded in REAPER with the S3 listener (95 ms round trip) and played: all stems sounded right, with no dropouts.
- **For A6:** the stem tracks of the session used for the test still had input monitoring on, as S3 noted; the A6 template sets it off.

**A6 (2026-11-13): pass.** REAPER 7.85 on the Stage, the listener started by `__startup.lua`, the session opened from `reaper/template/karaoke.RPP` with no warning: the mic tracks armed and monitoring, the stem tracks not.

- **Start to end, driven by the Stage:** `stage play` of the shortest test song (2:19) with the lead muted loaded it (load acks of 95 to 124 ms), followed it second by second and printed "the song ended" with exit 0 when REAPER stopped at the end of the project.
- **How REAPER reports the end:** a 50 ms poll of `TRANSPORT` showed playing (1), paused (2) at the pause button, playing (1) again when it was pressed again, with no stopped state in between, and stopped (0) at the end. At the end the position goes back to where playback last started (31.32 s after a resume there), not to the end of the song, so the rule cannot rest on the duration; "stopped without the Stage asking" is the rule, and a stop by hand (the space bar is play/stop in REAPER) ends the song too. Ctrl+C in a console stopped playback and exited 130.
- **Listener:** a malformed command got an error ack with its id, and a command with no readable id an error ack without one; the next `ping` answered. A stem track the listener created came with input monitoring off. During the test the S3 listener, started by hand from the action list, was still running next to the startup one and was the one a malformed command killed: start the listener only from `__startup.lua`.
- **Headroom:** the stems come close to full scale, so the voice on top was quiet, and clipped once raised. The template now holds the stems in a `Music` folder track at -8 dB, which left room for the voice and gives the music one fader.
- **Input monitoring crackled** on battery until the buffer went up to 512 samples; plugged in, S1 round 2 ran clean at 64 under a full night's load.
- **Pitch correction,** the guided test for the effects chain guide (`reaper/README.md`): Graillon 3 Free corrected well but added about 26 ms (from about 30 to about 56 ms round trip at 512 samples), uncomfortable at that buffer; with a native driver at 64 samples it was comfortable (S1 round 2). ReaTune added about 7000 samples and is not usable live.

**A7 (2026-11-14): pass.** `stage search` on the Stage, ytmusicapi 1.12.3 and yt-dlp 2026.10.26, over the 21 test songs typed as in S4, checked by a script against the video ids the S4 verdicts chose (the ones A3 processed).

- **Search:** run once with an empty library, the chosen version was in the online top 3 for 20 of 21 songs, first for all 20; a second run after the fix round found 21 of 21. The miss was YouTube Music itself: seven calls of the same query returned the chosen video id at #2 in some and left it out of the top 4 in others, with an upload of the same title and length always first. Each search took 1.1 to 1.6 s.
- **Library first:** with the 21 package library, the local song came first, marked ready, for 21 of 21.
- **Without internet** (simulated with a dead proxy): the library matches and the notice, exit 0. It took 19.3 s, 16.5 s of it yt-dlp retrying on its own; with `extractor_retries` at 1 it takes 11.2 s (about 2 s ytmusicapi, 8 s yt-dlp). A network that hangs instead of refusing was not measured.
- **Blank text** (empty or only spaces) searches nothing: before the fix it called both sources, which failed on it and printed the offline notice with internet up, and yt-dlp returned unrelated videos for spaces.
- **Fields:** ytmusicapi returned 20 to 29 songs per query whatever its limit says, with thumbnails of 60 and 120 px only, and some songs without an album; yt-dlp entries have no album and the channel stands in for the artist, with thumbnails up to 720 px.

**A8a (2026-11-14): pass.** `stage serve` on the Stage, against the Studio over Tailscale and REAPER 7.85 with the session template.

- **Studio:** adding a song the Studio already held went `queued` → `fetching` (0 to 8 files) → `ready` in 11.7 s, into an empty library. Removed after 2 of its stems, the entry left the playlist and its download stopped, the partial folder kept. With the Studio unreachable, an added song stayed `queued` with the `studio_unreachable` warning instead of failing.
- **REAPER, driven only through the API:** a 2:19 song with the lead muted and the key at +2 started in 0.95 s and played to its end; the session moved to `results` on its own when REAPER stopped. "Next now" played the next song with the lead still muted and the key back to 0; a stop through the API about 20 s in moved the session to `results` and left REAPER stopped; "setup next" with an empty playlist went back to `setup`. By ear: lead silent in both, the first song higher, the second in its original key, no crackle or dropout on the mic.
- **Routes:** a reload of `/karaoke/stage/results` served the web, an unknown `/api` path answered 404 and a GET on a POST route 405, both as `{error, detail}`.

**A8b (2026-11-14): pass, with the first-time user still to come.** The maintainer used the screens in a browser on the Stage, against `stage serve` over the 21 song library and REAPER with the session template, and sang 3 songs through them: search and library, add to the playlist, setup (stems, key), start, lyrics, results and the next song all worked. The A8 criterion asks for someone who has never used the app; that person is the first night's (A9).

- **Bug found live, fixed in the same PR:** with `stage serve` running without a Studio, a new song failed (`no_studio`) and, being first in the playlist, became the current song: "Começar" stayed disabled and the library songs behind it could not play, since the current entry was listed nowhere with a remove button. Now, while the session is in setup, the current entry shows in the home's side queue and in the setup bar with its state and remove button. Checked live by the maintainer: the failed entry showed with its reason, was removed, and the library song behind it became current and played.
- **Review fixes:** `session.stems` reads only the current song's manifest and only in setup (the reviewer measured 34 ms per poll with a 300 package library when it scanned the whole library); an action's answer is no longer dropped by a poll sent before it; errors and lyrics of the previous song are cleared; the screen's automatic moves replace the browser history instead of pushing.

**A9 tools (2026-11-14): pass, with the night at the venue still to come.** The night scripts, checked live on the Studio and the Stage over Tailscale, from the home network.

- **Studio:** `studio-start` over SSH created the rule `karaoke-studio` and started `studio serve` detached; with the SSH session closed it kept answering (`/songs` 200 in 0.08 s, 401 without the token), and the token was not in the log. Run again, it said the server was already running and started nothing. `studio-stop` stopped the 4 processes of the server (the PowerShell wrapper, `studio.exe` and two Python processes) and removed the rule; the port stopped answering.
- **Stage:** with REAPER closed, the shortcut's script opened REAPER with the session, started `stage serve` in its own window and opened Edge full screen at `/karaoke`, with no first run page, in about 2 s. Run with REAPER open, it left REAPER as it was. The maintainer played a library song through the screens: it played with its lyrics following.
- **Review fixes:** `studio-start` now waits 5 s and, when the server already stopped (an empty token file, the separation extra missing, the port in use), shows the end of the log and exits 1 instead of reporting it started; both Studio scripts require an elevated session, since outside one the command lines of elevated processes read as empty and the check for a running server would go blind; the server filter requires `serve` right after `studio.exe`; an empty token file no longer breaks the shortcut.
- **Messages from Windows itself** (a failed cmdlet, the elevation refusal, a Windows error in the log) come in the language of Windows; every message the scripts write is in English.
- **Dress rehearsal at home:** the Studio started with `studio-start`, the Stage with the shortcut's script, and the maintainer added a song not in the library through the screens: ready 4 min 37 s after it was added, then sung. On the Studio: `starting` 41 s (the models loading for the first job after the server started), `downloading` 17 s, separation 72 s (vocals), 75 s (karaoke) and 20 s (instruments), 167 s in all for a 5:00 song, the 0.56 of its length S2 measured, then lyrics and packaging 6 s. The download to the Stage took 46 s for 98.5 MB in 8 files (about 17 Mbit/s, under the 38 Mbit/s of A5 on the same network; not investigated).

**A10 (2026-11-15): pass.** Two PRs, A10a (the Stage side and the contract) and A10b (the Vercel side), deployed by Vercel from `main` to the production domain, with the store (Upstash Redis) and the function in São Paulo. The Stage started with the shortcut's script and a store file, without a Studio; the maintainer used the public page from a phone on a mobile network, off the Stage's network.

- **Production, checked from outside:** the functions of a plain Vite project build and answer JSON (`time`, `state`, `library`); Redis `TIME` works over Upstash's REST API; the cached routes went `MISS`, `HIT`, `MISS` on three GETs in a row (1 s at the edge) while the browser gets `max-age=0`; `time` is `no-store`; `/karaoke` serves the page, `/` and `/karaoke/` redirect to it.
- **From the phone:** adding a library song and removing an entry worked; a public search took about 3 s; a song from the online results failed at once, as it should without a Studio. Each entry showed in the public snapshot 0 to 2 s after it showed on the Stage.
- **Lyrics:** sampled every 2 s over one song, the position a phone computes from the anchor was 37 to 326 ms ahead of the Stage's own (median 201 ms, 43 samples). The Stage's position is read every 250 ms, so the sampling alone accounts for about half of that lead. By ear, the lines on the phone followed the song. The snapshot the phones read was 1.8 s old at the median, 3.2 s at most.
- **Network:** 6 of about 400 requests from the Stage to the public page timed out in the TLS handshake on the home network. The page recovered on the next poll each time; not investigated.
- **Not measured yet:** the edge requests and store commands a full night spends (§8), and a page with many phones at once.

### Phase 2: beta

The beta's screens are in §5.7; the items below fill them in (the results report comes with item 7).

| Milestone | Deliverable | Done when |
|---|---|---|
| B1 | Stage screen controls (§5.7, §5.8, §5.9): pause and resume, music and voice volumes on the TV (in the setup and while the song plays), and reordering the playlist on the TV and on the phone. Two PRs: B1a, the Stage side; B1b, the web (TV, phone and the public function). | At home, live: a song paused and resumed with the lyrics stopping and going on, on the TV and on a phone; both volumes changed by ear; the playlist reordered from the TV and from a phone on a mobile network. |
| B2 | The pre-performance, item 4 below (§5.4, §5.7, §5.8): the excerpt of the song in the setup, with its lyrics on the TV. One PR, Stage and web. | At home, live: the excerpt starts at the right place with its lyrics following it; a key change and a stem muted during it are heard at once; it stops alone after 20 s and stays in the setup; *Começar* in the middle of it plays the song from its start; removing the song in the middle of it stops it; a phone shows nothing of it. |

Next, as separate rounds: the analysis of each song (pipeline version 2, items 2 and 3 below), and the second microphone once the new interface is in (item 1). Stems toggled while the song plays and the QR code on the TV are left for later.

Proposed order of the items:

1. **Two microphones:** each mic gets a role (lead or backing), and the role decides which original vocal stems are muted.
2. **Lyrics and melody analysis** on the Studio. Together they become `pipeline_version` 2, and the library is reprocessed.
   - Lyrics split into lead and backing, with WhisperX alignment.
   - Melody: pitch tracking on the lead stem gives the expected notes and the key per section. Backing gets the same only when it holds a single harmony line; several voices at once aren't reliable. The tracker is RMVPE, from S6.
3. **Pitch correction guided by the song:** when it loads a song, the Lua listener writes a guide MIDI track with the expected notes and a key automation, both already transposed. Whether the pitch correction plugin uses them stays a choice made in REAPER. Target notes must allow singing an octave up or down.
4. **Pre-performance,** on the setup of Stage mode (§5.7), milestone B2: before singing, the app plays a short excerpt with its lyrics on screen, so the singer can try the key, the muted stems and the volumes. No volume per stem. The lyrics offset adjustment is deferred by the maintainer, since no song has needed it so far.
5. **Phone companion page:** A10 already brings live lyrics and adding songs to phones, over the internet (§5.9). What is left for the beta is joining with a QR code shown on the TV.
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
| Audio interface | At least 2 mic preamps and a native ASIO driver (for example Behringer UMC204HD or Focusrite Scarlett 2i2). With one, S1 round 2 measured about 11 ms round trip at 64 samples. Without a native driver it still works through ASIO4ALL, at about 22 to 24 ms (S1 round 1). |
| Speakers | Wired from the interface output. **Never Bluetooth**, which adds 100–200 ms of delay. Enough power to hear the voice over the music without running at full volume. For larger rooms, a PA with a line input. |
| Stage machine | High-performance power plan, plugged in, validated with LatencyMon. |
| Studio machine | A CUDA-capable NVIDIA GPU. S2 peaked at 5.3 GB reserved by PyTorch (combination B), so 8 GB of VRAM should be enough. |

## 8. Risks

| Risk | Mitigation |
|---|---|
| High latency on the Stage machine (DPC or drivers) | Run S1 early. Plan B uses direct monitoring. Use an interface with a native ASIO driver. |
| Other singers notice the latency more than the maintainer did in S1 | Ask the singers on the first night (A9). Pitch correction off saves about 25 ms (S1 round 2), and plan B remains an option. |
| Studio offline, or no internet at the venue | Preload the library. In the beta, a cloud Studio. |
| The public page goes over a free tier (Vercel Hobby or Upstash) on a busy night | Count edge requests and store commands on the first public night (A10). Phones poll every 2 s and the edge caches each answer for 1 s (§5.9); a slower poll costs only playlist latency, not lyrics timing. |
| YouTube changes and `yt-dlp` breaks | Keep `yt-dlp` up to date (recent versions need a JS runtime such as Deno). Accept local files as a source. Measured in the A3 batch with `yt-dlp` 2026.10.26, the latest stable release then: of 21 downloads in a row, 4 were blocked by YouTube, 3 with `HTTP Error 403: Forbidden` on the download after the metadata had worked and 1 with "Sign in to confirm you're not a bot"; tried again later, all 4 downloaded normally (3 within minutes, the last about an hour later). `studio process` has no retry: the failure is shown, and trying again is left to the person. The Studio API job queue (§5.3) tries a failed download again, and only a download: up to 3 retries, after 2, 10 and 30 minutes. |
| Poor lead vs backing separation (duets) | The "no vocals" preset always works. Pick models in S2 and reprocess when better ones appear. |
| Song missing from LRCLIB, or out of sync | Unsynced lyrics as a fallback. In the beta, automatic alignment. An offset adjustment is deferred (no song has needed it so far); the manifest's `lyrics.offset_ms` is still read (§5.8). |
| Melody tracking misled by backing residue in the lead stem, melisma, or rap and spoken parts | Ignore sections with low confidence or no pitch, and don't score them. RMVPE (S6) got the octave right on a strong held note where the others jumped up. Ignore notes where the singer is silent. |
| Mics pick up the original backing vocals from the speakers, which skews scoring | Dynamic mics and speakers in front of the singers. Measure it when scoring is built. |
| Feedback with a PA | Dynamic mics, speakers in front of the singers, low cut and a gate in the effects chain. |
| Slow upload from the Studio, or a slow downlink at the venue | FLAC by default. S5 measured 53 to 57 Mbit/s of upload from the Studio, and a phone hotspot as the bottleneck (12.6 Mbit/s median). If the venue's network is that slow, high-bitrate Opus just for transfer. |

## 9. Open decisions

- [x] Separation models (from S2: combination B, see §3)
- [x] Pitch tracker and key detection method (from S6: RMVPE, see §3)
- [ ] Loudness target between songs (LUFS)
- [ ] Hardware recommendations confirmed (from S1 and sound tests)
- [x] Public access (A10): Vercel holds the public page, a few functions and a store; the Stage publishes a snapshot there and applies the public's requests, and nothing on the internet connects to the Stage or the Studio (§3, §5.9)
- [ ] Preview of the original (§5.7): where the audio comes from for a search result (a stream URL from `yt-dlp`, which needs deno on the Stage, or YouTube's own player), and how the browser's sound reaches the speakers while REAPER holds the interface through ASIO. A library song has no original file, only stems that add up to it.
- [ ] Thumbnails offline (§5.7): the library screen needs them without internet, so they travel in the package (a manifest change) or get cached on the Stage.
- [x] Settings carried to the next song (§5.7, results): playing the next one at once keeps the muted stems and puts the key back at 0 (§5.8). Volumes come with the beta.
- [x] Where the playlist lives: in the Stage's memory while the service runs (§5.7). How A10's public access reaches it belongs to the A10 decision above.
- [ ] A health check that confirms the Studio, the Stage and the public page are all up (idea, not scheduled).
