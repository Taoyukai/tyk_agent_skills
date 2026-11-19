"""UART decoder for LogicCapture data (LSB first, idle high)."""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


@dataclass
class UartFrame:
    start_sample: int
    byte: int
    stop_sample: int
    parity_error: Optional[bool] = None
    framing_error: bool = False


def _sample_at(sig, sample_rate, baud, pos):
    """Value of sig at fractional sample position."""
    i = int(round(pos))
    if i < 0:
        i = 0
    if i >= len(sig):
        i = len(sig) - 1
    return bool(sig[i])


def _auto_baud(sig, samplerate):
    """Estimate baud from shortest run length (a bit time)."""
    s = sig.astype(np.int8)
    change = np.nonzero(np.diff(s) != 0)[0] + 1
    if len(change) < 2:
        return None
    runs = np.diff(change)
    runs = np.append(runs, change[0])  # include first run
    unit = np.min(runs)  # shortest run ~ one bit
    if unit <= 0:
        return None
    return samplerate / float(unit)


def decode_uart(sig, samplerate, baud=None, data_bits=8, parity=None,
                stop_bits=1, max_frames=None):
    """Decode UART bytes from one channel.

    parity: None | 'even' | 'odd'
    Returns list of UartFrame.
    """
    if baud is None:
        baud = _auto_baud(sig, samplerate)
        if baud is None:
            return []
    samples_per_bit = samplerate / float(baud)

    frames: List[UartFrame] = []
    n = len(sig)
    i = 0
    while i < n - 1:
        # wait for falling edge (start bit)
        if sig[i] or not sig[i + 1]:
            i += 1
            continue
        start = i + 1
        # check start bit at its middle
        mid = start + samples_per_bit * 0.5
        if mid >= n or _sample_at(sig, samplerate, baud, mid):
            i += 1
            continue

        pos = start + samples_per_bit
        byte_val = 0
        ok = True
        for b in range(data_bits):
            center = pos + samples_per_bit * 0.5
            if center >= n:
                ok = False
                break
            if _sample_at(sig, samplerate, baud, center):
                byte_val |= 1 << b
            pos += samples_per_bit

        if not ok:
            break

        parity_err = None
        if parity in ("even", "odd"):
            center = pos + samples_per_bit * 0.5
            if center >= n:
                break
            pbit = 1 if _sample_at(sig, samplerate, baud, center) else 0
            ones = bin(byte_val).count("1") + pbit
            expect = 0 if parity == "even" else 1
            # total ones incl. parity should be even (even parity) / odd (odd)
            parity_err = bool((ones % 2) != expect)
            pos += samples_per_bit

        framing = False
        stop_center = pos + samples_per_bit * 0.5
        if stop_center < n:
            framing = not _sample_at(sig, samplerate, baud, stop_center)
        pos += samples_per_bit * stop_bits

        frames.append(UartFrame(start, byte_val, int(pos), parity_err, framing))
        i = int(pos)
        if max_frames and len(frames) >= max_frames:
            break
    return frames


def frames_to_bytes(frames):
    return bytes(f.byte for f in frames)


def decode_uart_bytes(sig, samplerate, baud=None, **kw):
    return frames_to_bytes(decode_uart(sig, samplerate, baud=baud, **kw))
