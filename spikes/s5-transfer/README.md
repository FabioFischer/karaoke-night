# S5: Transfer

Runbook for spike S5 in [docs/PLAN.md](../../docs/PLAN.md) §6. The Studio serves its song library over Tailscale; the Stage downloads a package from it, first on the home network and then from a remote network, simulated with a phone hotspot.

**What it answers:** how long a whole song package (§5.2: the manifest and its 7 stems) takes to go from the Studio to the Stage over Tailscale, whether the connection is direct or goes through a DERP relay, and whether every file arrives intact (its sha256 matches the manifest).

**Pass criterion:** a ~150 MB package downloads in under ~1 min from a remote network, that is **20 Mbit/s sustained** (150 MB in 60 s). The largest real package available is about 130 MB, so use that one and judge it by its Mbit/s, not by its time alone.

## Files

| File | What it is |
|---|---|
| `transfer.py` | The command line tool: `serve` on the Studio, `fetch` on the Stage. Python standard library only (3.11 or later). |
| `test_transfer.py` | Tests against a real server on `127.0.0.1` with a placeholder package: identical bytes, totals, throughput math, a hash mismatch reported without stopping the run, a path outside the library not served, the address the server listens on, and the record the CLI writes, including after a run that fails. No other network. |

Run the tests from the repo root:

```
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s spikes/s5-transfer -v
```

## How it works

- **`serve <library_dir> --bind <ip> [--port 8765]`** is Python's own static file server (`ThreadingHTTPServer` with `SimpleHTTPRequestHandler`) rooted at the library folder. It answers GET and HEAD only, and requests cannot reach files outside the folder. `--bind` is required, so it only listens on the address you name. It has **no token**: in this spike the tailnet, plus the firewall rule below, is the boundary. The token (§5.3) comes with the Studio API in milestone A4. Directory listing is on, so serving the whole `spikes/s2-separation/library` also exposes its `downloads/` folder (source audio) and `timings.json` to the tailnet. That is within this spike's scope, since the tailnet is the boundary, but keep the server up only for the runs.
- **`fetch <base_url> <song_id> [--runs N] [--out DIR] [--label TEXT] [--keep]`** runs N times. Each run gets `<base_url>/<song_id>/manifest.json`, then every stem file the manifest lists, one after the other, into a fresh folder under `--out` (default: the system temp folder). Each file is streamed to disk in 1 MiB chunks while its sha256 is computed. After the run the folder is deleted, unless `--keep`, also when the run fails.
- **Times** come from `time.perf_counter`. A file's time runs from its request to its last byte. The run's total time and total bytes include the manifest. Throughput is in MB/s, where **1 MB is 10^6 bytes**, and in Mbit/s (10^6 bits). Each file opens its own TCP connection (8 per run), each with its own handshake and slow start, so the times lean high: the measure is conservative.
- **Hashes.** Each file's sha256 is compared with the manifest. A mismatch is reported (per file, and `MISMATCH in <stems>` in the run line) and the run goes on; it does not stop the tool.
- Nothing more: no retries, no resume, no parallel downloads. A network error stops `fetch` with the Python error; the runs that finished before it are already in the results file.

`fetch` prints one block per run and the spread across runs:

```
run 1/3: 7 stems + manifest, 131.204 MB in 42.118 s, 3.12 MB/s, 24.9 Mbit/s, sha256 all match
  manifest manifest.json            0.001 MB    0.090 s
  lead     stems/lead.flac         18.441 MB    5.902 s     3.12 MB/s     25.0 Mbit/s  match
  ...
total seconds over 3 runs: min 40.551, median 42.118, max 47.930
```

(The numbers above are made up, to show the format.) It appends one record to `spikes/s5-transfer/results.local.json` (gitignored) and rewrites it as each run ends, so every finished run is on disk: UTC timestamp, label, base URL, song id, and every run with its per-file bytes, seconds, throughput and hashes.

## What you need

- Tailscale on the Studio and the Stage, on the same account (§6 prerequisites).
- Python 3.11 or later on both machines. Nothing to install.
- A real package on the Studio, for example one the S2 spike wrote under `spikes/s2-separation/library/<song_id>/`. The `<song_id>` is the package folder name. Pick the largest one.
- On the Stage, mobile data for the hotspot runs: **each run downloads the whole package**, so 3 runs of a 130 MB package cost about 400 MB.

## 1. Open the port on the Studio

In an admin PowerShell on the Studio, allow TCP 8765 in, on the Private profile, from Tailscale addresses only:

```
New-NetFirewallRule -DisplayName "karaoke-s5" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private -RemoteAddress 100.64.0.0/10
```

The rule only applies while the Tailscale adapter is on the Private profile; `Get-NetConnectionProfile` shows the profile of each adapter.

## 2. Start the server on the Studio

Find the Studio's Tailscale IP with `tailscale ip -4` and start the server bound to it, from the repo root:

```
python spikes/s5-transfer/transfer.py serve spikes/s2-separation/library --bind 100.x.y.z
```

It logs one line per request. Leave it running; Ctrl+C stops it.

## 3. Baseline on the home network (Stage)

With the Stage on the same network as the Studio, from the repo root:

```
tailscale ping <studio>
python spikes/s5-transfer/transfer.py fetch http://100.x.y.z:8765 <song_id> --runs 3 --label "home lan"
```

`tailscale ping` says whether the path is direct (`via <ip>:<port>`) or through a relay (`via DERP(<region>)`). The first ping or so usually goes over DERP while a direct path is found, and it stops once one is, so write down the **last** line for each network.

## 4. Remote network (Stage on a phone hotspot)

1. Switch the Stage's network to a phone hotspot (turn off its home Wi-Fi and cable, so it cannot fall back to them).
2. Check the path: `tailscale ping <studio>`. Note direct or DERP from the last line, and the latency it reports.
3. Run:

   ```
   python spikes/s5-transfer/transfer.py fetch http://100.x.y.z:8765 <song_id> --runs 3 --label "hotspot, direct"
   ```

   Put the path you saw in `--label` (`direct` or `DERP`), so the record says it.

## 5. Clean up on the Studio

Stop the server with Ctrl+C, then remove the firewall rule in an admin PowerShell:

```
Remove-NetFirewallRule -DisplayName "karaoke-s5"
```

## Recording the results

The raw records stay in the gitignored `results.local.json`. Only a summary goes into `docs/PLAN.md`: package size, the path per network (direct or DERP), the min, median and max total seconds per network, whether every hash matched, and the verdict against the ~1 min target.
