# karaoke-night

Self-hosted karaoke that plays the **original recordings**, not re-recorded backing tracks. Songs are split into stems with AI source separation, singers choose which stems to keep, and live vocals run through a real effects chain in [REAPER](https://www.reaper.fm).

> **Status:** the alpha (Phase 1) is done, but for a check with someone who has never used the app. The beta (Phase 2) has started: pause, volumes, reordering and the pre-performance excerpt are done.

## How it works

- **Studio:** a GPU machine that finds a song, downloads the audio and splits it into stems (lead vocals, backing vocals, drums, bass, guitar, piano, other). It also fetches synced lyrics and packages everything.
- **Stage:** the machine where people sing. It runs REAPER with the microphones and effects, shows search and lyrics on a TV, and plays songs from its local library. Once songs are synced, it works offline.
- **Public page:** a page on Vercel where guests follow the playlist and the lyrics on their phones, and search and add songs. The Stage publishes its state there and applies the guests' requests; nothing on the internet connects to the Stage or the Studio.

```
Studio (GPU) ──Tailscale──► Stage ──► REAPER ──► speakers
                              ├─────► browser on the TV (search, stems, lyrics)
                              └─────► public page on Vercel ◄── phones
```

## Required apps

Two Windows machines: the **Studio**, with an NVIDIA GPU, and the **Stage**, with an audio interface, microphones and a TV. Install these on a clean machine:

| App | Studio | Stage | What for |
|---|:-:|:-:|---|
| [Git](https://git-scm.com) | ✓ | ✓ | Clone this repository. |
| [uv](https://docs.astral.sh/uv/) | ✓ | ✓ | Installs Python 3.12 and every Python package. |
| [Tailscale](https://tailscale.com/download) | ✓ | ✓ | The private network between the Studio and the Stage. Sign both machines in to the same account. |
| [NVIDIA driver](https://www.nvidia.com/drivers) with CUDA 13.0 or later | ✓ | | Stem separation on the GPU. `nvidia-smi` shows the CUDA version. |
| [FFmpeg](https://ffmpeg.org/download.html), on `PATH` | ✓ | | Audio decoding for the download and the separation. |
| [Deno](https://deno.com), on `PATH` | ✓ | | Solves YouTube's challenges for `yt-dlp`. |
| [Node.js](https://nodejs.org) 24, with npm | | ✓ | Builds the TV screens (CI uses Node 24). |
| [REAPER](https://www.reaper.fm), in its default folder | | ✓ | Plays the stems and runs the microphones' effects. |
| The audio interface's ASIO driver ([ASIO4ALL](https://asio4all.org) only for an interface without one) | | ✓ | Low latency monitoring of the voice. |
| Microsoft Edge (ships with Windows) | | ✓ | The full screen browser on the TV. |

The public page for phones is optional and needs two accounts: [Vercel](https://vercel.com) and [Upstash](https://upstash.com) (a Redis database).

## How to run

The steps below use `C:\path\to\karaoke-night` for the clone and `C:\path\to\studio-token.txt` for the token file; replace them with real paths. Keep the token file outside the clone, or name it `*.local.*` inside it, which git ignores.

### 1. Set up the Studio (once)

In a PowerShell:

```powershell
git clone https://github.com/FabioFischer/karaoke-night.git C:\path\to\karaoke-night
cd C:\path\to\karaoke-night
uv sync --package studio --extra separation   # more than 2 GB the first time
```

Never run `uv sync --all-packages` on the Studio: it removes the separation packages. Then create the token, a long random secret both machines share:

```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
($bytes | ForEach-Object { $_.ToString('x2') }) -join '' | Set-Content -NoNewline C:\path\to\studio-token.txt
```

`tailscale ip -4` prints the Studio's address on the private network: the Stage reaches the Studio at `http://<that address>:8790`.

### 2. Set up the Stage (once)

1. Clone and build, in a PowerShell:

   ```powershell
   git clone https://github.com/FabioFischer/karaoke-night.git C:\path\to\karaoke-night
   cd C:\path\to\karaoke-night
   uv sync --package stage
   cd web
   npm ci
   npm run build
   ```

2. Copy the Studio's token file to the Stage.
3. Set up REAPER with the [setup checklist](reaper/README.md#setup-checklist): the web interface on port 8080, the listener started with REAPER, the audio device at 64 samples, and the session `reaper/template/karaoke.RPP` saved under another name outside the clone.
4. Create the desktop shortcut that opens everything, as in step 5 of [`docs/NIGHT.md`](docs/NIGHT.md#before-at-home), pointing at the session saved in step 3.

### 3. Run a night

1. **Studio:** in an administrator PowerShell, or over SSH, start the Studio API. It opens its port to the Tailscale network only and keeps running after the window closes:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\karaoke-night\scripts\studio-start.ps1 -TokenFile C:\path\to\studio-token.txt
   ```

2. **Stage, optional:** copy every song the Studio already processed, so they play without the network. In a PowerShell in the clone:

   ```powershell
   $env:STUDIO_TOKEN = (Get-Content -Raw C:\path\to\studio-token.txt).Trim()
   uv run stage sync --studio http://<studio address>:8790
   ```

3. **Stage:** open the desktop shortcut. It opens REAPER with the session, starts the Stage server in its own window and opens Edge in full screen at `/karaoke`. Mirror the screen to the TV (`Windows + P`, *Duplicate*).
4. **Sing:** search for a song and add it to the playlist. A new song is processed on the Studio first (a few minutes) and then downloaded; a song of the library is ready at once. *Ir para o palco* opens the setup: choose the stems to mute and the key, try them with *Ouvir um trecho*, then *Começar*.
5. **After:** close Edge and the Stage server's window. On the Studio, in an administrator PowerShell, stop the API and close its port:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\karaoke-night\scripts\studio-stop.ps1
   ```

Without the Studio, the Stage still plays every song already in its library; only new songs need it. [`docs/NIGHT.md`](docs/NIGHT.md) is the full runbook, and [`CLAUDE.md`](CLAUDE.md#commands) lists every command and its options, including running `studio serve` and `stage serve` by hand.

### 4. Public page for phones (optional)

1. Create a Redis database on Upstash and note its REST URL and token.
2. Import this repository on Vercel as a project whose root directory is `web/`. In its Production environment, set `KARAOKE_STORE_URL` and `KARAOKE_STORE_TOKEN` to the REST URL and token. Every merge on `main` deploys it.
3. On the Stage, write a store file outside the clone: the REST URL on its first line and the token on its second. Add `-StoreFile <that file>` to the shortcut.

While the Stage server runs, anyone with the link `https://<your Vercel domain>/karaoke` sees the playlist, searches and adds songs, and follows the lyrics.

## Roadmap

- **Alpha:**
  - song search and stem separation
  - stems and key chosen at setup
  - line-synced lyrics
  - playback through REAPER with live vocal effects
  - a public page for phones: the playlist, search, adding songs and live lyrics
- **Beta:**
  - pause, volumes and playlist reordering, on the TV and on phones (done)
  - pre-performance excerpt to try the key, the stems and the volumes before singing (done)
  - two singers with roles (lead or backing), and lyrics split the same way
  - melody analysis: pitch correction that follows the song's key, and scoring against the original melody
  - joining the public page with a QR code on the TV
  - recorded performances with share links
  - song suggestions and playlist import

See [docs/PLAN.md](docs/PLAN.md) for the decisions, architecture, contracts and milestones.

## Tech stack

Python 3.12 (uv, FastAPI) · audio-separator (RoFormer, Demucs) · yt-dlp · ytmusicapi · LRCLIB · REAPER + Lua · TypeScript + React + Vite · Vercel + Upstash Redis · Tailscale

## Legal note

This project is meant for private use and does not distribute any audio. You are responsible for following copyright law and the terms of service of any platform you get audio from.

## License

[MIT](LICENSE). The license covers the code only. This repository contains no audio.
