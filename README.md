# karaoke-night

Self-hosted karaoke that plays the **original recordings**, not re-recorded backing tracks. Songs are split into stems with AI source separation, singers choose which stems to keep, and live vocals run through a real effects chain in [REAPER](https://www.reaper.fm).

> **Status:** Phase 1 (alpha) in progress.

## How it works

- **Studio:** a GPU machine that finds a song, downloads the audio and splits it into stems (lead vocals, backing vocals, drums, bass, guitar, piano, other). It also fetches synced lyrics and packages everything.
- **Stage:** the machine at the venue. It runs REAPER with the microphones and effects, shows search and lyrics on a TV, and plays songs from its local library. Once songs are synced, it works offline.

```
Studio (GPU) ──Tailscale──► Stage ──► REAPER ──► speakers
                              └─────► browser on the TV (search, stems, lyrics)
```

## Roadmap

- **Alpha:**
  - song search and stem separation
  - stems toggled live, and key change at setup
  - line-synced lyrics
  - playback through REAPER with live vocal effects
- **Beta:**
  - two singers with roles (lead or backing), and lyrics split the same way
  - melody analysis: pitch correction that follows the song's key, and scoring against the original melody
  - pre-performance preview to adjust volumes, key and lyrics timing
  - phone companion page with live lyrics and a queue
  - recorded performances with share links
  - song suggestions and playlist import

See [docs/PLAN.md](docs/PLAN.md) for the decisions, architecture, contracts and milestones.

## Tech stack

Python 3.12 (uv, FastAPI) · audio-separator (RoFormer, Demucs) · yt-dlp · ytmusicapi · LRCLIB · REAPER + Lua · TypeScript + React + Vite · SQLite · Tailscale

## Legal note

This project is meant for private use and does not distribute any audio. You are responsible for following copyright law and the terms of service of any platform you get audio from.

## License

[MIT](LICENSE). The license covers the code only. This repository contains no audio.
