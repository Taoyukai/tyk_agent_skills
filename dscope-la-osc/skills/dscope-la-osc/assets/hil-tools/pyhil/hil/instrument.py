"""Device control layer: Python wrappers around the ds-capture CLI.

Two instrument classes make cross-project reuse a one-liner:

    from hil.instrument import DSLogicLA, DSCopeScope

    la = DSLogicLA()          # logic analyzer (DSLogic Plus / any DSLogic)
    cap = la.capture(samplerate="20M", samples="2M", channels=[0, 4],
                     mode="stream", vth=1.65, trigger={4: "r"})

    sc = DSCopeScope()        # oscilloscope (DSCope U3P100)
    acap = sc.capture(samplerate="10M", samples="1M", vdiv={0: 1.0},
                      coupling="dc", trigger={"source": "auto", "slope": "r"})

Both return loaded capture objects (hil.capture.LogicCapture /
AnalogCapture) with per-run metadata (thresholds, vdiv, coupling, trigger)
recorded in `cap.meta`. Data files go to `reports/` under the hil root by
default (timestamped prefixes, so runs never overwrite each other).

Device indices are not stable across re-plugs — find by name instead
(`DSLogicLA.find()` / `DSCopeScope.find()` do this automatically).
"""

import json
import os
import subprocess
import time

from .capture import load_capture

_HIL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 跨项目调用时可覆盖：环境变量 HIL_DSCAPTURE（exe 路径）/ HIL_OUT_DIR（数据输出目录），
# 或在构造时传 exe=/out_dir=
DEFAULT_EXE = os.environ.get("HIL_DSCAPTURE") or \
    os.path.join(_HIL_ROOT, "bin", "ds-capture.exe")
DEFAULT_OUT_DIR = os.environ.get("HIL_OUT_DIR") or \
    os.path.join(_HIL_ROOT, "reports")

# vendor/product of DreamSourceLab devices (for usb_reset)
USB_VID = "2A0E"


class InstrumentError(RuntimeError):
    pass


def _run(cmd, timeout=180):
    """Run ds-capture, return CompletedProcess. Raises InstrumentError on
    hard launch failure. Output decoded leniently (GLib may emit any bytes)."""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout,
                              text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise InstrumentError(f"ds-capture.exe not found: {cmd[0]} — build it first")
    except subprocess.TimeoutExpired:
        raise InstrumentError(f"ds-capture timed out after {timeout}s: {' '.join(cmd)}")


def list_devices(exe=None):
    """[{index, name}] as reported by `ds-capture list`."""
    exe = exe or DEFAULT_EXE
    proc = _run([exe, "list"], timeout=30)
    out = proc.stdout
    start = out.find("[")
    if start < 0:
        raise InstrumentError(f"unexpected `list` output: {out[:200]!r}")
    return json.loads(out[start:])


def find_device(name_substr, exe=None):
    """Return the device index whose name contains name_substr (case
    insensitive), or None."""
    sub = name_substr.lower()
    for d in list_devices(exe):
        if sub in d["name"].lower():
            return d["index"]
    return None


def dsview_running():
    """True if DSView.exe holds the USB (captures will fail with
    LIBUSB_ERROR_ACCESS / fall back to the demo device)."""
    try:
        proc = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DSView.exe"],
                              capture_output=True,
                              encoding="gbk", errors="replace")
        return "DSView.exe" in (proc.stdout or "")
    except OSError:
        return False


def usb_reset(pid="002B", exe=None):
    """Software re-plug (libusb bus reset) to clear stuck FX3/FPGA pipes.
    Run BEFORE a capture, in its own invocation."""
    exe = exe or DEFAULT_EXE
    return _run([exe, "reset", "--pid", pid], timeout=30)


