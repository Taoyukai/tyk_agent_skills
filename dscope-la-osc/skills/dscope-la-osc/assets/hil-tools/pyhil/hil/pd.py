"""Run DSView/sigrok protocol decoder scripts (PDs) on numpy sample data.

DSView ships ~149 official protocol decoders as Python scripts in
`<DSView>/decoders/<pd>/pd.py` (sigrok api_version 3, DSView fork dialect).
This module embeds them in our analysis layer without DSView itself:
a `sigrokdecode` module shim implements the framework-side API surface
(wait/put/register/metadata/samplenum/matched) over numpy arrays.

Condition types supported (per DSView libsigrokdecode4DSL):
  {slot: 'r'|'f'|'e'}        rising/falling/any edge at that sample
  {slot: 0|1} ('l'/'h')      level at that sample
  {'skip': N}                advance N samples, match there
  a list of dicts = alternatives; the returned `matched` bitmask tells
  which alternative(s) matched.

Usage:
    from hil.pd import run_pd
    out = run_pd('0-uart', {'rxtx': sig_array}, samplerate=1e6,
                 options={'baudrate': 115200})
    anns = out.anns[out.out_ann]   # [(ss, es, [ann_class, [text, ...]]), ...]
"""

import importlib.util
import os
import sys
import types

import numpy as np

def _find_dsview_decoders():
    """Locate protocol decoder scripts, in priority order:

    1. DSVIEW_DIR env (explicit override, points at a DSView install dir)
    2. bundled copy at <HIL_ROOT>/decoders — ships with the plugin, so
       decoding works without DSView installed; also keeps behavior
       consistent when DSView happens to be installed
    3. an installed DSView on any drive (fallback for self-managed
       HIL_TOOLS deployments without the bundled decoders)
    """
    env = os.environ.get("DSVIEW_DIR")
    if env:
        return os.path.join(env, "decoders")
    bundled = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "decoders")
    if os.path.isdir(bundled):
        return bundled
    drives = [d for d in "CDEFGH" if os.path.exists(f"{d}:\\")]
    cands = [rf"{d}:\{base}\DSView\decoders"
             for d in drives
             for base in ("Program Files", "Program Files (x86)")]
    cands.append(r"C:\DSView\decoders")
    for c in cands:
        if os.path.isdir(c):
            return c
    return bundled


DEFAULT_DECODERS_ROOT = _find_dsview_decoders()

OUTPUT_ANN = 1
OUTPUT_BINARY = 2
OUTPUT_META = 3
OUTPUT_PYTHON = 4
SRD_CONF_SAMPLERATE = 0

_INF = 1 << 62


class EndOfData(Exception):
    """Raised from wait() when the sample stream ends (PD decode loop ends)."""


_active = None  # currently-decoding PDRunner (single-threaded by design)


class _DecoderBase:
    """srd.Decoder base — methods delegate to the active runner."""

    def put(self, ss, es, out_id, data):
        _active.put(ss, es, out_id, data)

    def wait(self, conds=None):
        return _active.wait(self, conds)

    def register(self, output_type, **kw):
        return _active.register(output_type, **kw)

    def has_channel(self, slot):
        return _active.has_channel(slot)

    def printlog(self, *args, **kw):
        print("[pd]", *args)


def _build_sigrokdecode_module():
    m = types.ModuleType("sigrokdecode")
    m.Decoder = _DecoderBase
    m.OUTPUT_ANN = OUTPUT_ANN
    m.OUTPUT_BINARY = OUTPUT_BINARY
    m.OUTPUT_META = OUTPUT_META
    m.OUTPUT_PYTHON = OUTPUT_PYTHON
    m.SRD_CONF_SAMPLERATE = SRD_CONF_SAMPLERATE
    return m


_SIGROKDECODE = _build_sigrokdecode_module()


