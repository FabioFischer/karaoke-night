# S1: latency

Runbook for spike S1 in [docs/PLAN.md](../../docs/PLAN.md) §6. There is no code: everything happens in REAPER and LatencyMon on the Stage machine.

**Pass criterion:** a measured round trip of about 12 ms or less, and singing feels comfortable with pitch correction on. Otherwise, try plan B (step 8).

**Rounds:** run it once with the interface on hand, and again after any interface upgrade, to compare.

## What you need

- The Stage machine, plugged in.
- The audio interface, with its native ASIO driver installed. Install [ASIO4ALL](https://www.asio4all.org) too, to compare.
- A dynamic microphone and an XLR cable.
- Speakers wired to the interface output. Not Bluetooth, and not USB speakers: those are separate sound devices.
- [REAPER](https://www.reaper.fm) and [LatencyMon](https://www.resplendence.com/latencymon).
- Optional: a cable from an interface output to a free input, for an electrical loopback (step 4).

## 1. Prepare the machine

- Power plan set to *High performance*, with the machine plugged in.
- Close other apps. Keep Wi-Fi on, because the Stage needs the network on a real night.
- If the TV is connected over HDMI, mute it or disable the HDMI audio device in Windows.

## 2. LatencyMon

1. Run it for 10 minutes with the machine idle.
2. Run it again for 10 minutes while REAPER plays a song with the mic monitored (after step 3).
3. Write down the verdict, the highest DPC and ISR execution times, and the driver with the highest DPC.

If the verdict is red, find the culprit by disabling one device at a time (Wi-Fi, Bluetooth, webcam) and running it again. Write down what changed.

## 3. Set up REAPER

1. **Preferences → Audio → Device:** ASIO, the interface's driver, sample rate **44.1 kHz** (the same as the stems, so nothing gets resampled), and the buffer size under test.
2. **Preferences → Audio → Recording:** turn off *Use audio driver reported latency*. Recordings then show the raw round trip instead of hiding it. **Turn it back on after the spike.**
3. Write down the latency REAPER reports (shown at the right end of the menu bar, for example `~2.9/2.9ms`).
4. Create two tracks:
   - **Click:** an item from *Insert → Click source*, routed to the speakers.
   - **Mic:** input set to the mic, armed for recording, **monitoring off** while measuring (otherwise the click loops between the speaker and the mic).

## 4. Measure the round trip

**Acoustic loopback (default):**

1. Put the mic about 30 cm in front of the speaker, pointing at it.
2. Play the click item and record about 10 clicks on the Mic track.
3. Zoom in and measure the time from a click's start in the Click item to the same click's start in the recording. Set the time format to samples or milliseconds, and select the gap to read its length.
4. Subtract the time the sound spends in the air: about **0.9 ms at 30 cm** (distance ÷ 343 m/s).
5. Average three clicks.

**Electrical loopback (optional, more precise):** connect an output to a free input, record that input instead of the mic, and skip the air-time correction.

What you measure is input latency plus output latency, the same path a monitored voice takes.

## 5. Test matrix

For every driver (native ASIO, ASIO4ALL) and buffer (64, 128, 256 samples):

1. Measure the round trip (step 4).
2. Turn mic monitoring on, load the effects chain (step 6), and play a dense song for 5 minutes while singing or talking into the mic.
3. Listen for clicks or dropouts, and check *View → Performance Meter* for CPU.

Skip the smaller buffers once one of them crackles.

## 6. Effects chain

Build a test chain on the Mic track, roughly in this order:

- low cut (EQ), and a gate;
- a compressor **without look-ahead** (in ReaComp, *pre-comp* at 0);
- pitch correction in **low-latency, chromatic** mode;
- a doubler (a short delay with a slight detune);
- reverb and delay.

The plugins are up to you. ReaPlugs ship with REAPER, and there are free third-party options for pitch correction.

**Check each plugin's reported delay (PDC) in the FX chain window: it must be 0.** On a monitored track, any plugin delay adds directly to what the singer hears. Replace plugins that report delay.

## 7. Singing test

At the smallest buffer that was stable in step 5:

1. Sing 2–3 songs with the chain on, then with it bypassed. Any audio file in REAPER works as the backing.
2. Rate the comfort from 1 to 5 for each.
3. Write down anything that felt late, sounded hollow or phasey (your own voice through the air and the delayed one together), or sounded robotic.

## 8. Plan B (only if the criterion fails)

1. Turn on the interface's direct monitoring, if it has one, so the dry voice skips the computer.
2. On the Mic track, keep only reverb and delay at 100% wet, with the dry signal muted.
3. Repeat step 7 and compare.

## Recording the results

Results name the hardware, so they stay out of git. Copy the template into `spikes/s1-latency/results.local.md`, which is gitignored. Only a generic summary goes into `docs/PLAN.md` (for example "about 8 ms at 128 samples with the native driver: pass").

```markdown
# S1 results: round <n>, <date>

Interface / driver version:
Machine (CPU, RAM, Windows build):

## LatencyMon
| Run | Verdict | Highest DPC (µs) | Highest ISR (µs) | Worst driver |
|---|---|---|---|---|
| Idle | | | | |
| REAPER playing | | | | |

## Round trip
| Driver | Buffer | Reported (ms) | Measured (ms) | Crackles in 5 min? | CPU |
|---|---|---|---|---|---|
| Native ASIO | 64 | | | | |
| Native ASIO | 128 | | | | |
| Native ASIO | 256 | | | | |
| ASIO4ALL | 64 | | | | |
| ASIO4ALL | 128 | | | | |
| ASIO4ALL | 256 | | | | |

## Effects chain
| Plugin | Role | PDC (samples) |
|---|---|---|

## Singing test
| Setup | Comfort (1–5) | Notes |
|---|---|---|
| Chain on | | |
| Chain bypassed | | |
| Plan B | | |

## Verdict
Pass / fail, and why:
```

On the second round, also check two mics monitored at the same time.
