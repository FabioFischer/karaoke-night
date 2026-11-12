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

CI (`.github/workflows/ci.yml`) runs `uv sync --all-packages --locked`, the lint, format check and tests, then `npm ci` and `npm run build`. After changing dependencies, commit the updated `uv.lock` or `web/package-lock.json`.
