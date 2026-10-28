"""Render a recording through the presets, for comparing them side by side.

    python render.py take.wav                      # every preset -> renders/take/
    python render.py take.wav --clean              # run the pro vocal clean-up first (recommended)
    python render.py take.wav --clean --shift -3   # ...and pitch down 3 semitones first
    python render.py take.wav --presets p5,q1      # only some presets
    python render.py take.wav --ref seven_line.mp3 # match loudness to a reference and include it

Then listen with:  python flip.py renders/take

Every file is set to the same speech loudness (-17 dBFS, or the reference's)
with peaks limited to -1 dBFS, so louder never sounds "better".
"""
import argparse
import os

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

import pro_chain as pc
from seven_voice import PRESETS, SR, process_file


def load(path):
    import librosa
    x, sr = sf.read(path)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return librosa.resample(x, orig_sr=sr, target_sr=SR) if sr != SR else x


def shift_pitch(x, semitones):
    """Formant-preserving pitch shift with WORLD (handles low, creaky voices well)."""
    import pyworld as pw
    x = np.ascontiguousarray(x, dtype=np.float64)
    f0, t = pw.harvest(x, SR, f0_floor=40, f0_ceil=400, frame_period=5.0)
    sp, ap = pw.cheaptrick(x, f0, t, SR), pw.d4c(x, f0, t, SR)
    y = pw.synthesize(f0 * 2 ** (semitones / 12), sp, ap, SR, 5.0)[:len(x)]
    return np.pad(y, (0, len(x) - len(y)))


def level(y, target_db):
    for _ in range(4):
        y = pc.limit(y * 10 ** ((target_db - pc.speech_db(y, SR)) / 20), SR)
    return y.astype(np.float32)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    ap.add_argument("--clean", action="store_true", help="denoise, de-ess, de-breath and compress first")
    ap.add_argument("--shift", type=float, default=0.0, help="pitch shift in semitones before the effects")
    ap.add_argument("--presets", default="all", help="comma-separated preset names, or 'all'")
    ap.add_argument("--ref", help="reference clip: match its loudness and include it as 00_reference")
    ap.add_argument("--out", help="output folder (default renders/<file name>)")
    a = ap.parse_args()

    names = list(PRESETS) if a.presets == "all" else a.presets.split(",")
    for n in names:
        if n not in PRESETS:
            raise SystemExit(f"unknown preset {n!r}; choose from: {', '.join(PRESETS)}")
    stem = os.path.splitext(os.path.basename(a.file))[0]
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "renders", stem)
    os.makedirs(out, exist_ok=True)

    x = load(a.file)
    target = -17.0
    if a.ref:
        r = load(a.ref)
        target = pc.speech_db(r, SR)
        sf.write(os.path.join(out, "00_reference.wav"), r.astype(np.float32), SR)
    sf.write(os.path.join(out, "01_original.wav"), level(x.copy(), target), SR)
    if a.clean:
        x = pc.pro_chain(x, SR)
        sf.write(os.path.join(out, "02_clean.wav"), level(x.copy(), target), SR)
    ratio = 1.0
    if a.shift:
        x = shift_pitch(x, a.shift)
        ratio = 2 ** (a.shift / 12)
        sf.write(os.path.join(out, "03_shifted.wav"), level(x.copy(), target), SR)

    lp = butter(8, 16500, fs=SR, output="sos")  # Seven's lines stop around 16.5 kHz
    for i, n in enumerate(names, 10):
        settings = dict(PRESETS[n][1])
        # Keep the fixed-pitch parts in proportion when the voice is shifted.
        settings["vocoder_hz"] = settings.get("vocoder_hz", 92.5) * ratio
        if settings.get("lock_hz", 0) > 0:
            settings["lock_hz"] *= ratio
        y = process_file(x, SR, {**settings, "seed": 1})
        if not settings.get("quantize"):
            y = sosfilt(lp, y)
        sf.write(os.path.join(out, f"{i}_{n}.wav"), level(y, target), SR)
        print(f"  {n:6s} {PRESETS[n][0]}")
    print(f"wrote {out}\nlisten: python flip.py \"{out}\"")


if __name__ == "__main__":
    main()
