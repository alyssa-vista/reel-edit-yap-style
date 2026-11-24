"""Little sounds, made from nothing (no samples, so nothing to get flagged), under the voice."""

import numpy as np
import soundfile as sf

SR = 48000
RND = np.random.default_rng(4)


def pop():
    t = np.arange(int(SR * 0.12)) / SR
    return np.sin(2 * np.pi * (520 - 1500 * t) * t) * np.exp(-t * 46) * (1 - np.exp(-t * 900)) * 0.5


def swoosh():
    n = int(SR * 0.32)
    t = np.arange(n) / n
    noise, low = RND.normal(0, 1, n), np.zeros(n)
    for i in range(1, n):
        k = 0.02 + 0.22 * np.sin(np.pi * t[i]) ** 2
        low[i] = low[i - 1] + k * (noise[i] - low[i - 1])
    return low * np.sin(np.pi * t) ** 1.6 * 0.28


def mix(voice, sounds, amount, seconds, out):
    v, sr = sf.read(voice, dtype="float64")
    total = int(seconds * SR)
    v = np.pad(v, (0, max(0, total - len(v))))[:total]
    loud = np.sqrt(np.mean(v[np.abs(v) > 0.01] ** 2)) if (np.abs(v) > 0.01).any() else 1
    v = v * min(8, 0.165 / loud)
    made, last = dict(pop=pop(), swoosh=swoosh()), -1.0
    for t, kind in sorted(sounds):
        if amount and t - last > 0.15:
            s, i = made[kind] * amount, int(max(0, t - 0.02) * SR)
            end = min(total, i + len(s))
            if end > i:
                v[i:end] += s[: end - i]
            last = t
    peak = np.abs(v).max()
    v = np.tanh(v / 0.89) * 0.89 if peak > 0.89 else v
    fade = min(total, int(SR * 0.08))
    v[-fade:] *= np.linspace(1, 0, fade)
    sf.write(out, np.stack([v, v], 1), SR, subtype="PCM_16")
    return out
