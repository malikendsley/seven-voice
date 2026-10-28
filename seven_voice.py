"""Live voice changer modelled on Seven from Deadlock.

    python seven_voice.py                          # default preset, default mic -> default output
    python seven_voice.py --out "CABLE Input"      # send to VB-Cable for Discord etc.
    python seven_voice.py --preset q1              # pick a preset (see --presets)
    python seven_voice.py --ring 0.4 --lock-mix 0.5   # override any setting
    python seven_voice.py --file take.wav          # process a file instead (writes take_seven.wav)
    python seven_voice.py --list                   # audio devices

Signal chain, in order:
  pitch lock / quantize -> tone EQ -> compressor + noise gate -> saw vocoder
  -> ring mod -> soft saturation -> 50 Hz high-pass
"""
import argparse
import sys

import numpy as np
from scipy.signal import butter, lfilter, lfilter_zi, sosfilt, sosfilt_zi

from pitch_lock import SCALES, PitchLock

SR = 48000
BLOCK = 256

# Tone-match EQ as (type, Hz, dB, Q). Fitted by comparing the original author's
# recordings against the same lines by Seven: it cuts low-mid/nasal 200-500 Hz
# and adds deep bass, presence and air. It's tuned to one voice and mic, so try
# --no-eq if it sounds wrong on yours. The top shelf is capped at +8 dB so it
# doesn't drag up mic hiss.
MATCH_EQ = [("peak", 65, -9.0, 1.4), ("peak", 120, 8.7, 1.4), ("peak", 200, -3.7, 1.4), ("peak", 320, -5.6, 1.4),
            ("peak", 500, -7.7, 1.4), ("peak", 800, 5.3, 1.4), ("peak", 1300, 1.0, 1.4), ("peak", 2100, -0.9, 1.4),
            ("peak", 3400, 5.4, 1.4), ("peak", 5500, 1.5, 1.4), ("shelf", 9000, 8.0, 0.7)]

# Seven's voice keeps his natural pitch audible with roughly a quarter to a third
# of it locked to 92.5 Hz, plus a ring-mod "charge". The P presets reach that
# balance by different routes (a fixed vocoder buzz, a blended pitch-locked copy,
# or both). The Q presets are hard auto-tune: snap to the nearest note.
_SEVEN = dict(ring=0.6, ring_hz=81.0)
PRESETS = {
    "p1": ("Pitch-lock blend 50%, no vocoder", {**_SEVEN, "lock_hz": 92.5, "lock_mix": 0.5}),
    "p2": ("Vocoder 0.1 + pitch-lock blend 25%", {**_SEVEN, "vocoder": 0.1, "lock_hz": 92.5, "lock_mix": 0.25}),
    "p3": ("Vocoder 0.1 + pitch-lock blend 50%", {**_SEVEN, "vocoder": 0.1, "lock_hz": 92.5, "lock_mix": 0.5}),
    "p4": ("Vocoder 0.2, no pitch lock (lowest delay)", {**_SEVEN, "vocoder": 0.2}),
    "p5": ("Vocoder 0.2 + pitch-lock blend 25% (default)", {**_SEVEN, "vocoder": 0.2, "lock_hz": 92.5, "lock_mix": 0.25}),
    "heavy": ("Vocoder 0.4 + full pitch lock (more robotic)", {**_SEVEN, "vocoder": 0.4, "lock_hz": 92.5}),
    "q1": ("Quantize to nearest semitone, clean", dict(quantize="chromatic", drive=0.0, eq=None)),
    "q2": ("Quantize to nearest semitone + P5 effects", {**_SEVEN, "vocoder": 0.2, "quantize": "chromatic"}),
    "q3": ("Quantize to C minor pentatonic, clean", dict(quantize="pentatonic", drive=0.0, eq=None)),
}
DEFAULT_PRESET = "p5"


def biquad(kind, f, gain_db, q, sr):
    """RBJ audio-EQ-cookbook peaking / high-shelf biquad as an SOS row."""
    A, w = 10 ** (gain_db / 40), 2 * np.pi * f / sr
    alpha, c = np.sin(w) / (2 * q), np.cos(w)
    if kind == "peak":
        b = [1 + alpha * A, -2 * c, 1 - alpha * A]
        a = [1 + alpha / A, -2 * c, 1 - alpha / A]
    else:
        k = 2 * np.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * c + k), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - k)]
        a = [(A + 1) - (A - 1) * c + k, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - k]
    return np.concatenate([np.array(b) / a[0], np.array(a) / a[0]])