class PDRunner:
    """Instantiate one PD and decode a set of numpy bool arrays."""

    def __init__(self, pd_name, channels_by_id, samplerate,
                 options=None, decoders_root=DEFAULT_DECODERS_ROOT,
                 instantiate=True, deliver_metadata=True):
        self.decoders_root = decoders_root
        self.samplerate = int(samplerate)
        self.anns = {}
        self._next_out_id = 0
        self._cursor = 0

        dec = load_pd(pd_name, decoders_root)
        self.dec = dec
        dec_cls = dec.Decoder  # channels/options/annotations live on the class

        # resolve requested channels by id -> slot index, defaults for the rest
        declared = getattr(dec_cls, "channels", None) or getattr(dec, "channels", ())
        slot_arrays = []
        for slot, ch in enumerate(declared):
            cid = ch["id"] if isinstance(ch, dict) else ch.id
            if cid in channels_by_id and channels_by_id[cid] is not None:
                slot_arrays.append(np.asarray(channels_by_id[cid], dtype=np.int8))
            else:
                slot_arrays.append(None)
        self.slots = slot_arrays
        self.n_slots = len(declared)

        # per-channel match position indexes
        self._rises, self._falls, self._changes = [], [], []
        self._ones, self._zeros = [], []
        for arr in slot_arrays:
            if arr is None:
                self._rises.append(None)
                continue
            d = np.diff(arr)
            self._rises.append(np.flatnonzero(d == 1) + 1)
            self._falls.append(np.flatnonzero(d == -1) + 1)
            self._changes.append(np.flatnonzero(d != 0) + 1)
            self._ones.append(np.flatnonzero(arr == 1))
            self._zeros.append(np.flatnonzero(arr == 0))

        # options: declared defaults + user overrides
        merged = {}
        for opt in (getattr(dec_cls, "options", None) or getattr(dec, "options", ())):
            merged[opt["id"]] = opt.get("default")
        if options:
            merged.update(options)
        self.options = merged

        # make the sigrokdecode module available, then instantiate the PD
        self._prev_mod = sys.modules.get("sigrokdecode")
        sys.modules["sigrokdecode"] = _SIGROKDECODE
        global _active
        _active = self
        try:
            self.inst = dec.Decoder()
            self.inst.options = dict(merged)
            if hasattr(self.inst, "reset"):
                self.inst.reset()
            if hasattr(self.inst, "start"):
                self.inst.start()
            if deliver_metadata and hasattr(self.inst, "metadata"):
                self.inst.metadata(SRD_CONF_SAMPLERATE, self.samplerate)
            self.inst.samplerate = self.samplerate
        finally:
            _active = None
            if self._prev_mod is not None:
                sys.modules["sigrokdecode"] = self._prev_mod
            else:
                sys.modules.pop("sigrokdecode", None)

    # -------------------------------------------------- framework callbacks

    def put(self, ss, es, out_id, data):
        self.anns.setdefault(out_id, []).append((int(ss), int(es), data))

    def register(self, output_type, **kw):
        oid = self._next_out_id
        self._next_out_id += 1
        return oid

    def has_channel(self, slot):
        return 0 <= slot < self.n_slots and self.slots[slot] is not None

    # --------------------------------------------------------- wait engine

    def _sig_len(self):
        for arr in self.slots:
            if arr is not None:
                return len(arr)
        return 0

    def _next_match(self, slot, cond, lo):
        """Smallest sample index >= lo satisfying one (slot, cond) pair."""
        if slot < 0 or slot >= self.n_slots or self.slots[slot] is None:
            return _INF
        if cond == "r":
            arr = self._rises[slot]
        elif cond == "f":
            arr = self._falls[slot]
        elif cond == "e":
            arr = self._changes[slot]
        elif cond in (1, "h"):
            arr = self._ones[slot]
        elif cond in (0, "l"):
            arr = self._zeros[slot]
        else:
            return _INF
        if arr is None or len(arr) == 0:
            return _INF
        k = np.searchsorted(arr, lo)
        return int(arr[k]) if k < len(arr) else _INF

    def _alt_match_from(self, alt, lo):
        """First position >= lo where every pair in one alternative holds.
        Returns (pos or _INF, driving_pairs)."""
        pairs = list(alt.items())
        if not pairs:
            p = lo
            return (p if p < _INF else _INF)
        slot0, cond0 = pairs[0][0], pairs[0][1]
        p = self._next_match(slot0, cond0, lo)
        while p < _INF:
            ok = True
            for slot, cond in pairs[1:]:
                if self._next_match(slot, cond, p) != p:
                    ok = False
                    break
            if ok:
                return p
            p = self._next_match(slot0, cond0, p + 1)
        return _INF

    def wait(self, dec_obj, conds=None):
        if conds is None:
            conds = {"skip": 1}
        if isinstance(conds, dict):
            alternatives = [conds]
        else:
            alternatives = list(conds)

        lo = self._cursor
        best = _INF
        for alt in alternatives:
            if "skip" in alt:
                # absolute-ish: skip N samples from the current position
                p = lo + int(alt["skip"])
                if p < lo:
                    p = lo
                best = min(best, p)
            else:
                p = self._alt_match_from(alt, lo)
                best = min(best, p)

        if best >= _INF or best >= self._sig_len():
            raise EndOfData()

        matched = 0
        for i, alt in enumerate(alternatives):
            if "skip" in alt:
                if lo + int(alt["skip"]) == best:
                    matched |= 1 << i
            elif self._alt_holds_at(alt, best):
                matched |= 1 << i

        values = []
        for arr in self.slots:
            if arr is None or best >= len(arr):
                values.append(0)
            else:
                values.append(int(arr[best]))

        self._cursor = best + 1
        self.inst.samplenum = best
        self.inst.matched = matched
        return tuple(values)

    def _alt_holds_at(self, alt, p):
        for slot, cond in alt.items():
            if slot < 0 or slot >= self.n_slots or self.slots[slot] is None:
                return False
            if p >= len(self.slots[slot]) or p < 1:
                return False
            v = int(self.slots[slot][p])
            prev = int(self.slots[slot][p - 1])
            if cond == "r" and not (prev == 0 and v == 1):
                return False
            if cond == "f" and not (prev == 1 and v == 0):
                return False
            if cond == "e" and prev == v:
                return False
            if cond in (0, "l") and v != 0:
                return False
            if cond in (1, "h") and v != 1:
                return False
        return True

    # -------------------------------------------------------------- run

    def run(self):
        """Drive the PD's decode() generator to completion."""
        global _active
        prev = _active
        _active = self
        try:
            for _ in self.inst.decode():
                pass
        except EndOfData:
            pass
        finally:
            _active = prev
        return self


