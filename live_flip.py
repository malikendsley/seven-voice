"""Live voice changer that switches presets with the arrow keys (Windows).

python live_flip.py [--in MIC] [--out DEVICE] [--start p5]

  Left/Right  previous/next preset
  Space       bypass on/off (hear your dry voice)
  Q / Esc     quit

Use headphones, or the speakers will feed back into the mic.
"""
import argparse

from keys import read_key
from seven_voice import DEFAULT_PRESET, PRESETS, SR, SevenFX, device, open_stream

ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="dev_in", type=device, help="input device id or name substring (default: system mic)")
ap.add_argument("--out", dest="dev_out", type=device, help="output device id or name substring (default: system output)")
ap.add_argument("--start", default=DEFAULT_PRESET, choices=list(PRESETS), help="preset to start on")
a = ap.parse_args()

print("Loading presets...", flush=True)
keys = list(PRESETS)
fxs = [SevenFX(**settings) for _, settings in PRESETS.values()]
state = {"i": keys.index(a.start), "bypass": False}


def process(x):
    # Only the selected preset runs, so switching resets nothing but costs nothing.
    return x if state["bypass"] else fxs[state["i"]].process(x)


def show():
    if state["bypass"]:
        label = "BYPASS (dry)"
    else:
        fx = fxs[state["i"]]
        delay = f"  (+{fx.delay / SR * 1000:.0f} ms)" if fx.delay else ""
        label = f"[{state['i'] + 1}/{len(keys)}] {keys[state['i']]}: {PRESETS[keys[state['i']]][0]}{delay}"
    print(f"\r\033[K{label}", end="", flush=True)


with open_stream(process, a.dev_in, a.dev_out):
    print(__doc__.split("\n\n", 2)[2])
    show()
    while True:
        k = read_key()
        if k in ("right", "left"):
            state["i"] = (state["i"] + (1 if k == "right" else -1)) % len(keys)
            state["bypass"] = False
        elif k == "space":
            state["bypass"] = not state["bypass"]
        elif k in ("q", "esc"):
            print()
            break
        show()