class SevenFX:
    """Block-by-block effect chain. Call process() with consecutive blocks of mono float audio.

    vocoder     0-1, blend of a fixed-pitch sawtooth "buzz" shaped by your voice
    vocoder_hz  pitch of that buzz
    ring        0-1, ring-mod depth (the electric rattle); ring_hz its frequency
    lock_hz     pull your pitch toward this note (0 = off); adds ~60 ms delay
    lock_mix    0-1, how much of the pitch-locked copy to blend with your natural voice
    lock_keep   0-1, how much of your own pitch movement the locked copy keeps
    quantize    snap to the nearest note of a scale instead (see pitch_lock.SCALES); key sets the root
    drive       soft saturation amount, 0 = off
    eq          list of EQ bands, or None to skip the tone EQ
    comp_db/comp_ratio   compressor threshold (dBFS) and ratio, ratio 1 = off
    gate_db/gate_range   noise-gate threshold (dBFS) and how far it pulls down (dB), range 0 = off
    """

    def __init__(self, sr=SR, vocoder=0.0, vocoder_hz=92.5, ring=0.0, ring_hz=81.0,
                 lock_hz=0.0, lock_mix=1.0, lock_keep=0.15, quantize=None, key="C",
                 drive=1.5, eq=MATCH_EQ, comp_db=-30.0, comp_ratio=8.0, gate_db=-50.0, gate_range=15.0,
                 seed=None):
        self.sr = sr
        self.vocoder, self.vocoder_hz = vocoder, vocoder_hz
        self.ring, self.ring_hz, self.drive = ring, ring_hz, drive
        self.rng = np.random.default_rng(seed)
        self.t = 0  # running sample counter, keeps oscillators continuous across blocks

        # Pitch lock / quantize. The natural voice is delayed to line up with the locked copy.
        self.lock = (PitchLock(sr, target_hz=lock_hz, keep=lock_keep, seed=seed, quantize=quantize, key=key)
                     if lock_hz > 0 or quantize else None)
        self.lock_mix = lock_mix
        self.dry_hist = np.zeros(self.lock.delay) if self.lock is not None else None

        # Vocoder: split the mic and a sawtooth into the same bands, and let the
        # mic's loudness in each band set the sawtooth's loudness there.
        edges = np.geomspace(100, 8000, 21)
        self.bands = [butter(2, [lo / (sr / 2), hi / (sr / 2)], btype="band") for lo, hi in zip(edges[:-1], edges[1:])]
        self.mod_zi = [lfilter_zi(b, a) * 0 for b, a in self.bands]
        self.car_zi = [lfilter_zi(b, a) * 0 for b, a in self.bands]
        self.band_env_zi = np.zeros((len(self.bands), 1))
        a = np.exp(-1.0 / (0.008 * sr))
        self.band_env_b, self.band_env_a = [1 - a], [1, -a]
        self.phase = 0.0
        self.centers = np.sqrt(edges[:-1] * edges[1:])
        # A sawtooth is much quieter up high, so even out each band's level, and
        # mix noise into the top bands so "s"/"t"/"ch" sounds stay understandable.
        self.band_gain = self._carrier_band_gain()

        # Tone EQ on the input, before the effects.
        self.eq = np.array([biquad(*band, sr) for band in eq]) if eq else None
        self.eq_zi = sosfilt_zi(self.eq) * 0 if eq else None

        # Compressor (20 ms RMS detector) with make-up gain of half the reduction
        # a full-scale signal would get, and a noise gate on the same detector so
        # the make-up gain doesn't lift the pauses.
        self.comp_db, self.comp_ratio = comp_db, comp_ratio
        a = np.exp(-1.0 / (0.020 * sr))
        self.comp_b, self.comp_a = [1 - a], [1, -a]
        self.comp_zi = np.zeros(1)
        self.makeup_db = -comp_db * (1 - 1 / comp_ratio) * 0.5 if comp_ratio > 1 else 0.0
        self.gate_db, self.gate_range = gate_db, gate_range
        a = np.exp(-1.0 / (0.010 * sr))  # gate gain smoothing, so it doesn't chatter
        self.gs_b, self.gs_a = [1 - a], [1, -a]
        self.gs_zi = np.zeros(1)

        # High-pass at 50 Hz to clear rumble without touching the deep bass.
        self.hp_b, self.hp_a = butter(1, 50 / (sr / 2), btype="high")
        self.hp_zi = lfilter_zi(self.hp_b, self.hp_a) * 0

    @property
    def delay(self):
        """Added latency in samples (from the pitch lock)."""
        return self.lock.delay if self.lock is not None else 0

    def _carrier(self, n, phase=None):
        start = self.phase if phase is None else phase
        ph = start + np.arange(1, n + 1) * self.vocoder_hz / self.sr
        saw = 2 * (ph % 1.0) - 1
        if phase is None:
            self.phase = ph[-1] % 1.0
        return saw, self.rng.standard_normal(n)

    def _carrier_band_gain(self):
        saw, noise = self._carrier(self.sr, phase=0.0)
        gains = []
        for (b, a), fc in zip(self.bands, self.centers):
            src = self._mix_noise(saw, noise, fc)
            gains.append(1.0 / (np.sqrt(np.mean(lfilter(b, a, src) ** 2)) + 1e-9))
        return np.array(gains)

    @staticmethod
    def _mix_noise(saw, noise, fc):
        n = np.clip((fc - 3000) / 3000, 0, 0.8)
        return (1 - n) * saw + n * noise

    def _vocode(self, x):
        saw, noise = self._carrier(len(x))
        out = np.zeros(len(x))
        for k, ((b, a), fc) in enumerate(zip(self.bands, self.centers)):
            m, self.mod_zi[k] = lfilter(b, a, x, zi=self.mod_zi[k])
            env, self.band_env_zi[k] = lfilter(self.band_env_b, self.band_env_a, np.abs(m), zi=self.band_env_zi[k])
            c, self.car_zi[k] = lfilter(b, a, self._mix_noise(saw, noise, fc), zi=self.car_zi[k])
            out += c * env * self.band_gain[k]
        return out * 1.5

    def process(self, x):
        x = x.astype(np.float64)
        if self.lock is not None:
            locked = self.lock.process(x)
            buf = np.concatenate([self.dry_hist, x])
            dry, self.dry_hist = buf[:len(x)], buf[len(x):]
            x = (1 - self.lock_mix) * dry + self.lock_mix * locked
        if self.eq is not None:
            x, self.eq_zi = sosfilt(self.eq, x, zi=self.eq_zi)

        ms, self.comp_zi = lfilter(self.comp_b, self.comp_a, x * x, zi=self.comp_zi)
        level_db = 10 * np.log10(ms + 1e-12)
        gain_db = np.zeros(len(x))
        if self.comp_ratio > 1:
            gain_db += self.makeup_db - np.maximum(level_db - self.comp_db, 0) * (1 - 1 / self.comp_ratio)
        if self.gate_range > 0:
            closed = np.clip((self.gate_db - level_db) / 6, 0, 1)  # fades shut over 6 dB below threshold
            g, self.gs_zi = lfilter(self.gs_b, self.gs_a, -self.gate_range * closed, zi=self.gs_zi)
            gain_db += g
        x = x * 10 ** (gain_db / 20)

        y = x
        if self.vocoder > 0:
            y = (1 - self.vocoder) * x + self.vocoder * self._vocode(x)
        t = (self.t + np.arange(len(x))) / self.sr
        y *= (1 - self.ring) + self.ring * np.sin(2 * np.pi * self.ring_hz * t)
        if self.drive > 0:
            y = np.tanh(self.drive * y) / np.tanh(self.drive)
        y, self.hp_zi = lfilter(self.hp_b, self.hp_a, y, zi=self.hp_zi)
        self.t += len(x)
        return y.astype(np.float32)


