# CLAUDE.md

Guidance for AI coding agents working in this repository.

## Start here

Read [docs/PLAN.md](docs/PLAN.md) first. It holds the decisions, architecture, contracts and milestones, and its **Status** section says where the work stopped and what comes next.

## Rules

- **English only.** Everything committed is in English: code, comments, docs, commit messages and PR descriptions.
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
| Lint | `uv run ruff check` |
| Format | `uv run ruff format` (CI runs `uv run ruff format --check`) |
| Set up web | `npm ci` |
| Web build (tsc, then Vite) | `npm run build` |
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

CI (`.github/workflows/ci.yml`) runs `uv sync --all-packages --locked`, the lint, format check and tests, then `npm ci` and `npm run build`. After changing dependencies, commit the updated `uv.lock` or `web/package-lock.json`.
