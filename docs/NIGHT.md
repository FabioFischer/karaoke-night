# Karaoke night: runbook

What to do before, during and after a night, with the scripts in `scripts/`. The roles are those of `docs/PLAN.md` §3: the **Studio** processes new songs, the **Stage** plays them at the venue. The commands below assume the clone is at `C:\path\to\karaoke-night` on both machines; replace it with the real path.

Windows does not run `.ps1` files by default, so each script runs as `powershell -NoProfile -ExecutionPolicy Bypass -File <script> ...`, which works in PowerShell, in `cmd` and over SSH.

The Studio token is kept in a file on each machine, the same token on both: outside the clone, or in a `*.local.*` file in its root, which git ignores. No script prints it or puts it on a command line.

## Before, at home

On the **Studio**, in an administrator PowerShell or over SSH:

1. Update the clone (`git pull`) and its environment: `uv sync --package studio --extra separation`, never `uv sync --all-packages` (`CLAUDE.md`, Studio pipeline).
2. Start the Studio API for the event:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\karaoke-night\scripts\studio-start.ps1 -TokenFile C:\path\to\studio-token.txt
   ```

   It creates the firewall rule `karaoke-studio` (inbound TCP on the port, Tailscale only) when it is missing, then starts `studio serve` detached, so it keeps running after the SSH session ends. It prints the process id and the log file (`studio-serve.log` in the clone unless `-LogFile` says otherwise). If the server stops within its first seconds (a token file that is empty, the separation extra missing, the port in use), it says so with the end of the log and exits 1. Run again while the server runs, it starts nothing. Outside an elevated session it refuses to run, and so does `studio-stop`.

On the **Stage**, in a PowerShell in the clone:

3. Update the clone and its environment: `git pull`, `uv sync --package stage`, then in `web/`: `npm ci` and `npm run build`.
4. Mirror the Studio library, so every song already processed plays without the network:

   ```powershell
   $env:STUDIO_TOKEN = (Get-Content -Raw C:\path\to\studio-token.txt).Trim()
   uv run stage sync --studio http://<studio address>:8790
   ```

5. Create the desktop shortcut, once per Stage machine (again only if a path changes). `-Project` is the REAPER session saved outside the clone (`reaper/README.md`, checklist step 6):

   ```powershell
   $clone = 'C:\path\to\karaoke-night'
   $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$clone\scripts\stage-night.ps1`"" +
       " -Project `"C:\path\to\karaoke-session.RPP`"" +
       " -Studio http://<studio address>:8790" +
       " -TokenFile `"C:\path\to\studio-token.txt`""
   $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut(
       (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Karaoke night.lnk'))
   $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
   $shortcut.Arguments = $arguments
   $shortcut.WorkingDirectory = $clone
   $shortcut.Save()
   ```

   Without `-Studio` and `-TokenFile`, the night still runs, with the songs of the library only.

## At the venue

1. Go through the setup checklist of [`reaper/README.md`](../reaper/README.md#setup-checklist) before opening anything: the audio interface straight into the machine, the Windows sample rate matching REAPER's, a single monitoring path, the machine plugged in.
2. Bring Tailscale up on the Stage and check that it reaches the Studio (`tailscale status`). New songs need it; the library does not.
3. Open the shortcut. It opens REAPER with the session (a REAPER already running is left as it is, with the session it has open), starts `stage serve` in its own window, waits until it answers, and opens the browser in full screen at `/karaoke`. F11 leaves full screen. If something fails, the shortcut's window says what and waits for Enter.
4. Mirror the Stage's screen to the TV (`Windows + P`, *Duplicate*).
5. **Measure once:** the download of a new song on the venue's network, still open from S5 (`docs/PLAN.md` §6). Add a song that is not in the library, and time its entry from the start of `fetching` (the download to the Stage) to `ready`; note the size of its package folder in the library. Record both in the A9 results of `docs/PLAN.md`.

## During

- Never run `stage sync` while `stage serve` runs: both write to the library, and nothing stops them from doing it at once.
- A new song needs the Studio up and reachable: `studio serve` running and Tailscale connected on both machines. Songs in the library play without either.
- The playlist lives in `stage serve`'s memory: closing its window stops the server and empties the playlist.
- If the screen says the karaoke script in REAPER did not answer, restart REAPER (`reaper/README.md`, How the bridge works).

## After

1. On the Stage, close the browser and the `stage serve` window.
2. On the Studio, in an administrator PowerShell or over SSH, stop the Studio API and remove its firewall rule:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\karaoke-night\scripts\studio-stop.ps1
   ```

   It prints each process it stopped, and nothing when the server was not running.
