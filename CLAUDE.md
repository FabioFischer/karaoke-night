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

There is no code yet. Add the build, test and run commands here when milestone A1 lands. The planned layout is in `docs/PLAN.md` §5.5.