class _Base:
    name_substr = ""
    friendly = ""

    def __init__(self, dev=None, out_dir=None, exe=None):
        self.exe = exe or DEFAULT_EXE
        self.out_dir = out_dir or DEFAULT_OUT_DIR
        self.dev = dev
        self.last_prefix = None
        self.last_cmd = None
        os.makedirs(self.out_dir, exist_ok=True)
        if self.dev is None:
            self.dev = self.find()
        if self.dev is None:
            raise InstrumentError(
                f"{self.friendly} not found — run `ds-capture.exe list` and "
                f"check DSView.exe is closed")

    def find(self):
        return find_device(self.name_substr, self.exe)

    def info(self):
        """Device details + config state as a dict."""
        proc = _run([self.exe, "info", "--dev", str(self.dev)], timeout=60)
        start = proc.stdout.find("{")
        if start < 0:
            raise InstrumentError(f"unexpected `info` output: {proc.stdout[:200]!r}")
        return json.loads(proc.stdout[start:])

    def _prefix(self, prefix):
        if prefix is None:
            prefix = os.path.join(
                self.out_dir,
                time.strftime("%Y%m%d_%H%M%S") + "_" + self.friendly.split()[0].lower())
        for ext in (".raw", ".json"):
            p = prefix + ext
            if os.path.exists(p):
                os.remove(p)
        return prefix

    def _capture(self, args, prefix, timeout_s):
        """Run one capture, then load and sanity-check the result."""
        prefix = self._prefix(prefix)
        cmd = [self.exe, "capture", "--dev", str(self.dev), "-o", prefix] + args
        self.last_cmd = cmd
        proc = _run(cmd, timeout=timeout_s + 90)
        tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        meta_path = prefix + ".json"
        if not os.path.exists(meta_path):
            raise InstrumentError(
                f"{self.friendly} capture produced no data (rc={proc.returncode}): "
                f"{tail or proc.stderr.strip()[-300:]}")
        cap = load_capture(prefix)
        cap.meta.setdefault("cmd", cmd)
        if cap.meta.get("status") != "ok":
            # keep the partial data, but surface the problem loudly
            cap.meta["stderr_tail"] = proc.stderr.strip()[-300:]
        self.last_prefix = prefix
        return cap

    @staticmethod
    def _as_list(v, n=None):
        """Accept scalar / dict keyed by channel / list."""
        if isinstance(v, dict):
            return v
        if isinstance(v, (list, tuple)):
            return list(v)
        return v  # scalar


class DSLogicLA(_Base):
    """DSLogic-family logic analyzer.

    capture() accepts:
        samplerate  "20M" / 20e6 etc (size string, ds-capture syntax)
        samples     "2M" / 2000000
        channels    None (all) or list of channel indices [0, 4]
        mode        "buffer" (FPGA DDR, default) or "stream" (USB real-time,
                    <=20MHz @16ch / 50MHz @6ch / 100MHz @3ch on USB2 Plus)
        threshold   3.3 or 5.0 — front-end level family (reconfigures FPGA)
        vth         fine threshold in volts, e.g. 1.65 (Plus supports it)
        filt        "none" or "1t" — 1-clock-cycle input glitch filter
        clock       "int" (default) or "ext" (external clock on CH15)
        clock_edge  "r"/"f" for external clock
        trigger     {ch: "r"|"f"|"h"|"l"} simple trigger, e.g. {4: "r"}
        atrig       advanced multi-stage pattern trigger spec string,
                    e.g. "0: 0=h,4=r; 1: 2=l logic=or cnt=3" (stages joined
                    by ';', cond chars r/f/h/l/x, per-stage logic/cnt/inv)
        trigpos     pre-trigger position in percent (0..100)
        timeout, retry
    """

    name_substr = "DSLogic"
    friendly = "DSLogic logic analyzer"

    def capture(self, prefix=None, samplerate="20M", samples="1M",
                channels=None, mode="buffer", threshold=None, vth=None,
                filt=None, clock=None, clock_edge=None,
                trigger=None, atrig=None, trigpos=0,
                timeout=30, retry=1):
        args = ["--samplerate", _size_str(samplerate),
                "--samples", _size_str(samples),
                "--timeout", str(timeout), "--retry", str(retry)]
        if mode == "stream":
            args.append("--stream")
        if channels:
            args += ["--ch", ",".join(str(c) for c in channels)]
        if threshold is not None:
            fam = "3v3" if abs(float(threshold) - 3.3) < 0.9 else "5v0"
            args += ["--th", fam]
        if vth is not None:
            args += ["--vth", str(vth)]
        if filt:
            args += ["--filter", str(filt)]
        if clock:
            args += ["--clock", str(clock)]
        if clock_edge:
            args += ["--clock-edge", str(clock_edge)]
        if trigger:
            args += ["--trigger",
                     ",".join(f"{ch}:{val}" for ch, val in trigger.items())]
        if atrig:
            args += ["--atrig", atrig]
        if trigpos:
            args += ["--trigpos", str(trigpos)]
        return self._capture(args, prefix, timeout)


