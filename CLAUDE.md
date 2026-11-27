# CLAUDE.md

Guidance for AI coding agents working in this repository.

## Start here

Read [docs/PLAN.md](docs/PLAN.md) first. It holds the decisions, architecture, contracts and milestones, and its **Status** section says where the work stopped and what comes next.

## Rules

- **English only.** Everything committed is in English: code, comments, docs, commit messages and PR descriptions.
- **One exception: the screens speak Brazilian Portuguese,** since the singers read it. Every string the web shows lives in `web/src/text.ts`, and nowhere else; the web's code and comments stay in English.
- **No personal information.** Never commit anything about the maintainer's own hardware, people, venues or song preferences. Describe requirements generically, and use placeholder song data in examples and tests.
- **Personal data stays local.** Personal data that work needs, such as the test song list, lives in gitignored `*.local.*` files (for example `songs.local.txt` at the repository root).
- **No audio or model weights.** Never commit source audio, stems, recordings or model weights.
- **The agent writes, the maintainer reviews.** Work on a branch, one PR per milestone or spike, small and reviewable. Never push to `main`.
- **Keep the plan current.** When a decision changes, or a milestone or spike finishes, update `docs/PLAN.md` (including its Status section) in the same PR.

## Commands

The layout is in `docs/PLAN.md` §5.5. Python commands run from the repository root, web commands from `web/`.

| Task | Command |
|---|---|
| Set up Python (uv workspace, Python 3.12) | `uv sync --all-packages` |
| Test | `uv run pytest` |
| Web test (Vitest, public functions and phone logic) | `npm test` |
| Lint | `uv run ruff check` |
| Format | `uv run ruff format` (CI runs `uv run ruff format --check`) |
| Set up web | `npm ci` |
| Web build (tsc, then Vite) | `npm run build` |
| Public web build, for Vercel | `npm run build:public` |
| Web dev server | `npm run dev` |

### Studio pipeline

The separation stack (audio-separator, CUDA 13.0 PyTorch, yt-dlp) is an optional extra of the `studio` package, installed only on the Studio machine. On the Studio, set up with `uv sync --package studio --extra separation`, never `uv sync --all-packages`, which is an exact sync and uninstalls the extra. Steps:

