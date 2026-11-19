"""Load ds-capture output (.raw + .json sidecar) into numpy arrays."""

import json
import os

import numpy as np


def _resolve_ch(meta, channels, index_or_name):
    """Resolve a channel selector to a data column index.

    Accepts: channel name string ("4"), data column position (0 = first
    captured slot), or physical channel index (4) when it is not a valid
    position but the metadata records it.
    """
    if isinstance(index_or_name, str):
        return channels.index(index_or_name)
    idx = int(index_or_name)
    if 0 <= idx < len(channels):
        return idx
    phys = [c["index"] for c in meta.get("channels", [])]
    if idx in phys:
        return phys.index(idx)
    raise IndexError(
        f"channel {index_or_name} not in capture (captured: "
        f"{list(enumerate(phys))})")


# DSCope U3P100: real zero baseline per vdiv (mV -> ADC code). DSView reads
# this value ("hw_offset") from every DSO packet header; ds-capture never
# exposes it, so it is calibrated here against DSView + PSU reference
# (2026-11-16, 10x probe; expected to be probe-factor independent).
# Ranges not listed fall back to the front-end offset register (128).
HW_OFFSET_CAL = {
    100: 130.0,   # 100 mV/div
    1000: 129.0,  # 1 V/div
}


class LogicCapture:
    """Logic-analyzer capture.

    Attributes:
        samplerate: float, samples per second
        channels:   list of channel names (enabled ones, in bit order)
        data:       np.ndarray (n_samples, n_channels) of bool
    """

    def __init__(self, meta, raw):
        self.meta = meta
        self.samplerate = float(meta["samplerate"])
        self.channels = [c["name"] for c in meta["channels"]]
        self.unitsize = max(1, (len(self.channels) + 7) // 8)
        fmt = meta.get("data", {}).get("format", "cross_data")
        nch = len(self.channels)
        if nch == 0 or len(raw) < 8:
            self.data = np.zeros((0, nch), dtype=bool)
            return

        if fmt == "cross_data":
            self._decode_cross(raw, nch)
        else:
            # plain sample-major bit packing (unitsize bytes per sample,
            # bit i of byte k == channel i of sample k, LSB first)
            n = len(raw) // self.unitsize
            packed = np.frombuffer(raw[: n * self.unitsize], dtype=np.uint8)
            bits = np.unpackbits(packed.reshape(n, self.unitsize), axis=1,
                                 bitorder="little")
            self.data = bits[:, :nch].astype(bool)

    def _decode_cross(self, raw, nch):
        """Decode LA_CROSS_DATA (DSLogic/demo native format).

        Layout (mirrors DSView LogicSnapshot::append_cross_payload):
        the payload is a stream of little-endian uint64 words; the words
        for channel j are at stream indices j, j+nch, j+2*nch, ...
        Each word carries 64 samples of that channel, bit k == sample k
        (LSB first).
        """
        nbytes = (len(raw) // 8) * 8
        words = np.frombuffer(raw[:nbytes], dtype="<u8")
        nblocks = len(words) // nch
        if nblocks == 0:
            self.data = np.zeros((0, nch), dtype=bool)
            return
        usable = nblocks * nch
        W = np.ascontiguousarray(words[:usable].reshape(nblocks, nch))
        B = W.view(np.uint8)                          # (nblocks, nch*8)
        bits = np.unpackbits(B, axis=1, bitorder="little")  # (nblocks, nch*64)
        # (nblocks, ch, 64 samples) -> (nblocks*64, ch)
        self.data = bits.reshape(nblocks, nch, 64).transpose(0, 2, 1) \
            .reshape(nblocks * 64, nch)

    def __len__(self):
        return len(self.data)

    def ch(self, index_or_name):
        """Return the bool array for one channel (name, capture position,
        or physical channel index — see _resolve_ch)."""
        return self.data[:, _resolve_ch(self.meta, self.channels, index_or_name)]

    def duration_s(self):
        return len(self.data) / self.samplerate


class AnalogCapture:
    """Oscilloscope (DSO) capture.

    Attributes:
        samplerate: float
        channels:   list of names
        raw:        np.ndarray (n_samples, n_channels) of raw ADC ints
        vdiv_mV / probe_factor / offset_counts: per-channel front-end
                    config recorded by ds-capture (when available)

    Voltage conversion: prefer volts() (auto scale from metadata + auto
    ground-baseline estimate), or pass an explicit scale in volts/count.
    """

    def __init__(self, meta, raw):
        self.meta = meta
        self.samplerate = float(meta["samplerate"])
        self.channels = [c["name"] for c in meta["channels"]]
        d = meta.get("data", {})
        dtype_name = d.get("type", "analog")
        unit_bits = int(d.get("unit_bits", 0))
        nch = int(d.get("channels", len(self.channels)) or len(self.channels))

        if dtype_name == "dso" or (dtype_name == "analog" and unit_bits <= 8):
            # DScope DSO wire format: 1 unsigned byte per sample per channel,
            # interleaved [s0ch0, s0ch1, s1ch0, ...] (DsoSnapshot::append_data)
            nch = max(1, nch)
            usable = (len(raw) // nch) * nch
            vals = np.frombuffer(raw[:usable], dtype=np.uint8)
            self.raw = vals.reshape(-1, nch)
            self.unit_bits = 8
        else:
            bps = int(d.get("bytes_per_sample", 2))
            usable = (len(raw) // bps) * bps
            dtype = np.dtype("<i%d" % bps)
            vals = np.frombuffer(raw[:usable], dtype=dtype)
            nch = max(1, nch)
            self.raw = vals.reshape(-1, nch)
            self.unit_bits = unit_bits or bps * 8

        sc = meta.get("scope") or {}
        self.vdiv_mV = sc.get("vdiv_mV")
        self.probe_factor = sc.get("factor")
        self.offset_counts = sc.get("offset")
        # hardware-reported zero baseline (future: ds-capture may record it)
        self.hw_offsets = sc.get("hw_offset")
        # ADC ref window: the code range mapped to the 10-division screen
        # span (per-profile, DSView measurement math uses it as the
        # volts denominator — NOT the nominal 256)
        self.ref_min = int(sc.get("ref_min", 10))
        self.ref_max = int(sc.get("ref_max", 245))

    def ch(self, index_or_name):
        return self.raw[:, _resolve_ch(self.meta, self.channels, index_or_name)]

    def volts_per_count(self, index_or_name):
        """Volts per ADC count from the capture metadata:
        vdiv * 10 divisions / (ref_max - ref_min) counts / probe factor.
        The ref window (per-profile, e.g. 10..245 for DSCope U3P100) is the
        same denominator DSView's measurement math uses — NOT the nominal
        256. None when the capture lacks scope metadata."""
        if not self.vdiv_mV:
            return None
        k = self._idx(index_or_name)
        vdiv_v = self.vdiv_mV[k] / 1000.0
        factor = 1
        if self.probe_factor and k < len(self.probe_factor):
            factor = self.probe_factor[k] or 1
        span = max(1, self.ref_max - self.ref_min)
        # 10x 探头: 前端输入电压 = 真实/10，真实伏特 = 输入 × factor
        return vdiv_v * 10.0 / span * factor

    def _idx(self, index_or_name):
        return _resolve_ch(self.meta, self.channels, index_or_name)

    def zero_code(self, index_or_name):
        """Raw code that corresponds to 0V: the front-end offset value
        recorded in the capture metadata (default 128). On the DSCope the
        ADC codes are INVERTED vs voltage (higher V = lower code, 0V at the
        offset code) — verified 2026-11-14 against known duty signals."""
        k = self._idx(index_or_name)
        if self.offset_counts and k < len(self.offset_counts):
            return int(self.offset_counts[k]) or 128
        return 128

    def baseline_counts(self, index_or_name):
        """Raw code treated as 0V.

        DSView's algorithm (verified in DSView source 2026-11-16): the UI
        converts samples as V = (hw_offset - code) * vdiv * factor * 10 /
        (ref_max - ref_min), where hw_offset is the per-channel zero code
        the FPGA reports in every DSO packet header (get_measure() in
        libsigrok4DSL dsl.c). ds-capture does not expose that field (its
        JSON `offset` is the offset REGISTER value, default 128), so the
        real per-vdiv zero comes from this calibration table instead,
        measured against DSView readings + PSU reference.
        """
        k = self._idx(index_or_name)
        if self.hw_offsets and k < len(self.hw_offsets) and self.hw_offsets[k]:
            return float(self.hw_offsets[k])
        vdiv = None
        if self.vdiv_mV and k < len(self.vdiv_mV):
            vdiv = self.vdiv_mV[k]
        cal = HW_OFFSET_CAL.get(int(vdiv) if vdiv else None)
        if cal is not None:
            return cal
        if self.offset_counts and k < len(self.offset_counts):
            return int(self.offset_counts[k]) or 128
        col = self.raw[:, k]
        vals, counts = np.unique(col, return_counts=True)
        return int(vals[np.argmax(counts)])

    def volts(self, index_or_name, scale=None, baseline=None, invert=None):
        """Convert one channel to volts (np.float64 array).

        DSCope DSO codes are INVERTED vs voltage (0V at the offset code,
        higher voltage = lower code) — verified against known duty signals.
        invert defaults to True for DSO/analog data; pass invert=False for
        raw-code semantics.

        scale:   volts per ADC count (magnitude). Default: metadata-derived
                 (volts_per_count).
        baseline: raw code treated as 0V. Default: front-end offset from
                 metadata (zero_code), else the most common code.
        """
        k = self._idx(index_or_name)
        if scale is None:
            scale = self.volts_per_count(k)
            if scale is None:
                raise ValueError(
                    "no scope metadata in capture — pass scale explicitly")
        if baseline is None:
            baseline = self.baseline_counts(k)
        if invert is None:
            invert = True  # DSCope DSO front-end delivers inverted codes
        v = self.ch(k).astype(np.float64)
        d = (baseline - v) if invert else (v - baseline)
        return d * scale

    def __len__(self):
        return len(self.raw)


def load_capture(prefix):
    """Load '<prefix>.json' + '<prefix>.raw'. Returns Logic or Analog capture."""
    jpath = prefix if prefix.endswith(".json") else prefix + ".json"
    rpath = os.path.splitext(jpath)[0] + ".raw"
    with open(jpath, "r", encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("status") not in ("ok", None):
        # still load, but expose status; caller may check .meta["status"]
        pass
    with open(rpath, "rb") as f:
        raw = f.read()
    dtype = meta.get("data", {}).get("type", "logic")
    if dtype in ("analog", "dso"):
        return AnalogCapture(meta, raw)
    return LogicCapture(meta, raw)