def preset_settings(name):
    if name not in PRESETS:
        raise SystemExit(f"unknown preset {name!r}; choose from: {', '.join(PRESETS)}")
    return dict(PRESETS[name][1])


def process_file(x, sr, settings):
    """Run a whole clip through the chain; the pitch-lock delay is trimmed off."""
    fx = SevenFX(sr=sr, **settings)
    n = len(x)
    x = np.concatenate([x, np.zeros(fx.delay + BLOCK)])  # flush the delay out
    y = np.concatenate([fx.process(x[i:i + BLOCK]) for i in range(0, len(x) - BLOCK + 1, BLOCK)])
    return y[fx.delay:fx.delay + n]


def device(s):
    """Device id, or the first device whose name contains s (names repeat once per Windows audio API)."""
    if s.isdigit():
        return int(s)
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if s.lower() in d["name"].lower():
            return i
    raise argparse.ArgumentTypeError(f"no audio device matching {s!r} (see --list)")


# Virtual devices that show up as "microphones" but carry no live mic signal.
VIRTUAL = ("cable", "vb-audio", "voicemod", "virtual", "steam streaming", "voicemeeter")


def default_mic():
    """The Windows default mic, unless it's a virtual device (e.g. Voicemod or the
    VB-Cable we're feeding), in which case the first real microphone."""
    import sounddevice as sd
    devs = sd.query_devices()
    d = sd.default.device[0]
    if d is not None and d >= 0 and not any(v in devs[d]["name"].lower() for v in VIRTUAL):
        return d
    for i, dev in enumerate(devs):
        name = dev["name"].lower()
        if dev["max_input_channels"] > 0 and "microphone" in name and not any(v in name for v in VIRTUAL):
            return i
    return d


