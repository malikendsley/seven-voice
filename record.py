"""Record your dry mic to a WAV file.

python record.py [file] [--in MIC]   (default file: me.wav)

Press Enter to start, Enter again to stop.
"""
import argparse

import numpy as np
import sounddevice as sd
import soundfile as sf

from seven_voice import SR, default_mic, device

ap = argparse.ArgumentParser()
ap.add_argument("file", nargs="?", default="me.wav")
ap.add_argument("--in", dest="dev_in", type=device, help="input device id or name substring (default: system mic)")
a = ap.parse_args()
mic = a.dev_in if a.dev_in is not None else default_mic()
print(f"Mic: {sd.query_devices(mic)['name']}")

chunks = []
input("Press Enter to start recording...")
with sd.InputStream(samplerate=SR, device=mic, channels=1, dtype="float32",
                    callback=lambda d, *_: chunks.append(d.copy())):
    input("Recording. Press Enter to stop.")
x = np.concatenate(chunks)[:, 0]
sf.write(a.file, x, SR)
peak = np.abs(x).max()
print(f"Saved {a.file}: {len(x) / SR:.1f} s, peak {peak:.2f}" + ("  (clipped, back off the mic)" if peak > 0.98 else "  (quiet, move closer)" if peak < 0.1 else ""))
