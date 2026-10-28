"""Step through audio clips with the arrow keys (Windows). Handy for comparing renders.

python flip.py [folder]   (default: renders)

  Left/Right  previous/next clip (plays immediately)
  Space       replay current clip
  L           toggle looping
  Q / Esc     quit
"""
import glob
import os
import sys

import sounddevice as sd
import soundfile as sf

from keys import read_key

folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "renders")
files = sorted(f for ext in ("wav", "mp3", "flac", "ogg") for f in glob.glob(os.path.join(folder, "**", f"*.{ext}"), recursive=True))
if not files:
    sys.exit(f"no audio files in {folder}")

cache = {}
i, loop = 0, False


def play():
    if files[i] not in cache:
        cache[files[i]] = sf.read(files[i], dtype="float32")
    data, sr = cache[files[i]]
    sd.play(data, sr, loop=loop)
    print(f"\r\033[K[{i + 1}/{len(files)}] {os.path.relpath(files[i], folder)}{'  (loop)' if loop else ''}", end="", flush=True)


print(__doc__.split("\n\n", 2)[2])
play()
while True:
    k = read_key()
    if k in ("right", "left"):
        i = (i + (1 if k == "right" else -1)) % len(files)
        play()
    elif k == "space":
        play()
    elif k == "l":
        loop = not loop
        play()
    elif k in ("q", "esc"):
        sd.stop()
        print()
        break