1. NVIDIA driver with CUDA 13.0 or later (`nvidia-smi`), `ffmpeg` and `deno` on `PATH` (deno solves YouTube's challenges for yt-dlp).
2. `uv sync --package studio --extra separation` (more than 2 GB the first time).
3. `uv run studio process <url|video_id|file> [--library DIR] [--model-dir DIR]`. A local file also needs `--title` and `--artist`. The default library is `library/` in the current folder, which git ignores. Models download on first use to `--model-dir` (default `~/.cache/audio-separator-models`); the `AUDIO_SEPARATOR_MODEL_DIR` environment variable wins over it, and that folder must exist.

The command prints each step's time and peak VRAM, then the null test (largest error and residual RMS of the stems against the mix). `uv run studio process --help` lists the options.

### Studio API

`studio serve` runs the Studio API (`docs/PLAN.md` §5.3) with the same setup as the pipeline. Every request must send the token from the `STUDIO_TOKEN` environment variable as `Authorization: Bearer <token>`, and the command refuses to start without it. In PowerShell:

```powershell
$env:STUDIO_TOKEN = "<a long random token>"
uv run studio serve [--library DIR] [--model-dir DIR] [--host 0.0.0.0] [--port 8790]
```

`--library` and `--model-dir` work as in `studio process`. Jobs run one at a time and are lost when the server stops; the packages stay in the library. `uv run studio serve --help` lists the options.

On start, before it binds the port, `studio serve` removes the folders a stopped run left in the library: builds named `.<song_id>.<random>.tmp` and deletions named `.<song_id>.<random>.deleting`, where `<random>` is exactly 8 characters of `a-z`, `0-9` and `_`. It logs each folder it removes, and each one it could not fully remove. It cannot tell a leftover from a build still running, so never run two `studio serve`, or a `studio serve` and a `studio process`, on the same library at once: the build in flight is removed, even by a second `studio serve` that then fails to start because the port is in use.

The `studio.*` log lines of `studio serve` go to stderr with time and level, next to uvicorn's own lines.

### Stage library

The Stage needs neither the separation extra nor the Studio stack: on the Stage machine, `uv sync --package stage` is enough. The library is the folder of packages (`docs/PLAN.md` §5.6), `library/` in the current folder by default, as for `studio`.

```powershell
$env:STUDIO_TOKEN = "<the token studio serve was started with>"
uv run stage sync --studio http://<studio address>:8790 [--library DIR]
uv run stage list [--library DIR]
```

`stage sync` mirrors the Studio library: it downloads what is missing or reprocessed, resumes interrupted downloads from `.<song_id>.partial` folders, checks each stem's sha256, and removes what the Studio no longer lists. It refuses to run without `STUDIO_TOKEN`. It prints one line per song acted on (`added`, `updated`, `removed`, `busy`, `failed`) and a summary line, and exits with 1 if the Studio cannot be reached (nothing changes then) or any song failed. A song whose files are open (REAPER holding the loaded song) is reported `busy` and left whole. `stage list` needs no network and no token.

### Searching for a song

```powershell
uv run stage search "<text>" [--library DIR]
```

`stage search` lists the library songs whose title or artist holds every word of the text (case and accents ignored), then up to 10 YouTube Music results (`ytmusicapi`, songs only). When YouTube Music fails or finds nothing, the online part comes from a `yt-dlp` search of YouTube instead, with the channel as the artist and no album. A result already in the library shows once, as the library song. Each line holds `ready` or `new`, the title, the artist, the album, the duration and the `video_id` (`-` when missing), plus the `song_id` of a ready song. Without internet it lists the library matches and the line `could not search online; only songs already in the library are listed`. Text without words prints `no results for '<text>'` and searches nothing online. It exits with 0 whenever the library could be read. It needs no token.

### Playing a song in REAPER

REAPER is the player (`docs/PLAN.md` §5.4). Its one-time setup on the Stage (web interface on port 8080, the listener started from REAPER's `Scripts/__startup.lua`, stop at the end of the project, the session template `reaper/template/karaoke.RPP`) is the checklist in `reaper/README.md`. Then:

```powershell
uv run stage play <song_id> [--library DIR] [--mute STEM ...] [--transpose N]
```

`stage play` loads the song in REAPER, mutes the stems given and unmutes the others, shifts every stem except drums by `--transpose` semitones (0 by default), plays from the start and prints the position once per second until the song ends (exit 0). Ctrl+C stops playback and exits 130. It exits with 1 when the song is not in the library, REAPER cannot be reached or the listener does not answer (restart it then, as the error says). There is no Lua interpreter in CI: the listener and the template are proven only in a live REAPER.

### Stage server

`stage serve` runs the Stage API behind the TV screens (`docs/PLAN.md` §5.8) and, once `web/` is built (`npm run build`), the screens themselves. Run it from the repository root, with REAPER set up as above:

```powershell
$env:STUDIO_TOKEN = "<the token studio serve was started with>"
uv run stage serve --studio http://<studio address>:8790 [--library DIR] [--port 8791] [--web DIR]
```

It listens on `127.0.0.1` only, port 8791 by default, and serves the built web from `--web` (default `web/dist` in the current folder), or only the API when that folder holds no `index.html`, which it says on stderr. Without `--studio` or `STUDIO_TOKEN` it still serves and says so: songs of the library play, and a new song added to the playlist fails with the reason. On start it reads the music and voice volumes from REAPER (the `Music` and `Mic 1` tracks); when REAPER does not answer yet, it says so on stderr and reads them when a song starts. The playlist lives in memory and is lost when the server stops. The playlist worker downloads new songs into the library, so never run `stage serve` and `stage sync` on the same library at once (nothing enforces it). `uv run stage serve --help` lists the options.

With the public store's Upstash REST URL and token in `KARAOKE_STORE_URL` and `KARAOKE_STORE_TOKEN`, `stage serve` also feeds the public page (`docs/PLAN.md` §5.9, contract between the Stage and Vercel): on start it deletes the requests left in the store, then publishes the snapshot (on every change, checked every 0.5 s, and at least every 3 s) and the library (when it changes, checked every 3 s), and applies the public requests about once a second, its searches in a pool of 2 threads of their own; a request taken more than 15 s after its `at_ms` is answered `expired` and not applied. Nothing is published before the store's clock is known (Redis `TIME`, learned again every minute). Without both variables it says so on stderr and publishes nothing. A store that fails or does not answer within 5 s is logged once (`warning: the public store failed (...); trying again on every cycle`), and again only after `warning: the public store answers again`; playback and the local screens never wait on it. The tests reach no store: `FakeStore` in `packages/stage/tests/conftest.py` stands in for Upstash.

The screens are `/karaoke` (home), `/karaoke/stage` (setup, then the song playing) and `/karaoke/stage/results`; `/` goes to `/karaoke`. To work on them, run `stage serve` on its default port and `npm run dev` in `web/`: the Vite dev server sends `/api` to `http://127.0.0.1:8791`. The TV screens have no tests of their own: their logic lives in the Stage API and its tests, and they are proven by `npm run build` and in a browser. `npm test` (Vitest, below) covers only the public page's functions, the phone's logic and the reordering rule the TV shares with it.

### Night scripts

`scripts/` holds the PowerShell scripts of a night (Windows PowerShell 5.1); `docs/NIGHT.md` is the runbook that uses them. Each runs as `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\<name>.ps1 ...`, finds the clone from its own location, and runs the clone's `.venv\Scripts\stage.exe` or `studio.exe` directly, not `uv run`. A token file holds the Studio token, and a store file the public store's REST URL (first line) and token (second line); the scripts never print them or put them on a command line.

```powershell
scripts\stage-night.ps1 -Project <session.RPP> [-Studio URL] [-TokenFile FILE] [-StoreFile FILE] [-Library DIR] [-Port 8791] [-ReaperPath EXE] [-EdgePath EXE]
scripts\studio-start.ps1 -TokenFile FILE [-Library DIR] [-ModelDir DIR] [-Port 8790] [-LogFile FILE]
scripts\studio-stop.ps1
```

- `stage-night.ps1`, the target of the Stage's desktop shortcut: opens REAPER with the session unless a REAPER is already running (two would mean two listeners), starts `stage serve` in its own console window (closing it stops the server), waits up to 30 s for `/api/state`, then opens Edge in full screen at `/karaoke` as an app window, with its own profile under `%LOCALAPPDATA%\karaoke-night\edge`. The library defaults to `library` in the clone. `STUDIO_TOKEN`, `KARAOKE_STORE_URL` and `KARAOKE_STORE_TOKEN` are set only for the `stage serve` child and removed before Edge starts; a store file missing a line leaves its variable unset, and `stage serve` then publishes nothing. On a failure it prints the reason and waits for Enter, so the shortcut's window does not close with it.
- `studio-start.ps1`, on the Studio in an administrator PowerShell or over SSH (both Studio scripts refuse to run without elevation, which would hide the command lines of elevated processes): does nothing when a `studio serve` of this clone already runs (a second one would remove the first one's builds in flight, see Studio API above). Otherwise it creates the firewall rule `karaoke-studio` when missing (inbound TCP on `-Port`, interface `Tailscale`, remote `100.64.0.0/10`) and starts `studio serve` detached with `Win32_Process.Create`, so it survives the end of an SSH session, its output appended to `-LogFile` (default `studio-serve.log` in the clone, which git ignores). `--library` and `--model-dir` go to `studio serve` only when given, so its defaults apply relative to the clone. When the server stops within 5 s (no token, the separation extra missing, the port in use), it says so with the end of the log and exits 1; the firewall rule stays.
- `studio-stop.ps1`: stops every process whose command line holds this clone's `.venv\Scripts\studio.exe` followed by `serve` (the wrapper, `studio.exe` and its Python processes), then removes the rule. It prints what it stopped, and nothing when nothing runs.

None of them has a test harness: they are proven by parsing and in a live run.

### Public page on Vercel

The public page (`docs/PLAN.md` §5.9) is a Vercel project whose root directory is `web/`; `web/vercel.json` sets its build and routes. `npm run build:public` (`vite build --mode public`) builds only the phone's page, `web/public.html` with `src/public/`, into `web/dist-public`: one route, `/karaoke`, with the tabs *Agora*, *Fila* and *Buscar*. The operator's screens are not in it. `npm run build` stays the Stage's own build, served by `stage serve`.

The functions are the files of `web/api/public/` (Node runtime, Web standard handlers); `web/server/` holds what they share, outside `api/` so it is not a function, and `web/server/store.ts` is the only code that reads the store's credentials, `KARAOKE_STORE_URL` and `KARAOKE_STORE_TOKEN` (the Upstash REST URL and token, the same names the Stage uses). They are set by hand in the Vercel project's Production environment only, so a preview never touches the live page, and never committed. `tsc -b` checks the functions with `tsconfig.api.json`; their relative imports name the `.js` file, as Node ESM needs.

`npm test` runs Vitest on `web/test/`, in Node, with no DOM and no network:

- the five functions of `web/api/public/`, called as Vercel calls them, against a fake Upstash (`web/test/fake-upstash.ts`, a stubbed `fetch`), never the real store;
- the phone's logic without a screen: the requests and their answers (`src/public/public-api.ts`, with a stubbed `fetch` and fake timers: the polls, the give up after 15 s, the 5 s timeout of each call), the clock rules (`src/public/phone-clock.ts`: offset, position, the position held while paused, offline) and the phone's error messages;
- the playlist's reordering rule (`moveOptions` in `src/session.ts`: which entries move and which way), which the TV shares with the phone.

Not tested: every screen, the TV's and the phone's alike (proven by both builds and in a browser), and the Vercel platform itself (`web/vercel.json`, the routing of `api/` and the edge cache), which only a deployment shows. Tests live outside `api/`, where every file would become a function. `tsc -b` checks them with `tsconfig.test.json`.

CI (`.github/workflows/ci.yml`) runs `uv sync --all-packages --locked`, the lint, format check and tests, then `npm ci`, `npm test`, `npm run build` and `npm run build:public`. After changing dependencies, commit the updated `uv.lock` or `web/package-lock.json`.
