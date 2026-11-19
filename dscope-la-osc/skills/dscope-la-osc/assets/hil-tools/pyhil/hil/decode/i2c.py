"""I2C decoder for LogicCapture data (SDA/SCL)."""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


@dataclass
class I2cEvent:
    kind: str                 # 'start' | 'stop' | 'addr' | 'data' | 'ack' | 'nack'
    sample: int
    value: Optional[int] = None
    read: Optional[bool] = None  # for addr: True if read

    def __repr__(self):
        s = f"{self.kind}@{self.sample}"
        if self.value is not None:
            if self.kind == "addr":
                s += f" 0x{self.value:02X}{'r' if self.read else 'w'}"
            else:
                s += f" 0x{self.value:02X}"
        return s


@dataclass
class I2cTransaction:
    start_sample: int
    stop_sample: Optional[int] = None
    addr: Optional[int] = None
    read: Optional[bool] = None
    acked: Optional[bool] = None
    events: List[I2cEvent] = field(default_factory=list)
    data: bytearray = field(default_factory=bytearray)

    def __repr__(self):
        return (f"I2C(addr=0x{self.addr:02X}{'r' if self.read else 'w'}, "
                f"ack={'Y' if self.acked else 'N'}, "
                f"data={self.data.hex(' ')}, "
                f"@{self.start_sample}..{self.stop_sample})")


def decode_i2c(sda, scl, samplerate):
    """Decode I2C bus activity. Returns list of I2cTransaction."""
    n = min(len(sda), len(scl))
    sda = sda[:n].astype(np.int8)
    scl = scl[:n].astype(np.int8)

    trans: List[I2cTransaction] = []
    cur = None
    bits: List[int] = []
    bit_positions: List[int] = []
    scl_prev = 1
    sda_prev = 1

    for i in range(n):
        s, d = int(scl[i]), int(sda[i])
        if s == 1 and sda_prev == 0 and d == 1 and scl_prev == 1:
            # STOP
            if cur is not None:
                cur.stop_sample = i
                trans.append(cur)
                cur = None
            bits = []
        elif s == 1 and scl_prev == 1 and sda_prev == 1 and d == 0:
            # START
            cur = I2cTransaction(start_sample=i)
            bits = []
            bit_positions = []
        elif scl_prev == 0 and s == 1:
            # rising edge: capture SDA bit
            if cur is not None:
                bits.append(d)
                bit_positions.append(i)
                if len(bits) == 9:
                    byte = 0
                    for b in bits[:8]:
                        byte = (byte << 1) | b
                    ack = bits[8] == 0
                    ev_val = None
                    kind = "data"
                    if cur.addr is None:
                        kind = "addr"
                        cur.addr = byte >> 1
                        cur.read = bool(byte & 1)
                        cur.acked = ack
                        ev_val = byte
                    else:
                        cur.data.append(byte)
                        ev_val = byte
                    cur.events.append(
                        I2cEvent(kind, bit_positions[0], ev_val, cur.read if kind == "addr" else None))
                    cur.events.append(
                        I2cEvent("ack" if ack else "nack", bit_positions[8]))
                    bits = []
                    bit_positions = []
        scl_prev = s
        sda_prev = d

    if cur is not None:
        trans.append(cur)
    return trans
