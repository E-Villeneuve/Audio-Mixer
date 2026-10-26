"""Converts sound files to the app's sample rate (e.g. 44.1 kHz -> 48 kHz).

Each new sample is a weighted average of the nearby original samples. The
weights follow a smooth curve (a "windowed sinc"), which keeps high
frequencies clean. The simpler approach, drawing straight lines between
samples, dulls the highs and adds a faint whine.

When lowering the sample rate, sounds too high-pitched for the new rate are
filtered out first. Otherwise they would fold back down as harsh noise
("aliasing").
"""

import numpy as np

HALF_TAPS = 24    # neighbouring samples averaged on each side: more is cleaner but slower
BETA = 9.0        # how sharply the weights fade out at the edges: higher filters more cleanly
CHUNK = 1 << 16   # output samples made per pass, to limit memory use
OVERSAMPLE = 512  # the weight curve is precomputed at this many points per sample


def _kernel_table(cutoff, width):
    """Precompute the weight curve once, so resample() can look values up.

    Calculating the curve fresh for every sample was the slow part.
    """
    d = np.linspace(-width, width, 2 * width * OVERSAMPLE + 1)
    u = d / width
    window = np.i0(BETA * np.sqrt(np.clip(1.0 - u * u, 0.0, None))) / np.i0(BETA)
    return np.append(cutoff * np.sinc(cutoff * d) * window, 0.0)  # extra 0 so i + 1 is always valid


def resample(x, src_rate, dst_rate):
    """Resample a 1-D float array from src_rate to dst_rate (Hz)."""
    x = np.asarray(x, dtype=np.float32)
    if src_rate == dst_rate or len(x) == 0:
        return x
    ratio = dst_rate / src_rate
    n_out = int(round(len(x) * ratio))
    # Highest frequency to keep, as a fraction of the highest the input can
    # hold. When downsampling, it's lowered to what the new rate can hold;
    # the 0.95 leaves a small safety margin.
    cutoff = min(1.0, ratio) * 0.95
    width = int(np.ceil(HALF_TAPS / cutoff))  # how many input samples each side get averaged
    padded = np.pad(x, (width, width + 1)).astype(np.float64)
    table = _kernel_table(cutoff, width)

    out = np.empty(n_out, dtype=np.float32)
    for start in range(0, n_out, CHUNK):
        t = np.arange(start, min(start + CHUNK, n_out)) / ratio  # where each new sample falls in the input
        base = np.floor(t).astype(np.int64)
        frac = t - base
        acc = np.zeros(len(t))
        norm = np.zeros(len(t))
        for k in range(-width + 1, width + 1):
            # Weight for input sample base+k, looked up by its distance from
            # the new sample (blending the two nearest table entries).
            idx = (k - frac + width) * OVERSAMPLE
            i = idx.astype(np.int64)
            a = idx - i
            w = table[i] * (1.0 - a) + table[i + 1] * a
            acc += w * padded[base + k + width]
            norm += w
        # Dividing by the total weight keeps the overall volume exactly the same.
        out[start:start + len(t)] = acc / norm
    return out
