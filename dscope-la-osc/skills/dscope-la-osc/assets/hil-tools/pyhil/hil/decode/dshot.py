"""DSHOT (150/300/600/1200) decoder for LogicCapture data.

Unidirectional DSHOT: line idles LOW, each bit cell is a HIGH pulse whose
width encodes the bit (0 ~= 0.375*period, 1 ~= 0.75*period), MSB first.
Inverted/bidirectional wiring (idle HIGH, active-low pulses) is handled by
auto polarity detection.

Frame: 16 bits = 11 throttle + 1 telemetry-request + 4 CRC.
CRC = (v ^ v>>4 ^ v>>8) & 0xF over the 12-bit payload v.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

# nominal bit periods in us
DSHOT_RATES = {1200: 0.8333, 600: 1.6667, 300: 3.3333, 150: 6.6667}


@dataclass
class DshotFrame:
    start_sample: int
    end_sample: int
    word: int
    period_s: float
    bdshot: bool = False      # bidirectional DSHOT (inverted CRC nibble)

    @property
    def throttle(self) -> int:
        return (self.word >> 5) & 0x7FF

    @property
    def telemetry_req(self) -> bool:
        return bool((self.word >> 4) & 1)

    @property
    def crc(self) -> int:
        return self.word & 0xF

    @property
    def crc_ok(self) -> bool:
        v = self.word >> 4
        c = (v ^ (v >> 4) ^ (v >> 8)) & 0xF
        if self.bdshot:
            c = (~c) & 0xF
        return c == self.crc

    @property
    def is_special(self) -> bool:
        return self.throttle < 48

    def __repr__(self):
        return (f"Dshot(0x{self.word:04X} thr={self.throttle} "
                f"tel={int(self.telemetry_req)} "
                f"{'bd' if self.bdshot else ''}"
                f"crc={'OK' if self.crc_ok else 'BAD'})")


def _edges(sig):
    s = sig.astype(np.int8)
    rise = np.nonzero(np.diff(s) == 1)[0] + 1
    fall = np.nonzero(np.diff(s) == -1)[0] + 1
    return rise, fall


def _active_high(sig):
    """Return (signal_in_active_high_domain, inverted_flag).

    DSHOT normal idles LOW between frames; if the line is mostly HIGH it is
    an inverted/bidirectional wiring — flip it.
    """
    if len(sig) == 0:
        return sig, False
    if sig.mean() > 0.5:
        return ~sig, True
    return sig, False


def _pulse_widths(sig, samplerate):
    """High-pulse widths in samples, paired rise->next fall."""
    rise, fall = _edges(sig)
    if len(rise) == 0 or len(fall) == 0:
        return np.array([], dtype=np.int64), rise
    # align: for each rise, the first fall after it
    idx = np.searchsorted(fall, rise)
    ok = idx < len(fall)
    rise = rise[ok]
    idx = idx[ok]
    widths = fall[idx] - rise
    keep = widths > 0
    return widths[keep], rise[keep]


def _detect_period(spacings):
    """Bit period from consecutive rise spacings (within-frame ones)."""
    if len(spacings) == 0:
        return None
    med = np.median(spacings)
    close = spacings[spacings < med * 1.5]
    if len(close) < 4:
        return None
    return float(np.median(close))


def detect_dshot_rate(sig, samplerate):
    """Guess DSHOT rate; returns (rate_name, period_s) or (None, None)."""
    sig, _inv = _active_high(sig)
    rise, _ = _edges(sig)
    if len(rise) < 20:
        return None, None
    p = _detect_period(np.diff(rise))
    if p is None:
        return None, None
    p_us = p / samplerate * 1e6
    best = min(DSHOT_RATES.items(), key=lambda kv: abs(kv[1] - p_us))
    if abs(best[1] - p_us) / best[1] > 0.30:
        return None, p / samplerate
    return best[0], p / samplerate


def decode_dshot(sig, samplerate, rate=None, min_pulse_ns=150,
                 max_frames=None, bdshot=None):
    """Decode DSHOT frames from one channel.

    rate: force 600/300/150/1200 (default: autodetect from bit spacing).
    bdshot: bidirectional DSHOT CRC (inverted nibble). None = autodetect
    (normal CRC first; if most frames fail, retry as BDShot — typical for
    idle-high inverted wiring, where the ESC also replies on the line).
    Returns (frames, info_dict).
    """
    sig, inverted = _active_high(sig)
    widths, starts = _pulse_widths(sig, samplerate)
    info = {"inverted": inverted, "rate": rate, "period_s": None,
            "glitches_dropped": 0}

    # drop glitch pulses
    min_w = samplerate * min_pulse_ns * 1e-9
    keep = widths >= min_w
    info["glitches_dropped"] = int((~keep).sum())
    widths = widths[keep]
    starts = starts[keep]
    if len(widths) < 16:
        return [], info

    # bit period
    if rate is not None:
        period = DSHOT_RATES[rate] * 1e-6
    else:
        spacings = np.diff(starts)
        p = _detect_period(spacings)
        if p is None:
            return [], info
        period = p / samplerate
    info["period_s"] = period
    r, detected = detect_dshot_rate(sig, samplerate)
    info["detected_rate"] = r

    # frame grouping: new frame when gap between pulses > 1.6 * period
    breaks = np.nonzero(np.diff(starts) > period * samplerate * 1.6)[0] + 1
    groups = np.split(np.arange(len(starts)), breaks)

    thr_hi = period * samplerate * 0.5   # width >= 0.5*period -> bit 1

    frames: List[DshotFrame] = []
    for g in groups:
        if len(g) != 16:
            continue  # partial/corrupt frame
        w = 0
        for k, i in enumerate(g):
            if widths[i] >= thr_hi:
                w |= 1 << (15 - k)
        f = DshotFrame(int(starts[g[0]]), int(starts[g[-1]]), w, period)
        frames.append(f)
        if max_frames and len(frames) >= max_frames:
            break

    # CRC flavour: normal unidirectional vs bidirectional (inverted nibble)
    if frames:
        if bdshot is None:
            bad = sum(1 for f in frames if not f.crc_ok)
            if bad > len(frames) * 0.5:
                for f in frames:
                    f.bdshot = True
                info["bdshot"] = True
                # keep only frames that are now consistent
                frames = [f for f in frames if f.crc_ok] or frames
            else:
                info["bdshot"] = False
        else:
            for f in frames:
                f.bdshot = bool(bdshot)
            info["bdshot"] = bool(bdshot)
    return frames, info


def decode_dshot_values(frames):
    """Convenience: list of (throttle, telemetry_req, crc_ok)."""
    return [(f.throttle, f.telemetry_req, f.crc_ok) for f in frames]


# ---------------------------------------------------------------------------
# Bidirectional DSHOT (BDShot) ESC telemetry response — GCR decode
#
# Spec (Betaflight DShot API / brushlesswhoop handbook):
#   after each command frame (+~30us line-switch gap) the ESC drives a
#   21-bit sequence at 5/4 x the DSHOT bitrate (inverted line: idle HIGH).
#   NRZI-differential decode -> 20 GCR bits -> 4 x 5-bit codewords ->
#   16-bit value = eee(mmmmmmmmm)c: 3-bit exponent + 9-bit period base
#   + 4-bit CRC (uninverted DSHOT CRC over the 12-bit eRPM value).
#   period_us = base << exp;  eRPM = 60e6 / period_us.

# nibble -> 5-bit GCR codeword (MSB first within each codeword)
GCR_NIBBLE_TO_CODE = [
    0x19, 0x1B, 0x12, 0x13, 0x1D, 0x15, 0x16, 0x17,
    0x1A, 0x09, 0x0A, 0x0B, 0x1E, 0x0D, 0x0E, 0x0F,
]
GCR_CODE_TO_NIBBLE = {code: nib for nib, code in enumerate(GCR_NIBBLE_TO_CODE)}


def _gcr_bits_to_value(g):
    """20 GCR bits (list of 0/1) -> (value16, crc_ok). Nibble MSB first."""
    nibs = []
    for k in range(4):
        code = 0
        for b in g[k * 5:(k + 1) * 5]:
            code = (code << 1) | (b & 1)
        if code not in GCR_CODE_TO_NIBBLE:
            return None, False
        nibs.append(GCR_CODE_TO_NIBBLE[code])
    value = (nibs[0] << 12) | (nibs[1] << 8) | (nibs[2] << 4) | nibs[3]
    v12 = value >> 4
    crc_ok = ((v12 ^ (v12 >> 4) ^ (v12 >> 8)) & 0xF) == (value & 0xF)
    return value, crc_ok


@dataclass
class BdshotResponse:
    start_sample: int          # first wire sample of the response window
    value: int                 # 16-bit raw (eee/mantissa/crc)
    period_us: float           # decoded eRPM period in microseconds
    erpm: float                # electrical RPM = 60e6 / period_us
    crc_ok: bool
    polarity: int              # wire level that encoded a '1' (1=high, 0=low)

    def __repr__(self):
        return (f"BdshotResponse(period={self.period_us:.1f}us "
                f"eRPM={self.erpm:.0f} crc={'OK' if self.crc_ok else 'BAD'})")


def decode_bdshot_response(sig, samplerate, start_sample, dshot_rate=600,
                           n_bits=21):
    """Decode ONE BDShot ESC telemetry response starting near start_sample.

    sig:        bool array (TRUE polarity from the LA)
    start_sample: sample index ~30us after a command frame end (search is
                  attempted at several small offsets, CRC disambiguates)
    dshot_rate: the command frame's DSHOT rate (response runs at 5/4 speed)
    Returns BdshotResponse or None.
    """
    bit_s = (4.0 / 5.0) / (dshot_rate * 1e3)   # response bit period (s);
                                               # dshot_rate in kbit/s (600 = 600kbit/s)
    bit_n = bit_s * samplerate
    center0 = int(round(start_sample + bit_n * 0.5))
    best = None
    for off in (0, 2, -2, 4, -4, 6, -6):        # tolerate +/-150ns jitter
        c0 = center0 + int(round(off * bit_n * 0.05))
        centers = [c0 + int(round(i * bit_n)) for i in range(n_bits)]
        if centers[-1] >= len(sig) - 1 or centers[0] < 1:
            continue
        lv = np.array([1 if sig[c] else 0 for c in centers], dtype=np.int8)
        for pol in (1, 0):                       # try both wire polarity maps
            t = lv if pol == 1 else (1 - lv)
            g = [int(t[i + 1] ^ t[i]) for i in range(n_bits - 1)]  # 20 GCR bits
            value, crc_ok = _gcr_bits_to_value(g)
            if value is None or not crc_ok:
                continue
            v12 = value >> 4
            exp = (v12 >> 9) & 0x7
            mant = v12 & 0x1FF
            period_us = float(mant << exp) if exp < 16 else 0.0
            if period_us <= 0:
                continue
            r = BdshotResponse(int(centers[0]), value, period_us,
                               60e6 / period_us, True, pol)
            if best is None:
                best = r
            break
        if best:
            break
    return best


def decode_bdshot_responses(sig, samplerate, frames, dshot_rate=600,
                            gap_us=30.0):
    """Decode ESC responses following each command frame.

    frames: list of DshotFrame (command frames from decode_dshot)
    Returns list of BdshotResponse (one per frame with a valid response).
    """
    out = []
    dshot_bit_us = 1e6 / (dshot_rate * 1000)
    frame_us = 16 * dshot_bit_us
    for f in frames:
        end_t = (f.end_sample / samplerate * 1e6) + gap_us + frame_us * 0
        start = int(round((end_t + gap_us) * 1e-6 * samplerate))
        start = f.end_sample + int(round((gap_us) * 1e-6 * samplerate))
        r = decode_bdshot_response(sig, samplerate, start, dshot_rate=dshot_rate)
        if r is not None and r.crc_ok:
            out.append(r)
    return out
