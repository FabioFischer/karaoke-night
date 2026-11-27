# karaoke-night

Self-hosted karaoke that plays the **original recordings**, not re-recorded backing tracks. Songs are split into stems with AI source separation, singers choose which stems to keep, and live vocals run through a real effects chain in [REAPER](https://www.reaper.fm).

> **Status:** the alpha (Phase 1) is built and waits for its first full night at the venue. The beta (Phase 2) has started: pause, volumes, reordering and the pre-performance excerpt are done.

## How it works

- **Studio:** a GPU machine that finds a song, downloads the audio and splits it into stems (lead vocals, backing vocals, drums, bass, guitar, piano, other). It also fetches synced lyrics and packages everything.
- **Stage:** the machine at the venue. It runs REAPER with the microphones and effects, shows search and lyrics on a TV, and plays songs from its local library. Once songs are synced, it works offline.
- **Public page:** a page on Vercel where guests follow the playlist and the lyrics on their phones, and search and add songs. The Stage publishes its state there and applies the guests' requests; nothing on the internet connects to the Stage or the Studio.

```
Studio (GPU) ──Tailscale──► Stage ──► REAPER ──► speakers
                              ├─────► browser on the TV (search, stems, lyrics)
                              └─────► public page on Vercel ◄── phones
```

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