# ------------------------------------------------------------------ helpers

_PD_CACHE = {}


def load_pd(pd_name, decoders_root=DEFAULT_DECODERS_ROOT):
    """Load a decoder module by directory name ('0-uart') or by id ('0:uart')."""
    key = (pd_name, decoders_root)
    if key in _PD_CACHE:
        return _PD_CACHE[key]

    root = os.path.normpath(decoders_root)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"decoders root not found: {root}")

    dirname = pd_name
    if pd_name not in os.listdir(root):
        # resolve by decoder id: scan pd.py files for id = '<pd_name>'
        dirname = None
        for d in os.listdir(root):
            path = os.path.join(root, d, "pd.py")
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("id =") or s.startswith("id="):
                        if pd_name in s:
                            dirname = d
                        break
                    if s and not s.startswith(("#", "i")):
                        break
            if dirname:
                break
        if not dirname:
            raise FileNotFoundError(f"decoder '{pd_name}' not found in {root}")

    path = os.path.join(root, dirname, "pd.py")
    prev = sys.modules.get("sigrokdecode")
    sys.modules["sigrokdecode"] = _SIGROKDECODE
    sys.path.insert(0, root)
    try:
        spec = importlib.util.spec_from_file_location("dsview_pd_" + dirname.replace("-", "_").replace(":", "_"), path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    finally:
        if prev is not None:
            sys.modules["sigrokdecode"] = prev
        else:
            sys.modules.pop("sigrokdecode", None)
        sys.path.remove(root)

    _PD_CACHE[key] = mod
    return mod


def run_pd(pd_name, channels_by_id, samplerate, options=None,
           decoders_root=DEFAULT_DECODERS_ROOT):
    """Convenience: build runner, run decode, return the runner (see .anns)."""
    runner = PDRunner(pd_name, channels_by_id, samplerate,
                      options=options, decoders_root=decoders_root)
    return runner.run()


# ------------------------------------------------------------- conveniences

def _ann_items(runner, class_idx):
    """All annotations of class `class_idx` across ANN outputs, in order."""
    items = []
    for oid, lst in runner.anns.items():
        for ss, es, payload in lst:
            try:
                cls, texts = payload[0], payload[1]
            except (TypeError, IndexError):
                continue
            if cls == class_idx:
                items.append((ss, es, texts))
    items.sort(key=lambda t: t[0])
    return items


def pd_uart_bytes(sig, samplerate, baud=115200, parity="none", invert="no",
                  data_bits=8, decoders_root=DEFAULT_DECODERS_ROOT):
    """Decode UART bytes with the official DSView 0-uart decoder script."""
    runner = run_pd("0-uart", {"rxtx": sig}, samplerate,
                    options={"baudrate": baud, "parity_type": parity,
                             "invert": invert, "num_data_bits": data_bits},
                    decoders_root=decoders_root)
    out = bytearray()
    for ss, es, texts in _ann_items(runner, 0):
        txt = texts[0]
        if txt.startswith("@"):
            out.append(int(txt[1:], 16))
    return bytes(out)


def pd_pwm_periods(sig, samplerate, polarity="active-high",
                   decoders_root=DEFAULT_DECODERS_ROOT):
    """Decode PWM with the official pwm decoder script.
    Returns [{'ss': s, 'period_s': p, 'duty': d}, ...]."""
    runner = run_pd("pwm", {"data": sig}, samplerate,
                    options={"polarity": polarity}, decoders_root=decoders_root)
    out = []
    for ss, es, texts in _ann_items(runner, 0):
        if not isinstance(texts, list) or not texts or not isinstance(texts[0], str):
            continue  # binary/meta outputs also carry class index 0
        txt = texts[0]  # like '49.900000%'
        try:
            duty = float(txt.rstrip("%")) / 100.0
        except ValueError:
            continue
        period = (es - ss) / float(samplerate)
        out.append({"ss": ss, "period_s": period, "duty": duty})
    return out
