"""Offline "pro vocal" clean-up, run on a raw recording before the character effects.

python pro_chain.py in.wav [out.wav]   (default: in_pro.wav)

Chain: 80 Hz high-pass -> spectral denoise -> de-esser -> breath reducer -> compressor ->
expander -> limiter
-> loudness to -18 dBFS speech. Roughly what a studio take gets before sound
design.
"""
import sys

import numpy as np
from scipy.ndimage import maximum_filter1d, minimum_filter1d, uniform_filter1d
from scipy.signal import butter, istft, sosfilt, stft

NFFT, HOP = 2048, 512


def high_pass(x, sr, hz=80):
    return sosfilt(butter(2, hz, btype="high", fs=sr, output="sos"), x)


def denoise(x, sr, reduction_db=24, over=2.5):
    """Spectral gating: learn the noise from the quietest 10% of frames, then
    pull down every time-frequency bin that isn't clearly above it."""
    f, t, Z = stft(x, sr, nperseg=NFFT, noverlap=NFFT - HOP)
    mag = np.abs(Z)
    e = mag.sum(0)
    noise = np.median(mag[:, e <= np.percentile(e, 10)], axis=1, keepdims=True)
    keep = mag > over * noise
    # Smooth the mask over neighbouring bins and frames so it doesn't warble.
    mask = uniform_filter1d(uniform_filter1d(keep.astype(float), 5, axis=0), 5, axis=1)
    floor = 10 ** (-reduction_db / 20)
    _, y = istft(Z * (floor + (1 - floor) * mask), sr, nperseg=NFFT, noverlap=NFFT - HOP)
    return y[:len(x)]


def de_ess(x, sr, lo=4500, hi=10000, threshold=0.3, max_cut_db=10):
    """Turn down 4.5-10 kHz in frames where it holds more than `threshold` of
    the energy (an "s", "sh" or "t"), by up to max_cut_db."""
    f, t, Z = stft(x, sr, nperseg=1024, noverlap=768)
    p = np.abs(Z) ** 2
    band = (f >= lo) & (f <= hi)
    share = p[band].sum(0) / (p.sum(0) + 1e-12)
    cut_db = np.clip((share - threshold) / (1 - threshold), 0, 1) * max_cut_db
    cut_db = uniform_filter1d(maximum_filter1d(cut_db, 3), 3)  # hold briefly, then smooth
    g = np.ones_like(p)
    g[band] = 10 ** (-cut_db / 20)
    _, y = istft(Z * g, sr, nperseg=1024, noverlap=768)
    return y[:len(x)]


def find_breaths(x, sr, min_len=0.12, margin=0.02):
    """Breath segments as (start, end) samples: unvoiced, 10-45 dB under speech,
    at least min_len long and darker than an "s" (centroid under 4 kHz).
    Trimmed by `margin` at each end so word edges are left alone."""
    import librosa
    hop = int(0.01 * sr)
    z = librosa.resample(x, orig_sr=sr, target_sr=16000)
    _, voiced, _ = librosa.pyin(z, fmin=50, fmax=400, sr=16000, frame_length=1024, hop_length=160)
    lvl = 10 * np.log10(uniform_filter1d(x ** 2, 2 * hop)[::hop] + 1e-12)
    n = min(len(lvl), len(voiced))
    lvl, voiced = lvl[:n], voiced[:n]
    S = speech_db(x, sr)
    cand = ~voiced & (lvl < S - 10) & (lvl > S - 45)
    f, t, Z = stft(x, sr, nperseg=1024, noverlap=1024 - hop)
    p = np.abs(Z) ** 2
    cent = (f[:, None] * p).sum(0) / (p.sum(0) + 1e-12)
    segs, i = [], 0
    while i < n:
        if not cand[i]:
            i += 1
            continue
        j = i
        while j < n and cand[j]:
            j += 1
        if (j - i) * 0.01 >= min_len and np.median(cent[i:min(j, len(cent))]) < 4000:
            a, b = int((i * 0.01 + margin) * sr), int((j * 0.01 - margin) * sr)
            if b > a:
                segs.append((a, b))
        i = j
    return segs


def de_breath(x, sr, cut_db=15, fade=0.015):
    g = np.ones_like(x)
    for a, b in find_breaths(x, sr):
        g[a:b] = 10 ** (-cut_db / 20)
    k = int(fade * sr)
    return x * np.convolve(g, np.ones(k) / k, mode="same")  # fade in/out over 15 ms


def compress(x, sr, threshold_db=-24, ratio=3.0, attack=0.005, release=0.08):
    env = np.abs(x)
    a_att, a_rel = np.exp(-1 / (attack * sr)), np.exp(-1 / (release * sr))
    lvl = np.empty_like(env)
    c = 0.0
    for i, v in enumerate(env):
        c = a_att * c + (1 - a_att) * v if v > c else a_rel * c + (1 - a_rel) * v
        lvl[i] = c
    over = np.maximum(20 * np.log10(lvl + 1e-9) - threshold_db, 0)
    return x * 10 ** (-over * (1 - 1 / ratio) / 20)


def expand(x, sr, below_speech_db=30, ratio=3.0, max_cut_db=20):
    """Downward expander: the compressor lifts the gaps between words, so push
    anything more than `below_speech_db` under the speech level back down."""
    n = int(0.02 * sr)
    lvl = 10 * np.log10(uniform_filter1d(x ** 2, n) + 1e-12)
    thr = speech_db(x, sr) - below_speech_db
    cut = np.minimum(np.maximum(thr - lvl, 0) * (ratio - 1), max_cut_db)
    cut = uniform_filter1d(minimum_filter1d(cut, 2 * n), n)  # open early around words, smooth the edges
    return x * 10 ** (-cut / 20)


def speech_db(x, sr):
    n = int(0.05 * sr)
    frames = x[:len(x) // n * n].reshape(-1, n)
    d = 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9)
    return np.mean(d[d > d.max() - 25])


def limit(x, sr, ceil_db=-1.0, release=0.05):
    ceil = 10 ** (ceil_db / 20)
    need = np.minimum(1, ceil / (maximum_filter1d(np.abs(x), int(0.004 * sr)) + 1e-9))  # 2 ms look-ahead each side
    a = np.exp(-1 / (release * sr))
    g = np.empty_like(need)
    c = 1.0
    for i, n in enumerate(need):
        c = n if n < c else n + (c - n) * a
        g[i] = c
    return x * g


def pro_chain(x, sr, target_db=-18):
    y = high_pass(x, sr)
    y = denoise(y, sr)
    y = de_ess(y, sr)
    y = de_breath(y, sr)
    y = compress(y, sr)
    y = expand(y, sr)
    for _ in range(3):
        y = limit(y * 10 ** ((target_db - speech_db(y, sr)) / 20), sr)
    return y


if __name__ == "__main__":
    import soundfile as sf
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src.rsplit(".", 1)[0] + "_pro.wav"
    x, sr = sf.read(src)
    if x.ndim > 1:
        x = x.mean(1)
    sf.write(dst, pro_chain(x, sr).astype(np.float32), sr)
    print("wrote", dst)