def device_names(dev_in, dev_out):
    import sounddevice as sd
    return sd.query_devices(dev_in)["name"], sd.query_devices(dev_out if dev_out is not None else sd.default.device[1])["name"]


def open_stream(callback, dev_in, dev_out):
    import sounddevice as sd
    if dev_in is None:
        dev_in = default_mic()
    mic, out = device_names(dev_in, dev_out)
    print(f"Mic: {mic}  ->  Output: {out}")
    out_ch = min(2, sd.query_devices(dev_out if dev_out is not None else sd.default.device[1])["max_output_channels"])

    def cb(indata, outdata, frames, time, status):
        if status:
            print(status, file=sys.stderr)
        outdata[:] = np.repeat(callback(indata[:, 0])[:, None], outdata.shape[1], axis=1)

    return sd.Stream(samplerate=SR, blocksize=BLOCK, device=(dev_in, dev_out),
                     channels=(1, out_ch), dtype="float32", latency="low", callback=cb)


def main():
    ap = argparse.ArgumentParser(description="Seven-style live voice changer.")
    ap.add_argument("--list", action="store_true", help="list audio devices and exit")
    ap.add_argument("--presets", action="store_true", help="list presets and exit")
    ap.add_argument("--in", dest="dev_in", type=device, help="input device id or name substring (default: system mic)")
    ap.add_argument("--out", dest="dev_out", type=device, help="output device id or name substring (default: system output)")
    ap.add_argument("--file", help="process an audio file instead of running live")
    ap.add_argument("--preset", default=DEFAULT_PRESET, help=f"starting settings (default {DEFAULT_PRESET})")
    g = ap.add_argument_group("overrides (applied on top of the preset)")
    g.add_argument("--vocoder", type=float, help="0-1, fixed-pitch sawtooth buzz blend")
    g.add_argument("--vocoder-hz", type=float, help="pitch of the buzz in Hz")
    g.add_argument("--ring", type=float, help="0-1, ring-mod depth")
    g.add_argument("--ring-hz", type=float, help="ring-mod frequency in Hz")
    g.add_argument("--lock-hz", type=float, help="pitch-lock target in Hz, 0 = off")
    g.add_argument("--lock-mix", type=float, help="0-1, locked copy vs natural voice")
    g.add_argument("--lock-keep", type=float, help="0-1, how much of your own pitch movement to keep")
    g.add_argument("--quantize", choices=list(SCALES), help="snap pitch to the nearest note of this scale")
    g.add_argument("--key", help="key for --quantize, e.g. C, F#, A")
    g.add_argument("--drive", type=float, help="saturation, 0 = off")
    g.add_argument("--no-eq", action="store_true", help="skip the tone-match EQ")
    a = ap.parse_args()

    if a.list:
        import sounddevice as sd
        print(sd.query_devices())
        return
    if a.presets:
        for k, (desc, _) in PRESETS.items():
            print(f"  {k:6s} {desc}")
        return

    settings = preset_settings(a.preset)
    for k in ("vocoder", "vocoder_hz", "ring", "ring_hz", "lock_hz", "lock_mix", "lock_keep", "quantize", "key", "drive"):
        if getattr(a, k) is not None:
            settings[k] = getattr(a, k)
    if a.no_eq:
        settings["eq"] = None

    if a.file:
        import soundfile as sf
        x, sr = sf.read(a.file)
        if x.ndim > 1:
            x = x.mean(axis=1)
        dst = a.file.rsplit(".", 1)[0] + "_seven.wav"
        sf.write(dst, process_file(x, sr, settings), sr)
        print("wrote", dst)
        return

    fx = SevenFX(**settings)
    print(f"Preset {a.preset}: {PRESETS[a.preset][0]}" + (f"  (+{fx.delay / SR * 1000:.0f} ms delay)" if fx.delay else ""))
    import sounddevice as sd
    with open_stream(fx.process, a.dev_in, a.dev_out):
        print("Running. Ctrl+C to stop.", flush=True)
        try:
            while True:
                sd.sleep(1000)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
