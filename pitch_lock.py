"""Streaming pitch lock (TD-PSOLA): pulls your pitch to a fixed note, or snaps it to the
nearest note of a scale (hard auto-tune), keeping formants.

Fixed-note mode keeps `keep` of your own pitch movement (around your running
average) and centres it on `target_hz`; Seven's voice has a portion locked near
92.5 Hz. Quantize mode snaps to the nearest note of a scale, with `stick`
semitones of hysteresis so it doesn't flicker between two notes.

How it works: find your pitch with YIN, mark one point per pitch period, cut a
two-period Hann-windowed slice around each mark, and lay the slices back down
spaced at the target period. Re-spacing slices changes pitch but not the
spectral envelope inside each slice, so formants are unchanged.

Latency is fixed at ~60 ms at 48 kHz: look-ahead for the pitch detector plus
room to lay down every slice that overlaps a block before that block is played.
"""
import numpy as np
from scipy.signal import butter, lfilter, lfilter_zi

FMIN, FMAX = 65.0, 300.0
YIN_W = 1536          # minimum analysis window (2 periods at 65 Hz)
UNVOICED_HZ = 100.0   # slice spacing when there's no pitch (noise just passes through)

# Pitch classes (0 = the key's root) for quantize mode.
SCALES = {
    "chromatic": range(12),
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "pentatonic": (0, 3, 5, 7, 10),  # minor pentatonic
}
KEYS = {k: i for i, k in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


class PitchLock:
    def __init__(self, sr, target_hz=92.5, keep=0.15, threshold=0.2, hold_threshold=0.35, min_level=0.003, seed=None,
                 quantize=None, key="C", stick=0.2, delay=None, fmin=FMIN):
        self.sr, self.target_hz, self.keep = sr, target_hz, keep
        # Quantize mode: instead of one fixed note, snap to the nearest note of
        # `quantize` (a SCALES name) in `key`. `stick` (semitones) is how far past
        # the halfway point you must drift before it jumps, so it doesn't flicker.
        self.scale = None if quantize is None else sorted((KEYS[key] + p) % 12 for p in SCALES[quantize])
        self.stick, self.note = stick, None
        self.threshold, self.hold_threshold, self.min_level = threshold, hold_threshold, min_level
        self.rng = np.random.default_rng(seed)
        self.was_voiced = False
        # Lowest pitch handled; lower means more delay. Window must hold 2 periods.
        self.fmin = fmin
        self.yin_w = max(YIN_W, int(np.ceil(2 * sr / fmin)))
        self.tmax = int(np.ceil(sr / fmin))
        # 2.5 periods at fmin + half the pitch window + one block (~60 ms); below ~54 ms
        # slices start getting cut off and click.
        self.delay = delay if delay is not None else int(2.5 * self.tmax) + self.yin_w // 2 + 256
        self.total = 0                              # input samples received
        self.x = np.zeros(0)                        # input history, starts at self.x0
        self.lp = np.zeros(0)                       # lowpassed copy, for aligning marks to pitch peaks
        self.x0 = 0
        b, a = butter(2, 900 / (sr / 2))
        self.lp_ba, self.lp_zi = (b, a), lfilter_zi(b, a) * 0
        self.f0_pos, self.f0_val = [], []           # pitch track: frame centres and Hz (0 = unvoiced)
        self.marks = []                             # input marks: (pos, period, voiced)
        self.next_mark = 0
        self.next_out = 0.0                         # next output slice centre
        self.mi = 0                                 # search start into self.marks
        self.acc = np.zeros(0)                      # overlap-add output and window sum, start at self.o0
        self.wsum = np.zeros(0)
        self.o0 = 0
        self.log_mean = None                        # running average of your pitch (log Hz)

    # ---- analysis ---------------------------------------------------------
    def _yin(self, frame):
        if np.sqrt(np.mean(frame ** 2)) < self.min_level:
            return 0.0
        n, tmax = len(frame), int(self.sr / self.fmin)
        f = np.fft.rfft(frame, 2 * n)
        ac = np.fft.irfft(f * np.conj(f))[:tmax + 2]
        cs = np.concatenate([[0], np.cumsum(frame ** 2)])
        tau = np.arange(tmax + 2)
        d = cs[n - tau] + (cs[n] - cs[tau]) - 2 * ac       # YIN difference function
        d[0] = 0
        cmnd = d[1:] * np.arange(1, len(d)) / np.maximum(np.cumsum(d[1:]), 1e-12)
        lo = int(self.sr / FMAX)
        # Accept a weaker match while already voiced, so voicing doesn't drop out mid-word.
        below = np.where(cmnd[lo - 1:] < (self.hold_threshold if self.was_voiced else self.threshold))[0]
        if not len(below):
            return 0.0
        t = below[0] + lo - 1
        while t + 1 < len(cmnd) and cmnd[t + 1] < cmnd[t]:   # walk down to the local minimum
            t += 1
        if 0 < t < len(cmnd) - 1:                            # parabolic refinement
            a, b, c = cmnd[t - 1], cmnd[t], cmnd[t + 1]
            t = t + 0.5 * (a - c) / max(a - 2 * b + c, 1e-12)
        return self.sr / (t + 1)

    def _track(self, f0):
        """Fix octave errors against your running average, and keep that average."""
        if f0 <= 0:
            return 0.0
        if self.log_mean is None:  # first voiced frame sets the starting average
            self.log_mean = np.log(f0)
        ratio = f0 / np.exp(self.log_mean)
        if ratio > 1.6:
            f0 /= 2
        elif ratio < 0.62:
            f0 *= 2
        self.log_mean += 0.004 * (np.log(f0) - self.log_mean)  # ~1.3 s of voiced speech at 48 kHz / 256
        return f0

    def _f0_at(self, pos):
        i = np.searchsorted(self.f0_pos, pos, side="right") - 1
        return self.f0_val[max(i, 0)]

    def _extend_marks(self):
        known = self.f0_pos[-1] if self.f0_pos else -1
        while True:
            m = self.next_mark
            f0 = self._f0_at(m) if m <= known else None
            if f0 is None:
                return
            voiced = f0 > 0
            # Unvoiced spacing is jittered so it doesn't impose a buzz on noise.
            T = int(round(self.sr / (f0 if voiced else UNVOICED_HZ * self.rng.uniform(0.8, 1.25))))
            if m + T + T // 4 > known:
                return
            if voiced:  # snap to the strongest peak near one period ahead
                lo, hi = m + T - T // 4 - self.x0, m + T + T // 4 - self.x0
                nxt = m + T if lo < 0 else lo + int(np.argmax(self.lp[lo:hi])) + self.x0
            else:
                nxt = m + T
            self.marks.append((m, T, voiced))
            self.next_mark = max(nxt, m + T // 2)

    # ---- synthesis --------------------------------------------------------
    def _nearest_note(self, midi):
        cands = [o * 12 + pc for o in range(int(midi // 12) - 1, int(midi // 12) + 2) for pc in self.scale]
        return min(cands, key=lambda n: abs(n - midi))

    def _target_period(self, f0):
        if self.scale is None:
            hz = self.target_hz * np.exp(self.keep * (np.log(f0) - self.log_mean))
        else:
            midi = 69 + 12 * np.log2(f0 / 440)
            best = self._nearest_note(midi)
            if self.note is None or (best != self.note and abs(midi - self.note) > abs(midi - best) + self.stick):
                self.note = best
            hz = 440 * 2 ** ((self.note - 69) / 12)
        return self.sr / hz

    def _place_slices(self, upto):
        while self.next_out + self.tmax <= upto and self.marks:
            o = self.next_out
            while self.mi + 1 < len(self.marks) and abs(self.marks[self.mi + 1][0] - o) <= abs(self.marks[self.mi][0] - o):
                self.mi += 1
            if self.mi == len(self.marks) - 1 and self.marks[self.mi][0] < o:
                return  # need marks further ahead
            m, T, voiced = self.marks[self.mi]
            s = m - T - self.x0
            if s < 0:
                self.next_out += T
                continue
            grain = self.x[s:s + 2 * T + 1] * np.hanning(2 * T + 1)
            start = int(round(o)) - T - self.o0
            need = start + 2 * T + 1
            if need > len(self.acc):
                self.acc = np.concatenate([self.acc, np.zeros(need - len(self.acc) + 4096)])
                self.wsum = np.concatenate([self.wsum, np.zeros(len(self.acc) - len(self.wsum))])
            if start >= 0:
                self.acc[start:need] += grain
                self.wsum[start:need] += np.hanning(2 * T + 1)
            self.next_out += self._target_period(self.sr / T) if voiced else T

    # ---- public -----------------------------------------------------------
    def process(self, block):
        n = len(block)
        self.x = np.concatenate([self.x, block])
        lpb, self.lp_zi = lfilter(*self.lp_ba, block, zi=self.lp_zi)
        self.lp = np.concatenate([self.lp, lpb])  # signed: always snap to the same (positive) peak of each period
        self.total += n
        if self.total >= self.yin_w:
            c = self.total - self.yin_w // 2
            self.f0_pos.append(c)
            f0 = self._track(self._yin(self.x[self.total - self.yin_w - self.x0:self.total - self.x0]))
            self.was_voiced = f0 > 0
            self.f0_val.append(f0)
        self._extend_marks()
        emit = self.total - self.delay
        # Place every slice that overlaps this block before emitting it: a slice
        # reaches one period either side of its centre, so look 2 periods ahead.
        self._place_slices(emit + n + 2 * self.tmax)
        out = np.zeros(n)
        if emit >= 0:
            a, b = emit - self.o0, emit - self.o0 + n
            if b <= len(self.acc):
                out = self.acc[a:b] / np.maximum(self.wsum[a:b], 0.5)
        # Trim history we can no longer need.
        keep_from = min(emit, self.total - self.yin_w, int(self.next_out)) - 4 * self.tmax
        if keep_from - self.x0 > 48000:
            d = keep_from - self.x0
            self.x, self.lp, self.x0 = self.x[d:], self.lp[d:], keep_from
            k = np.searchsorted(self.f0_pos, keep_from - self.tmax)
            self.f0_pos, self.f0_val = self.f0_pos[k:], self.f0_val[k:]
            k = max(0, self.mi - 2)
            self.marks, self.mi = self.marks[k:], self.mi - k
        if emit - self.o0 > 48000:
            d = emit - self.o0
            self.acc, self.wsum, self.o0 = self.acc[d:], self.wsum[d:], emit
        return out
