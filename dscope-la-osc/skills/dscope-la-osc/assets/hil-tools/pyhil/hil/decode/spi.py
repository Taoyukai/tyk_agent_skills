"""SPI decoder for LogicCapture data."""

from dataclasses import dataclass, field
from typing import List

import numpy as np


@dataclass
class SpiTransaction:
    cs_low_sample: int
    cs_high_sample: int
    mosi_bytes: List[int] = field(default_factory=list)
    miso_bytes: List[int] = field(default_factory=list)

    @property
    def mosi(self) -> bytes:
        return bytes(self.mosi_bytes)

    @property
    def miso(self) -> bytes:
        return bytes(self.miso_bytes)


def _sample_center(sig, i):
    return bool(sig[i]) if 0 <= i < len(sig) else False


def decode_spi(sck, mosi, miso, cs, samplerate, cpol=0, cpha=0,
               bit_order="msb"):
    """Decode SPI transactions gated by chip select (active low).

    cpol/cpha: standard SPI mode parameters (mode = cpol*2 + cpha).
    bit_order: 'msb' or 'lsb'.
    Returns list of SpiTransaction.
    """

    def prev_edge_idx(arr, x):
        """largest value in arr < x"""
        idx = np.searchsorted(arr, x, side="left")
        return int(arr[idx - 1]) if idx > 0 else None

    def next_edge_idx(arr, x):
        idx = np.searchsorted(arr, x, side="right")
        return int(arr[idx]) if idx < len(arr) else None

    sck_int = sck.astype(np.int8)
    diff = np.diff(sck_int)
    rising = np.nonzero(diff == 1)[0] + 1
    falling = np.nonzero(diff == -1)[0] + 1

    # idle level of SCK given cpol
    idle = bool(cpol)
    latches = rising if idle is False else falling  # edge to idle->active
    if cpha == 0:
        # sample on leading (idle->active) edge
        leading = rising if not idle else falling
        trailing = falling if not idle else rising
        sample_edges = leading
    else:
        sample_edges = falling if not idle else rising

    cs_int = cs.astype(np.int8)
    csd = np.diff(cs_int)
    cs_fall = np.nonzero(csd == -1)[0] + 1
    cs_rise = np.nonzero(csd == 1)[0] + 1
    # ensure pairing: starts from a fall, ends at next rise
    trans = []
    for start in cs_fall:
        ends = cs_rise[cs_rise > start]
        if not len(ends):
            end = len(cs) - 1
        else:
            end = int(ends[0])
        t = SpiTransaction(int(start), end)
        bits_mosi = []
        bits_miso = []
        # sampling edges within [start, end]
        edges = sample_edges[(sample_edges > start) & (sample_edges < end)]
        for e in edges:
            bits_mosi.append(1 if _sample_center(mosi, e) else 0)
            bits_miso.append(1 if _sample_center(miso, e) else 0)
        for k in range(0, len(bits_mosi) // 8 * 8, 8):
            chunk = bits_mosi[k : k + 8]
            chunk2 = bits_miso[k : k + 8]
            if bit_order == "msb":
                t.mosi_bytes.append(int("".join(map(str, chunk)), 2))
                t.miso_bytes.append(int("".join(map(str, chunk2)), 2))
            else:
                t.mosi_bytes.append(int("".join(map(str, chunk[::-1])), 2))
                t.miso_bytes.append(int("".join(map(str, chunk2[::-1])), 2))
        trans.append(t)
    return trans
