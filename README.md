Slop coded voice mod to make you sound like seven from deadlock

###needs

- Windows 10/11
- Python 3.10
- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/), to send your voice to games and Discord

###setup

Double-click **`setup.bat`**. You only need to do this once.

Double-click **`run.bat`**. Then, in your game or Discord, set the microphone to **CABLE Output (VB-Audio Virtual Cable)**.

To switch presets with the arrow keys while you talk, run `presets.bat` instead and send it to the cable. From PowerShell, in this folder:

```powershell
.\presets.bat --out "CABLE Input"
```

###to use

Controls are in the terminal Leave off `--out "CABLE Input"` to hear it in your headphones instead. There's enough lag to voice jam yourself, so probably not the easiest to test live. 

To start `run.bat` with a specific preset:

You might want to try  `--no-eq`, that's based on my own voice

```powershell
.\run.bat --preset q1
```

i made some presets of me messing around, only p5 and heavy sound remotely close. p4 has no lag. the others ~60ms

| Name | Sound |
|---|---|
| `p5` | Default Seven voice |
| `p1` – `p3` | Seven variations |
| `p4` | Seven voice without pitch lock, so no added delay |
| `heavy` | More robotic Seven |
| `q1` | Auto-tune: snaps to the nearest note |
| `q2` | Auto-tune plus the Seven effects |
| `q3` | Auto-tune with bigger, sung-sounding jumps |
