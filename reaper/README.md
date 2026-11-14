# REAPER setup

REAPER is the player on the Stage (`docs/PLAN.md` §3 and §5.4). The Stage drives it through REAPER's built-in web interface and a Lua listener that REAPER starts when it opens. This folder holds both REAPER sides of that bridge:

| File | What it is |
|---|---|
| `scripts/karaoke_listener.lua` | The listener. It runs `load`, `transpose`, `ping` and `quit`, sent by the Stage through ExtState. |
| `scripts/lib/json.lua` | [rxi/json.lua](https://github.com/rxi/json.lua) v0.1.2 (MIT, notice in the file header), vendored unmodified. |
| `template/karaoke.RPP` | The session: 44.1 kHz, the tracks `Mic 1` and `Mic 2` (mono inputs 1 and 2, armed, input monitoring on), then the folder track `Music` at -8 dB holding `Lead`, `Backing`, `Drums`, `Bass`, `Guitar`, `Piano` and `Other` (not armed, input monitoring off). No effects. |

## Setup checklist

Once per Stage machine, then check it again before each night.

1. **Audio interface straight into the machine,** not through a USB hub: on a hub the hiss goes up and the volume down (S1). Keep it on the same USB port. On another port Windows sees a new device, with its default sample rate and ASIO4ALL's default buffer (512 samples), so both have to be set again.
2. **Windows sample rate matching REAPER's 44.1 kHz:** in Windows' sound settings, set the interface's input and output format to 44100 Hz. Otherwise ASIO4ALL resamples, which sounds robotic.
3. **Web interface on port 8080:** *Options > Preferences > Control/OSC/web > Add*, *Control surface mode* **Web browser interface**, port **8080**, no username or password. *OK*, then open `http://127.0.0.1:8080` in a browser: REAPER's remote page loads. The Stage always uses `127.0.0.1`, never `localhost`: the web interface listens on IPv4 only, and on Windows `localhost` tries IPv6 first, so every request would wait about 2 s.
4. **Stop at the end of the project:** *Options > Preferences > Audio > Playback*, turn on *Stop/repeat playback at end of project*. The Stage takes the stop at the end as the end of the song. A stop by hand in REAPER ends it too; a pause does not, and the song goes on when playback resumes.
5. **Start the listener with REAPER:** *Options > Show REAPER resource path in explorer/finder* opens REAPER's resource folder. In its `Scripts` folder, create `__startup.lua` (or open the one already there) and add this one line, with the full path of this repository's clone:

   ```lua
   dofile([[C:\path\to\karaoke-night\reaper\scripts\karaoke_listener.lua]])
   ```

   Restart REAPER. The listener now runs whenever REAPER is open. Check it with `uv run stage play` on a song of the library (next section): a listener that is not running makes the command fail with "the karaoke listener in REAPER did not answer". Errors from the script appear in REAPER's *ReaScript console output* window.
6. **Open the session:** *File > Open project*, `reaper/template/karaoke.RPP`. Choose the interface's inputs if they are not inputs 1 and 2. To keep an effects chain or other changes, save the session under another name, outside the clone (*File > Save project as*), and open that one on later nights: loading songs changes the session, and the template in the clone stays as it is.
7. **A single monitoring path:** the voice is monitored by REAPER's input monitoring on `Mic 1` and `Mic 2` only. Turn off the interface's direct monitoring and any other path (such as Windows' *Listen to this device*): two paths sound doubled and robotic (S1).

Load a song and play it from a terminal on the Stage, in the repository root:

```powershell
uv run stage play <song_id> [--library DIR] [--mute STEM ...] [--transpose N]
```

## How the bridge works

- **Transport, tracks and mute** go straight to the web interface: `TRANSPORT`, `TRACK`, `SET/TRACK/<i>/MUTE/<0|1>`, and actions `40042` (go to the start), `1007` (play) and `1016` (stop). Track indexes come from `TRACK`, where index 0 is the master, so none are hard-coded: tracks are found by name, case aside.
- **Everything else** goes to the listener: the Stage sets `SET/EXTSTATE/karaoke/cmd/<percent-encoded JSON>`, the listener runs the command and writes its reply to `karaoke/ack`, and the Stage reads it back with `GET/EXTSTATE/karaoke/ack` until the `id` matches. One command at a time.
- **`load`** first stops playback (`1016`). The listener then finds the track of each stem by name (it creates the ones missing at the end of the track list, with input monitoring off), deletes the items on those tracks only, and inserts each stem at the start with `gain_db` as item volume. Tracks with other names, such as the mic tracks, are never touched. Keep the timeline free of other items: one that runs past the song keeps playback going after it ends.
- **`transpose`** sets the pitch of every stem except drums, in semitones. It is absolute: `0` resets it.
- **If the listener stops answering** (S3 saw it once, while tracks were edited by hand), restart it: run `scripts/karaoke_listener.lua` from the action list, or restart REAPER.

## Effects chain

The effects chain on `Mic 1` and `Mic 2` is chosen by hand in REAPER; the app never picks plugins (`docs/PLAN.md` §3). Effects in the live path must add no latency of their own (§2).

Recommended settings: to come, after a guided test of pitch correction options.
