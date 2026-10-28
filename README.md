# Seven Voice

A live voice changer for Windows, modelled on Seven from *Deadlock*. Talk into your mic and it comes out with Seven's electric "charge": a ring-mod rattle, a fixed-pitch buzz, and part of your voice locked to one note. There are also some hard auto-tune presets that snap your voice to the nearest musical note.

It's plain Python: no plugins, no DAW.

## What you need

- **Windows 10/11.** The arrow-key tools use Windows-only keyboard input.
- **Python 3.10** from [python.org](https://www.python.org/downloads/) is what it was tested on. Tick **"Add python.exe to PATH"** when installing. Newer versions probably work, but one dependency (`pyworld`) may need to build from source if there's no prebuilt package for your version.
- **Headphones,** so your speakers don't feed back into the mic.
- **[VB-Audio Virtual Cable](https://vb-audio.com/Cable/)** (free) if you want to use it in Discord, OBS or games. It adds a fake mic that other apps can pick up.

## Setup

Double-click **`setup.bat`**. It creates a private Python environment in `.venv` and installs the dependencies. This only needs doing once.

## Use it

| To... | Do this |
|---|---|
| Try the presets on your own voice | Double-click **`presets.bat`**, put headphones on, and use ← / → to switch presets. Space toggles your dry voice, and Q quits. |
| Use it in Discord, OBS or a game | Double-click **`run.bat`**. It sends the processed voice to VB-Cable. In the app, set your microphone to **CABLE Output (VB-Audio Virtual Cable)**. |
| Pick a preset for Discord | Open a terminal in this folder and run `run.bat --preset q1` (CMD) or `.\run.bat --preset q1` (PowerShell), using any preset name below. |

**Discord settings** (User Settings → Voice & Video): turn off **Noise Suppression**, **Echo Cancellation** and **Automatic Gain Control**. They fight the effect.

**To hear yourself while using `run.bat`:** press Win+R, type `mmsys.cpl`, go to the **Recording** tab, right-click **CABLE Output** → **Properties**, then on the **Listen** tab tick **Listen to this device** and choose your headphones.

## Presets

| Name | What it does |
|---|---|
| `p5` | **Default.** Vocoder buzz 0.2 + 25% of your voice pitch-locked |
| `p1` | 50% pitch-locked, no vocoder buzz |
| `p2` | Vocoder 0.1 + 25% pitch-locked |
| `p3` | Vocoder 0.1 + 50% pitch-locked |
| `p4` | Vocoder 0.2, no pitch lock. Lowest delay. |
| `heavy` | Vocoder 0.4 + fully pitch-locked. More robotic. |
| `q1` | Hard auto-tune: snap to the nearest semitone, no other effects |
| `q2` | `q1` plus the `p5` effects |
| `q3` | Snap to C minor pentatonic. Bigger, more "sung" jumps. |

Presets with pitch lock or quantize add about **60 ms** of delay. `p4` adds none.

You can tweak any preset from the command line. Run `.venv\Scripts\python seven_voice.py --help` for the full list. A few examples:

```
.venv\Scripts\python seven_voice.py --preset p5 --ring 0.4 --lock-mix 0.4
.venv\Scripts\python seven_voice.py --preset q1 --quantize minor --key A
.venv\Scripts\python seven_voice.py --preset p5 --no-eq
```

**About the tone EQ:** the P presets include an EQ that was fitted to make the original author's voice and mic match Seven's tone. On a different voice it may be too much. If it sounds boomy, thin or harsh, try `--no-eq`.

## Devices

By default it uses your Windows default mic and output. If the default mic is a virtual one (VB-Cable, Voicemod, Voicemeeter...), it uses your first real microphone instead, since a game may have made the cable your default. At startup it prints which mic and output it's using. To pick others:

```
.venv\Scripts\python seven_voice.py --list
.venv\Scripts\python seven_voice.py --in "Microphone (USB" --out "Headphones"
```

`--in` / `--out` take a device number from `--list` or any part of its name.

## Offline tools

These are for recordings rather than live use. They're good for comparing presets carefully.

| Script | What it does |
|---|---|
| `record.py take.wav` | Records your dry mic. Press Enter to start and stop. |
| `render.py take.wav --clean` | Renders the recording through every preset into `renders/take/`, all level-matched. `--clean` runs a pro vocal clean-up first (denoise, de-ess, breath reduction, compression), which makes a big difference. `--shift -3` pitches you down 3 semitones first. `--presets p5,q1` renders only some presets. `--ref clip.mp3` matches loudness to a reference clip and includes it. |
| `flip.py renders/take` | Steps through a folder of clips with ← / →. Space replays, L loops. |
| `pro_chain.py take.wav` | Runs the clean-up on its own and writes `take_pro.wav`. |
| `seven_voice.py --file take.wav` | Runs one preset on a file and writes `take_seven.wav`. |

The clean-up, breath reduction and pitch shift are **offline only**. The live voice changer doesn't do them.

## How it works

`seven_voice.py` processes your voice in 256-sample blocks (about 5 ms at 48 kHz). The chain, in order:

1. **Pitch lock or quantize** (`pitch_lock.py`): finds your pitch and re-spaces one-period slices of your voice so it sits on a target note, without changing your formants. The P presets blend this locked copy with your natural voice. The Q presets snap to the nearest note of a scale.
2. **Tone EQ**
3. **Compressor and noise gate**
4. **Saw vocoder:** a fixed-pitch sawtooth shaped by your voice across 20 frequency bands
5. **Ring mod:** the electric rattle
6. **Soft saturation**

The settings came from measuring Seven's voice lines:
- A ring-mod-style modulation in every line.
- Roughly a quarter to a third of his voice locked near 92.5 Hz, with his natural pitch still audible.
- Heavy compression and near-silent pauses.

## Files

```
seven_voice.py   the voice changer and presets
pitch_lock.py    streaming pitch lock / quantize
live_flip.py     arrow-key preset switcher (presets.bat)
render.py        render a recording through the presets
pro_chain.py     offline vocal clean-up
flip.py          arrow-key clip player
keys.py          arrow-key reading for the two tools above
record.py        mic recorder
setup.bat        one-time setup
run.bat          live -> VB-Cable
presets.bat      live preset switcher
```

`me/` (personal recordings), `reference/` (game audio clips) and `renders/` are git-ignored. Seven's voice lines belong to Valve and aren't included. If you want reference clips to compare against, the [Deadlock wiki](https://deadlock.wiki/Seven/Voice_lines) has them, with file names starting `Gigawatt_`.

## Troubleshooting

- **"no audio device matching ..."**: run with `--list` and use a name or number from it.
- **`run.bat` says no CABLE device:** install VB-Cable and reboot, or use `presets.bat` to try it on headphones.
- **Echo or howling:** use headphones.
- **Crackling or dropouts:** close other audio apps, or try `--preset p4`, which has no pitch lock and does the least work.
- **Voice sounds wobbly on very low notes:** the pitch lock only tracks pitches down to about 65 Hz. Speak a little higher, or use `p4`.
