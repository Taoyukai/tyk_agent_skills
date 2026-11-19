"""Analog (oscilloscope) measurements for DSCope captures."""

import numpy as np


def stats(v):
    """Basic voltage statistics for one channel (np.float64 array in volts)."""
    if len(v) == 0:
        return None
    return {
        "vpp": float(v.max() - v.min()),
        "vmax": float(v.max()),
        "vmin": float(v.min()),
        "vmean": float(v.mean()),
        "vrms": float(np.sqrt(np.mean(v * v))),
        "vstd": float(v.std()),
    }


def ripple(v, samplerate=None):
    """AC ripple estimate: std of the signal and peak-to-peak of the
    high-frequency residual (mean removed)."""
    ac = v - v.mean()
    out = {"ripple_pp": float(ac.max() - ac.min()), "ripple_rms": float(np.sqrt(np.mean(ac * ac)))}
    return out


def fft_peak_freq(v, samplerate, fmin=0.0):
    """Dominant frequency via FFT (above fmin). Returns Hz or None."""
    if len(v) < 16:
        return None
    w = np.hanning(len(v))
    spec = np.abs(np.fft.rfft(v * w))
    freqs = np.fft.rfftfreq(len(v), 1.0 / samplerate)
    if fmin > 0:
        mask = freqs >= fmin
        if not mask.any():
            return None
        spec = spec[mask]
        freqs = freqs[mask]
    k = int(np.argmax(spec))
    if k == 0 or spec.max() <= 0:
        return None
    return float(freqs[k])


def measure_channel(v, samplerate):
    """Full measurement pass on one analog channel (np.float64 volts):
    levels, period/frequency/duty (hysteresis edge detection, robust to
    noise), level noise std, and 10-90% rise time of a middle edge.
    Returns a dict; freq/duty/edge_ns are None when not measurable."""
    out = {"vpp": None, "vmax": None, "vmin": None, "vmean": None,
           "freq": None, "duty": None, "edge_ns": None,
           "noise_high": None, "noise_low": None}
    if len(v) == 0:
        return out
    vmax, vmin = float(v.max()), float(v.min())
    vpp = vmax - vmin
    out.update(vpp=vpp, vmax=vmax, vmin=vmin, vmean=float(v.mean()))
    if vpp < 1e-9:
        return out

    rise, fall = edges_hysteresis(v)
    if len(rise) > 5 and len(fall) > 5:
        per = np.diff(rise) / samplerate
        f = 1.0 / float(np.median(per))
        # pair each rise with the first fall after it (handles captures
        # that start mid-high, where positional pairing would shift phase)
        idx = np.searchsorted(fall, rise)
        ok = idx < len(fall)
        high = fall[idx[ok]] - rise[ok]
        if len(high):
            out["freq"] = f
            out["duty"] = float(np.median(high)) / samplerate * f

    # noise on the two levels (std of samples in the outer 20% bands)
    hi_samples = v[v > vmax - vpp * 0.2]
    lo_samples = v[v < vmin + vpp * 0.2]
    # 平线/直流信号的高低带样本不足时，回退为全信号 std（仍是有效噪声度量）
    out["noise_high"] = float(np.std(hi_samples if len(hi_samples) > 10 else v) * 1000)  # mV
    out["noise_low"] = float(np.std(lo_samples if len(lo_samples) > 10 else v) * 1000)   # mV

    # 10-90% edge time at one rising edge
    if len(rise) > 1:
        r0 = rise[len(rise) // 2]
        seg = v[max(0, r0 - 5):r0 + max(10, int(samplerate * 2e-6))]
        if len(seg) > 10 and seg.max() > seg.min():
            t10 = np.flatnonzero(seg >= vmin + 0.1 * vpp)
            t90 = np.flatnonzero(seg >= vmin + 0.9 * vpp)
            if len(t10) and len(t90):
                out["edge_ns"] = float((t90[0] - t10[0]) / samplerate * 1e9)
    return out


def edges_hysteresis(v, frac=0.3):
    """Rising/falling edge indices with hysteresis (noise-immune):
    thresholds at frac and (1-frac) of the p2..p98 span. Mid-band samples
    keep their previous state."""
    lo, hi = np.percentile(v, 2), np.percentile(v, 98)
    if hi - lo < 1e-12:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    t_lo = lo + frac * (hi - lo)
    t_hi = lo + (1 - frac) * (hi - lo)
    s = np.where(v > t_hi, 1, np.where(v < t_lo, 0, -1)).astype(np.int8)
    mask = s == -1
    if mask.all():
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    first = np.flatnonzero(~mask)[0]
    cum = np.cumsum(~mask)
    s[mask] = s[~mask][np.maximum(cum[mask] - 1, 0)]
    s[:first] = s[first]
    d = np.diff(s)
    rise = np.flatnonzero(d == 1) + 1
    fall = np.flatnonzero(d == -1) + 1
    return rise, fall


def spectrum(v, samplerate, window="hann"):
    """Full amplitude spectrum (matching DSView's spectrum view role).

    Returns (freqs_hz, amplitude_v). Hann window, single-sided, amplitude
    corrected (coherent-gain compensated).
    """
    v = np.asarray(v, dtype=np.float64)
    n = len(v)
    if n < 16:
        return np.array([]), np.array([])
    if window == "hann":
        w = np.hanning(n)
        cga = 0.5          # coherent gain of hann
    else:
        w = np.ones(n)
        cga = 1.0
    spec = np.abs(np.fft.rfft((v - v.mean()) * w)) / (n / 2) / cga
    freqs = np.fft.rfftfreq(n, 1.0 / samplerate)
    return freqs, spec


def top_peaks(freqs, spec, n=5, fmin=0.0, min_gap_hz=None):
    """Top-n spectral peaks as [(freq_hz, amplitude_v), ...]"""
    if len(freqs) == 0:
        return []
    mask = freqs >= fmin
    f, s = freqs[mask], spec[mask]
    if min_gap_hz is None:
        min_gap_hz = (f[1] - f[0]) * 3 if len(f) > 1 else 1e9
    out = []
    order = np.argsort(s)[::-1]
    taken = []
    for k in order:
        if s[k] <= 0:
            break
        if any(abs(f[k] - t) < min_gap_hz for t in taken):
            continue
        out.append((float(f[k]), float(s[k])))
        taken.append(f[k])
        if len(out) >= n:
            break
    return out


def math_channel(a, b, op):
    """DSView math channels equivalent: ADD / SUB / MUL / DIV on two
    channels (arrays in volts). DIV guards divide-by-zero."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    op = op.upper()
    if op == "ADD":
        return a + b
    if op == "SUB":
        return a - b
    if op == "MUL":
        return a * b
    if op == "DIV":
        return np.divide(a, b, out=np.zeros_like(a), where=b != 0)
    raise ValueError(f"unknown op {op}")
