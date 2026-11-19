"""Assertion helpers for hardware-in-the-loop tests.

Each check returns a CheckResult and records it in the active Report when
one is open, so a test script reads as a flat list of expectations.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .capture import LogicCapture
from . import timing
from .decode import (decode_uart_bytes, decode_spi, decode_i2c,
                     decode_dshot)
from .pd import pd_uart_bytes, pd_pwm_periods


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


_active_results: List[CheckResult] = []


def results():
    return list(_active_results)


def reset_results():
    _active_results.clear()


def _record(name, passed, detail):
    r = CheckResult(name, bool(passed), detail)
    _active_results.append(r)
    print(("PASS  " if r.passed else "FAIL  ") + name + "  — " + detail)
    return r


# ---------------------------------------------------------------- logic


def expect_uart_bytes(cap: LogicCapture, ch, baud, expected: bytes,
                      name="uart_bytes", parity=None):
    """Decode with the official DSView 0-uart decoder script (PD); falls
    back to the built-in decoder if the PD runtime fails."""
    sig = cap.ch(ch)
    got = None
    how = "pd"
    try:
        got = pd_uart_bytes(sig, cap.samplerate, baud=baud,
                            parity=parity or "none")
    except Exception as e:
        how = f"builtin (pd failed: {type(e).__name__})"
        got = decode_uart_bytes(sig, cap.samplerate, baud=baud, parity=parity)
    ok = got == expected
    return _record(name, ok,
                   f"[{how}] expected {expected!r}, got {got!r}")


def expect_pwm_pd(cap: LogicCapture, ch, freq_hz, duty=None,
                  tol_rel=0.05, tol_duty=0.05, polarity="active-high",
                  name=None):
    """Assert PWM using the official DSView pwm decoder script."""
    sig = cap.ch(ch)
    periods = pd_pwm_periods(sig, cap.samplerate, polarity=polarity)
    nm = name or f"pwm_pd_ch{ch}"
    if not periods:
        return _record(nm, False, "pwm PD decoded no periods")
    f = 1.0 / float(np.median([p["period_s"] for p in periods]))
    d = float(np.median([p["duty"] for p in periods]))
    detail = f"frames={len(periods)} freq={f:.1f}Hz duty={d:.3f}"
    if abs(f - freq_hz) > freq_hz * tol_rel:
        return _record(nm, False, detail + f" (freq out of range)")
    if duty is not None and abs(d - duty) > tol_duty:
        return _record(nm, False, detail + " (duty out of range)")
    return _record(nm, True, detail)


def expect_uart_no_activity(cap: LogicCapture, ch, name="uart_idle"):
    sig = cap.ch(ch)
    idle = bool(sig.all())
    return _record(name, idle, "line all high (idle)" if idle else "activity seen on idle UART")


def expect_gpio_level(cap: LogicCapture, ch, level: bool,
                      name=None):
    sig = cap.ch(ch)
    ok = bool(sig.all()) == level
    return _record(name or f"gpio_level_ch{ch}", ok,
                   f"expected {'HIGH' if level else 'LOW'}, got {'HIGH' if sig.mean() == 1 else 'LOW' if sig.mean() == 0 else 'mixed'}")


def expect_pwm(cap: LogicCapture, ch, freq_hz, duty=None,
               tol_rel=0.05, tol_duty=0.05, name=None, invert=False):
    """Assert PWM frequency (and optionally duty) on a channel.
    invert=True: channel is active-low (complement-read bench wiring)."""
    sig = cap.ch(ch)
    if invert:
        sig = ~sig
    f, d = timing.frequency(sig, cap.samplerate)
    nm = name or f"pwm_ch{ch}"
    if f is None:
        return _record(nm, False, "no periodic signal found")
    if abs(f - freq_hz) > freq_hz * tol_rel:
        return _record(nm, False, f"freq {f:.1f}Hz out of range {freq_hz*(1-tol_rel):.1f}..{freq_hz*(1+tol_rel):.1f}")
    if duty is not None and d is not None:
        if abs(d - duty) > tol_duty:
            return _record(nm, False, f"duty {d:.3f} out of range {duty-tol_duty:.3f}..{duty+tol_duty:.3f}")
    return _record(nm, True, f"freq={f:.1f}Hz duty={d if d is None else round(d, 3)}")


def expect_transitions(cap: LogicCapture, ch, min_rise=None, max_rise=None,
                       name=None):
    sig = cap.ch(ch)
    rise, fall = timing.edges(sig)
    ok = True
    detail = f"{len(rise)} rising edges"
    if min_rise is not None and len(rise) < min_rise:
        ok, detail = False, detail + f" (< min {min_rise})"
    if max_rise is not None and len(rise) > max_rise:
        ok, detail = False, detail + f" (> max {max_rise})"
    return _record(name or f"transitions_ch{ch}", ok, detail)


def expect_pulse_width(cap: LogicCapture, ch, level=True, seconds=None,
                       tol_rel=0.1, name=None):
    """Assert all (or min) pulse widths near `seconds`."""
    sig = cap.ch(ch)
    w = timing.pulse_widths(sig, level=level, samplerate=cap.samplerate)
    nm = name or f"pulse_width_ch{ch}"
    if seconds is None or len(w) == 0:
        return _record(nm, len(w) > 0, f"{len(w)} pulses measured")
    ok = bool(np.all(np.abs(w - seconds) <= seconds * tol_rel))
    return _record(nm, ok, f"widths {w.min()*1e6:.1f}..{w.max()*1e6:.1f}us, expect {seconds*1e6:.1f}±{tol_rel*100:.0f}%")


def expect_delay(cap: LogicCapture, ch_a, ch_b, max_seconds, edge="rise",
                 name=None):
    d = timing.delay_between(cap.ch(ch_a), cap.ch(ch_b), cap.samplerate, edge=edge)
    nm = name or f"delay_ch{ch_a}_to_ch{ch_b}"
    if d is None:
        return _record(nm, False, "no edge pair found")
    ok = d <= max_seconds
    return _record(nm, ok, f"delay {d*1e6:.1f}us (limit {max_seconds*1e6:.1f}us)")


def expect_i2c_addr_read(cap: LogicCapture, sda, scl, addr, data=None,
                         name=None):
    trans = decode_i2c(cap.ch(sda), cap.ch(scl), cap.samplerate)
    nm = name or f"i2c_read_0x{addr:02X}"
    for t in trans:
        if t.addr == addr and t.read and (t.acked is True):
            if data is not None and not t.data.startswith(bytes(data)):
                return _record(nm, False, f"acked but data {bytes(t.data).hex()} != {bytes(data).hex()}")
            return _record(nm, True, f"acked, {len(t.data)} bytes: {bytes(t.data).hex()}")
    found = [(hex(t.addr), 'r' if t.read else 'w', t.acked) for t in trans]
    return _record(nm, False, f"no acked read of 0x{addr:02X}; saw {found}")


def expect_spi_exchange(cap: LogicCapture, cs, sck, mosi, miso,
                        expect_mosi: bytes = None, expect_miso: bytes = None,
                        name=None):
    trans = decode_spi(cap.ch(sck), cap.ch(mosi), cap.ch(miso), cap.ch(cs),
                       cap.samplerate)
    nm = name or f"spi_exchange"
    if not trans:
        return _record(nm, False, "no CS transaction seen")
    for t in trans:
        if expect_mosi is not None and t.mosi.startswith(expect_mosi):
            return _record(nm, True, f"mosi matched ({len(t.mosi)}B)")
        if expect_miso is not None and t.miso.startswith(expect_miso):
            return _record(nm, True, f"miso matched ({len(t.miso)}B)")
    return _record(nm, False,
                   f"transactions={len(trans)} first mosi={trans[0].mosi.hex()} miso={trans[0].miso.hex()}")


def expect_dshot(cap: LogicCapture, ch, rate=600, min_frames=None,
                 throttle=None, throttle_tol=2, require_crc_ok=True,
                 name=None):
    """Assert DSHOT activity on a channel.

    rate: nominal rate (600 etc.) or None to autodetect.
    throttle: expected throttle value (last/median frame); None = any.
    """
    frames, info = decode_dshot(cap.ch(ch), cap.samplerate, rate=rate)
    nm = name or f"dshot_ch{ch}"
    if not frames:
        return _record(nm, False, f"no frames decoded ({info})")
    ok = True
    detail = (f"{len(frames)} frames, rate={info.get('detected_rate')}, "
              f"crc_bad={sum(1 for f in frames if not f.crc_ok)}")
    if min_frames is not None and len(frames) < min_frames:
        ok, detail = False, detail + f" (< min {min_frames})"
    if require_crc_ok and any(not f.crc_ok for f in frames):
        ok, detail = False, detail + " (CRC errors present)"
    if throttle is not None:
        med_thr = int(np.median([f.throttle for f in frames]))
        if abs(med_thr - throttle) > throttle_tol:
            ok, detail = False, detail + f" (throttle {med_thr} != {throttle}±{throttle_tol})"
        else:
            detail += f", throttle~{med_thr}"
    return _record(nm, ok, detail)


# ---------------------------------------------------------------- analog


def expect_vpp_range(cap, ch, v_min, v_max, scale, name=None):
    """cap: AnalogCapture; scale = volts per LSB."""
    v = cap.volts(ch, scale)
    vpp = float(v.max() - v.min())
    ok = v_min <= vpp <= v_max
    return _record(name or f"vpp_ch{ch}", ok, f"Vpp={vpp:.3f}V expect {v_min}..{v_max}V")


def expect_mean_voltage(cap, ch, expected, tol, scale, name=None):
    v = cap.volts(ch, scale)
    m = float(v.mean())
    ok = abs(m - expected) <= tol
    return _record(name or f"vmean_ch{ch}", ok, f"mean={m:.3f}V expect {expected}±{tol}V")


def expect_ripple_below(cap, ch, vpp_limit, scale, name=None):
    v = cap.volts(ch, scale)
    r = float(v.max() - v.min())
    ok = r <= vpp_limit
    return _record(name or f"ripple_ch{ch}", ok, f"ripple_pp={r*1000:.1f}mV limit {vpp_limit*1000:.1f}mV")


def expect_freq_on_analog(cap, ch, freq_hz, scale=1.0, tol_rel=0.05,
                          fmin=None, name=None):
    v = cap.volts(ch, scale)
    f = _analog_freq(v, cap.samplerate, fmin)
    nm = name or f"analog_freq_ch{ch}"
    if f is None:
        return _record(nm, False, "no dominant frequency")
    ok = abs(f - freq_hz) <= freq_hz * tol_rel
    return _record(nm, ok, f"peak {f:.1f}Hz expect {freq_hz}±{tol_rel*100:.0f}%")


def _analog_freq(v, samplerate, fmin):
    from .analog import fft_peak_freq
    return fft_peak_freq(v, samplerate, fmin or 0.0)


# ---------------------------------------------------------------- generic


def expect_true(cond, name, detail=""):
    return _record(name, cond, detail)


def summary(results_list=None):
    rs = results_list if results_list is not None else results()
    npass = sum(1 for r in rs if r.passed)
    return f"{npass}/{len(rs)} checks passed"
