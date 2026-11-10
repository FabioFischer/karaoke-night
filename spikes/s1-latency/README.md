# S1: latency

Runbook for spike S1 in [docs/PLAN.md](../../docs/PLAN.md) §6. It happens in REAPER and LatencyMon on the Stage machine. The only code is `make_click.py`, which writes the click file used to measure.

**What it answers:** can the live voice go through the computer (mic → interface → REAPER effects → speakers) and still feel comfortable to sing with? If yes, pitch correction and the rest of the chain stay in the live path. If not, plan B (step 8). Fine-tuning the buffer size is not the goal.

**Pass criterion:** singing feels comfortable with pitch correction on. A round trip of about 12 ms is the usual reference, but comfort decides.

**Rounds:** run it once with the interface on hand, and again after any interface upgrade, to compare.

## What you need

- The Stage machine, plugged in.
- The audio interface with its native ASIO driver. If it has none (some entry-level interfaces only point to ASIO4ALL), use ASIO4ALL and compare it with REAPER's WASAPI exclusive mode.
- A dynamic microphone and an XLR cable.
- Speakers wired to the interface output. Not Bluetooth, and not USB speakers: those are separate sound devices.
- [REAPER](https://www.reaper.fm) and [LatencyMon](https://www.resplendence.com/latencymon).
- The click file: run `python spikes/s1-latency/make_click.py` to write `spikes/s1-latency/click.wav` (11 s with a click at exactly 1 s, 2 s … 10 s).

## 1. Prepare the machine

- Windows power mode set to *Best performance* (or the *High performance* plan where it exists), with the machine plugged in.
- Close other apps, especially anything that plays sound. Keep Wi-Fi on, because the Stage needs the network on a real night.
- If the TV is connected over HDMI, mute it or disable the HDMI audio device in Windows.

## 2. LatencyMon

1. Run it as administrator for 10 minutes with the machine idle.
2. Write down the verdict, the highest DPC and ISR execution times, and the driver with the highest DPC (the *Drivers* tab, sorted by highest execution).

If the verdict is red, find the culprit by disabling one device at a time (Wi-Fi, Bluetooth, webcam) and running it again.

## 3. Set up REAPER

**Audio device** (*Ctrl+P → Audio → Device*):

1. *Audio system*: ASIO, with the interface's driver (or *ASIO4ALL v2*).
2. With ASIO4ALL, click *ASIO Configuration…*, then the wrench icon for the advanced view:
   - leave **only the interface** switched on, for input and output. Switch off the built-in sound card and any monitor audio;
   - set *ASIO Buffer Size* to **128**.
3. Check *Enable inputs*, input range *In 1* to *In 2*, output range *Out 1* to *Out 2*.
4. Sample rate **44.1 kHz**, the same as the stems.
5. **Audio → Recording:** turn off *Use audio driver reported latency*. Otherwise REAPER moves recordings back by the reported latency and the measurement reads close to zero. **Turn it back on after the spike.**
6. Write down the latency REAPER reports at the right end of the menu bar, for example `128spls ~6.4/13ms ASIO` (input/output). It should also say `2/2ch`. `2/1ch` means only one output channel is active.

**Project:**

1. *File → New project*.
2. *View → Time unit for ruler → Minutes:Seconds*, so positions read like `0:01.012`.
3. Switch off snapping (the magnet icon), so the cursor doesn't jump to round positions.
4. **Click track:** press *Home*, then *Insert*, and pick `click.wav`. Select the item and press *F2*: *Position* must be `0:00.000`. Play it and set a normal listening volume.
5. **Mic track:** *Ctrl+T*, name it *Mic*, arm it (red button), and set its input to *Mono → Input 1*. **Monitoring off** (the speaker icon next to the arm button unlit): with it on, the click loops between the speaker and the mic. Speak into the mic and check that the meter moves.
6. Put the mic about 30 cm in front of the speaker, pointing at it.

## 4. Measure the round trip

1. Press *Home*, then *Ctrl+R* to record. Stop with *Space* after about 11 s.
2. The Mic track shows peaks a little after each second.
3. Zoom in on the peak near 1 s (mouse wheel) and click exactly where the waveform leaves the flat line. Read the position in the transport bar, for example `0:01.025`.
4. Round trip = position − 1 s − **0.9 ms** (the time the sound spends in 30 cm of air). In the example: 25 − 0.9 ≈ 24 ms.
5. Repeat on the peaks near 2 s and 3 s (subtracting 2 s and 3 s) and average.

What you measure is input latency plus output latency, the same path a monitored voice takes. Plugin delay comes on top (step 6).

**Electrical loopback (optional):** connect an output to a free input, record that input instead of the mic, and skip the air-time correction.

| Symptom | Likely cause |
|---|---|
| No click from the speakers | Wrong output in ASIO4ALL, or the interface output is turned down. |
| Nothing recorded on the Mic track | Wrong input, track not armed, or input gain at minimum. |
| Feedback or repeated clicks | Monitoring is on in the Mic track. |
| The peak sits exactly on `0:01.000` | *Use audio driver reported latency* is still on. |

## 5. Other drivers and buffers (optional)

Only if step 7 isn't comfortable, or for a second round. Repeat step 4 at 64 and 256 samples, and with the other driver (ASIO4ALL or WASAPI exclusive: *Audio system: WASAPI*, *Exclusive mode*, the interface as input and output). For each, play a dense song for 5 minutes with the mic monitored and listen for clicks or dropouts.

## 6. Effects chain

Build a test chain on the Mic track, roughly in this order:

- low cut (EQ), and a gate;
- a compressor **without look-ahead** (in ReaComp, *pre-comp* at 0);
- pitch correction in **low-latency, chromatic** mode;
- a doubler (a short delay with a slight detune);
- reverb and delay.

The plugins are up to you. ReaPlugs ship with REAPER, and there are free third-party options for pitch correction.

**Check each plugin's reported delay** (in samples, *spls*) in the FX chain window. On a monitored track it adds directly to what the singer hears: 1000 samples is about 23 ms at 44.1 kHz. Prefer plugins that report zero.

## 7. Singing test

1. Turn monitoring on in the Mic track, move the mic away from the speaker, and sing 2–3 songs over any audio file in REAPER.
2. Do it with the chain on and with it bypassed, and rate the comfort from 1 to 5.
3. Write down anything that felt late, sounded hollow or phasey (your own voice through the air and the delayed one together), or sounded robotic.

## 8. Plan B (only if the criterion fails)

1. Turn on the interface's direct monitoring, if it has one, so the dry voice skips the computer.
2. On the Mic track, keep only reverb and delay at 100% wet, with the dry signal muted.
3. Repeat step 7 and compare.

## 9. Noise

With the mic gain at minimum, monitoring off, nothing playing and the speakers at the volume a night needs: is there audible hiss or hum?

- If it goes away with the machine's charger unplugged, it's a ground loop: put everything on the same power strip, or use a ground loop isolator.
- If it stays, it comes from the interface's output. Check the gain staging first: the interface output turned up high and the speakers turned down.
- Hiss that only grows with the input gain comes from the mic preamp. Sing close to the mic (2–5 cm) to need less gain, and use a gate.

## Recording the results

Results name the hardware, so they stay out of git. Copy the template into `spikes/s1-latency/results.local.md`, which is gitignored, and give screenshots a `.local.png` suffix. Only a generic summary goes into `docs/PLAN.md`.

```markdown
# S1 results: round <n>, <date>

Interface / driver version:
Machine (CPU, RAM, Windows build):

## LatencyMon
| Run | Verdict | Highest DPC (µs) | Highest ISR (µs) | Worst driver |
|---|---|---|---|---|
| Idle | | | | |

## Round trip
| Driver | Buffer | Reported (ms) | Measured (ms) | Crackles? |
|---|---|---|---|---|
| | 128 | | | |

## Effects chain
| Plugin | Role | Delay (samples) |
|---|---|---|

## Singing test
| Setup | Comfort (1–5) | Notes |
|---|---|---|
| Chain on | | |
| Chain bypassed | | |

## Noise
Hiss or hum with the mic gain at minimum? Source:

## Verdict
Pass / fail, and why:
```

On the second round, also check two mics monitored at the same time.