class DSCopeScope(_Base):
    """DSCope-family oscilloscope (stream-mode acquisition on the wire).

    capture() accepts:
        samplerate  "10M" / 10e6 etc
        samples     "1M" / 1000000 (per channel)
        vdiv        volts/div: scalar, list [1.0, 0.5] or {0: 1.0, 1: 0.5}
                    (device table: 0.01/0.02/0.05/0.1/0.2/0.5/1/2 V)
        coupling    "dc"|"ac"|"gnd", list or per-ch dict
        factor      probe attenuation 1/10, list or per-ch dict
        bw          "full" or "20m" (20MHz bandwidth limit)
        offset      front-end offset counts per ch (raw DAC), usually unused
        instant     True (default; stream/roll) or False (single-shot,
                    requires --normal path and 2-channel mode)
        trigger     dict {"source": "auto"|"ch0"|"ch1"|"ch0a1"|"ch0o1",
                          "slope": "r"|"f",
                          "level": {ch: volts} (approximate: needs zero
                                   calibration — prefer "level_raw"),
                          "level_raw": {ch: adc_counts} (exact),
                          "horiz": 0..90 (pre-trigger %),
                          "holdoff": us, "margin": int}
                    Pass trigger=None for free-running capture.
        zero_load   restore offsets from EEPROM first
        zero_cal    run live auto-zero during capture (saves to EEPROM)
        timeout, retry
    """

    name_substr = "DSCope"
    friendly = "DSCope oscilloscope"

    def capture(self, prefix=None, samplerate="10M", samples="1M",
                vdiv=None, coupling=None, factor=None, bw=None,
                offset=None, instant=True, trigger=None,
                zero_load=False, zero_cal=False,
                timeout=30, retry=2, mode="osc"):
        """mode: "osc" (oscilloscope, up to ~500MS/s) or "daq" (Data
        Acquisition — slow continuous capture, clamped to 500kS/s by the
        driver). Both return AnalogCapture with compensated volts()."""
        args = ["--mode", mode,
                "--samplerate", _size_str(samplerate),
                "--samples", _size_str(samples),
                "--timeout", str(timeout), "--retry", str(retry)]
        if vdiv is not None:
            args += ["--vdiv", _spec_str(vdiv, scale=1000.0, small_is_volts=True)]
        if coupling is not None:
            args += ["--coupling", _spec_str(coupling)]
        if factor is not None:
            args += ["--factor", _spec_str(factor)]
        if bw is not None:
            args += ["--bw", str(bw)]
        if offset is not None:
            args += ["--offset", _spec_str(offset)]
        if not instant:
            args.append("--normal")
        if trigger:
            args += self._trigger_args(trigger)
        if zero_load:
            args.append("--zero-load")
        if zero_cal:
            args.append("--zero-cal")
        return self._capture(args, prefix, timeout)

    @staticmethod
    def _trigger_args(t):
        args = []
        if t.get("source"):
            args += ["--trig-src", str(t["source"])]
        if t.get("slope"):
            args += ["--trig-slope", str(t["slope"])]
        if t.get("level") is not None:
            args += ["--trig-level", _spec_str(t["level"])]
        if t.get("level_raw") is not None:
            args += ["--trig-level-raw", _spec_str(t["level_raw"])]
        if t.get("horiz") is not None:
            args += ["--horiz-pos", str(t["horiz"])]
        if t.get("holdoff") is not None:
            args += ["--trig-holdoff", str(t["holdoff"])]
        if t.get("margin") is not None:
            args += ["--trig-margin", str(t["margin"])]
        return args


def _size_str(v):
    """Pass size strings through; format raw ints without float noise."""
    if isinstance(v, str):
        return v
    return str(int(v))


def _spec_str(v, scale=1.0, small_is_volts=False):
    """Format a scalar/list/dict option as a comma list in channel order.
    Numbers: small_is_volts scales values < 10 (volts) up to the CLI unit.
    Strings (dc/ac/..., 'none') pass through unchanged."""
    if isinstance(v, dict):
        vals = [v[k] for k in sorted(v)]
    elif isinstance(v, (list, tuple)):
        vals = list(v)
    else:
        vals = [v]
    out = []
    for x in vals:
        if isinstance(x, str):
            out.append(x)
            continue
        x = float(x)
        if small_is_volts and x < 10:
            x = x * scale
        out.append(str(int(x)) if float(x).is_integer() else f"{x:g}")
    return ",".join(out)
