"""Timing measurements on logic captures: edges, pulse widths, frequency,
duty cycle, channel-to-channel delay."""

import numpy as np


def edges(sig):
    """Return (rise_idx, fall_idx) sample indices of a bool signal."""
    s = sig.astype(np.int8)
    diff = np.diff(s)
    rise = np.nonzero(diff == 1)[0] + 1
    fall = np.nonzero(diff == -1)[0] + 1
    return rise, fall


def pulse_widths(sig, level=True, samplerate=None):
    """Widths (in samples, or seconds if samplerate given) of all pulses at
    `level`. Returns np.ndarray."""
    rise, fall = edges(sig)
    idx = np.sort(np.concatenate([rise, fall]))
    if len(idx) < 2:
        return np.array([])
    # start from first edge into `level`
    starts = idx[:-1]
    ends = idx[1:]
    lv = sig[starts]
    widths = (ends - starts)[lv == level]
    if samplerate:
        return widths / float(samplerate)
    return widths


def frequency(sig, samplerate, n_periods=10, invert=False):
    """Estimate frequency from the first n_periods full periods (high+low).
    invert=True: treat the logical signal as active-low (bench wiring where
    the LA input reads the complement of the physical line).
    Returns (freq_hz, duty_cycle) or (None, None) if not enough edges."""
    if invert:
        sig = ~sig
    rise, _ = edges(sig)
    if len(rise) < 2:
        return None, None
    rise = rise[: n_periods + 1]
    periods = np.diff(rise)
    period = float(np.median(periods)) / samplerate
    if period <= 0:
        return None, None
    freq = 1.0 / period
    high = pulse_widths(sig, level=True)
    if len(high):
        # duty from median high width vs period
        duty = float(np.median(high[:n_periods])) / samplerate / period
    else:
        duty = 0.0
    return freq, min(max(duty, 0.0), 1.0)


def measure_pwm(sig, samplerate):
    """Alias of frequency(); returns dict for reporting."""
    f, d = frequency(sig, samplerate)
    return {"freq_hz": f, "duty": d}


def delay_between(sig_a, sig_b, samplerate, edge="rise", n=5):
    """Median delay from edge on A to next same-type edge on B, seconds."""
    ra, fa = edges(sig_a)
    rb, fb = edges(sig_b)
    a = ra if edge == "rise" else fa
    b = rb if edge == "rise" else fb
    if len(a) == 0 or len(b) == 0:
        return None
    delays = []
    for x in a[:n]:
        nxt = b[b >= x]
        if len(nxt):
            delays.append((nxt[0] - x) / samplerate)
    if not delays:
        return None
    return float(np.median(delays))


def count_transitions(sig):
    rise, fall = edges(sig)
    return len(rise), len(fall)


def min_pulse_width(sig, samplerate, level=True):
    w = pulse_widths(sig, level=level)
    if len(w) == 0:
        return None
    return float(np.min(w)) / samplerate
