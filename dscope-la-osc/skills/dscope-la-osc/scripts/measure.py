"""One-shot hardware capture + measurement for DSLogic Plus / DSCope U3P100.

Usage:
  python measure.py scope --ch 1 --vdiv 1 --rate 10M --samples 1M
  python measure.py scope --ch 1 --trigger '{"source":"auto","slope":"r"}'
  python measure.py la --ch 0,4 --rate 20M --samples 2M --stream --vth 1.65
  python measure.py la --ch 4 --trigger "4:r" --decode dshot
  python measure.py dual --la-ch 0 --sc-ch 0            # LA+示波器并发对比
  python measure.py scope --ch 0 --expect-freq 1000 --expect-duty 50 --plot
  python measure.py scope --ch 1 --json                 # machine-readable output
"""
import argparse
import json
import os
import subprocess
import sys
import time

# 采集栈解析：HIL_TOOLS 环境变量（自管部署）→ 插件自带 assets/hil-tools
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.environ.get("HIL_TOOLS") or os.path.join(_SKILL_DIR, "assets", "hil-tools")
sys.path.insert(0, os.path.join(TOOLS, "pyhil"))

import numpy as np  # noqa: E402
from hil.instrument import DSLogicLA, DSCopeScope, dsview_running  # noqa: E402
from hil import analog  # noqa: E402
from hil.decode import decode_dshot  # noqa: E402

FAILS = []


def check(name, ok, detail):
    print(("PASS  " if ok else "FAIL  ") + name + " — " + detail)
    if not ok:
        FAILS.append(name)


def out_dir():
    """数据输出目录：HIL_OUT_DIR → 自管部署的 <HIL_TOOLS>/captures →
    %LOCALAPPDATA%\\hil-tools\\captures（内置部署不写进插件缓存目录，
    插件更新会清掉缓存）。"""
    if os.environ.get("HIL_OUT_DIR"):
        d = os.environ["HIL_OUT_DIR"]
    elif os.environ.get("HIL_TOOLS"):
        d = os.path.join(os.environ["HIL_TOOLS"], "captures")
    else:
        d = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                         "hil-tools", "captures")
    os.makedirs(d, exist_ok=True)
    return d


def timing_us(sig, samplerate):
    """(edges, high_us, period_us) or None when no periodic signal."""
    s = sig.astype(np.int8)
    rise = np.flatnonzero(np.diff(s) == 1) + 1
    fall = np.flatnonzero(np.diff(s) == -1) + 1
    if len(rise) < 3:
        return None
    idx = np.searchsorted(fall, rise)
    ok = idx < len(fall)
    hi = (fall[idx[ok]] - rise[ok]) / samplerate * 1e6
    per = np.diff(rise) / samplerate * 1e6
    good = (per > 0.3 * np.median(per)) & (per < 5 * np.median(per))
    if not good.any():
        return None
    return len(rise), float(np.median(hi)), float(np.median(per[good]))


def apply_expects(a, freq_hz, duty_pct, vpp):
    """--expect-* 断言：PASS/FAIL 行 + 汇入退出码。"""
    if a.expect_freq:
        ok = freq_hz is not None and abs(freq_hz - a.expect_freq) <= a.expect_freq * a.expect_freq_tol / 100
        check(f"expect_freq={a.expect_freq}", ok,
              f"实测 {freq_hz and round(freq_hz, 2)}Hz ±{a.expect_freq_tol}%")
    if a.expect_duty is not None:
        ok = duty_pct is not None and abs(duty_pct - a.expect_duty) <= a.expect_duty_tol
        check(f"expect_duty={a.expect_duty}%", ok,
              f"实测 {duty_pct and round(duty_pct, 2)}% ±{a.expect_duty_tol}")
    if a.expect_vpp:
        ok = vpp is not None and abs(vpp - a.expect_vpp) <= a.expect_vpp * a.expect_vpp_tol / 100
        check(f"expect_vpp={a.expect_vpp}V", ok,
              f"实测 {vpp and round(vpp, 3)}V ±{a.expect_vpp_tol}%")


def plot_scope(cap, path, span_us=2000):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    n = min(len(cap), int(span_us * 1e-6 * cap.samplerate))
    t = np.arange(n) / cap.samplerate * 1e6
    fig, axes = plt.subplots(len(cap.channels), 1, figsize=(10, 1.2 * len(cap.channels) + 1),
                             sharex=True, squeeze=False)
    for ch in range(len(cap.channels)):
        axes[ch][0].plot(t, cap.volts(ch)[:n], lw=0.8)
        axes[ch][0].set_ylabel(f"CH{ch} (V)", fontsize=8)
        axes[ch][0].grid(alpha=0.3)
    axes[-1][0].set_xlabel("time (us)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def plot_la(cap, path, span_us=300):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    chs = list(range(len(cap.channels)))
    n = min(len(cap), int(span_us * 1e-6 * cap.samplerate))
    t = np.arange(n) / cap.samplerate * 1e6
    fig, axes = plt.subplots(len(chs), 1, figsize=(10, 0.6 * len(chs) + 1),
                             sharex=True, squeeze=False)
    for k, ch in enumerate(chs):
        axes[k][0].step(t, cap.ch(ch)[:n].astype(np.int8), where="post", lw=0.8)
        axes[k][0].set_ylim(-0.3, 1.3)
        axes[k][0].set_yticks([0, 1])
        axes[k][0].set_ylabel(f"ch{ch}", fontsize=8)
        axes[k][0].grid(alpha=0.3)
    axes[-1][0].set_xlabel("time (us)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def measure_scope(a):
    sc = DSCopeScope(out_dir=a.out)
    trig = json.loads(a.trigger) if a.trigger else None
    cap = sc.capture(prefix=a.prefix, samplerate=a.rate, samples=a.samples,
                     vdiv=a.vdiv, coupling=a.coupling, bw=a.bw, factor=a.factor,
                     mode=a.mode, trigger=trig,
                     zero_load=a.zero_load, timeout=a.timeout, retry=a.retry)
    if cap.meta.get("status") != "ok":
        print(f"ERROR: capture status={cap.meta.get('status')}")
        return 1
    result = {"device": "scope", "rate_hz": cap.samplerate, "channels": {}}
    for ch in range(len(cap.channels)):
        m = analog.measure_channel(cap.volts(ch), cap.samplerate)
        m["freq"] = round(m["freq"], 4) if m["freq"] else None
        m["duty_pct"] = round(m["duty"] * 100, 2) if m["duty"] is not None else None
        for k in ("vpp", "vmax", "vmin", "vmean"):
            m[k] = round(m[k], 4)
        m["state"] = "signal" if m["vpp"] > 0.05 else "idle/floating"
        result["channels"][f"CH{ch}"] = m
        print(f"CH{ch} [{m['state']}]: Vpp={m['vpp']:.3f}V Vmax={m['vmax']:.3f}V "
              f"Vmin={m['vmin']:.3f}V Vmean={m['vmean']:.3f}V")
        if m["freq"] and m["duty_pct"] is not None:
            extra = []
            if m["edge_ns"] is not None:
                extra.append(f"edge10-90={m['edge_ns']:.0f}ns")
            extra.append(f"noise(H/L)={m['noise_high']:.0f}/{m['noise_low']:.0f}mV rms")
            print(f"      freq={m['freq']:.4g}Hz duty={m['duty_pct']}% " + " ".join(extra))
        if ch == a.ch:
            apply_expects(a, m["freq"], m["duty_pct"], m["vpp"])
            result["measured"] = {"freq_hz": m["freq"], "duty_pct": m["duty_pct"],
                                  "vpp": m["vpp"]}
    if a.spectrum:
        f_, s_ = analog.spectrum(cap.volts(a.ch), cap.samplerate)
        peaks = analog.top_peaks(f_, s_, n=a.peaks, fmin=500)
        result["spectrum"] = [{"hz": round(fq, 1), "v": round(a_, 4)} for fq, a_ in peaks]
        print("频谱 top 峰值:")
        for fq, a_ in peaks:
            print(f"  {fq:9.1f} Hz  {a_:7.3f} V")
    if a.plot:
        p = plot_scope(cap, os.path.join(a.out, "scope_wave.png"))
        result["plot"] = p
        print("plot:", p)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 1 if FAILS else 0


def measure_la(a):
    la = DSLogicLA(out_dir=a.out)
    channels = [int(c) for c in a.ch.split(",")]
    trig = None
    if a.trigger:
        trig = {}
        for tok in a.trigger.split(","):
            k, v = tok.split(":")
            trig[int(k)] = v
    cap = la.capture(prefix=a.prefix, samplerate=a.rate, samples=a.samples,
                     channels=channels, mode="stream" if a.stream else "buffer",
                     vth=a.vth, threshold=a.th, trigger=trig,
                     timeout=a.timeout, retry=a.retry)
    if cap.meta.get("status") != "ok":
        print(f"ERROR: capture status={cap.meta.get('status')}")
        return 1
    result = {"device": "la", "rate_hz": cap.samplerate, "channels": {}}
    expect_ch = a.expect_ch if a.expect_ch is not None else channels[0]
    for ch in channels:
        sig = cap.ch(ch)
        t = timing_us(sig, cap.samplerate)
        entry = {}
        freq_hz = duty_pct = None
        if t:
            n, hi, per = t
            freq_hz, duty_pct = 1e6 / per, hi / per * 100
            entry = {"edges": n, "high_frac": round(float(sig.mean()), 4),
                     "freq_hz": round(freq_hz, 2),
                     "duty_pct": round(duty_pct, 2),
                     "high_us": round(hi, 3), "period_us": round(per, 3)}
            print(f"ch{ch}: edges={n} freq={freq_hz:.4g}Hz duty={duty_pct:.2f}% "
                  f"high={hi:.2f}us period={per:.3f}us")
        else:
            entry = {"level": "HIGH" if sig.mean() > 0.5 else "LOW", "edges": 0}
            print(f"ch{ch}: 无周期沿（电平 {'HIGH' if sig.mean() > 0.5 else 'LOW'}，"
                  f"高占比 {sig.mean():.3f}）")
        if a.decode == "auto" and entry.get("edges", 0) > 20:
            frames, info = decode_dshot(sig, cap.samplerate)
            if frames:
                crc = sum(1 for x in frames if not x.crc_ok)
                starts = np.array([x.start_sample for x in frames])
                fr = cap.samplerate / np.median(np.diff(starts)) if len(frames) > 2 else 0
                entry["dshot"] = {"frames": len(frames), "crc_bad": crc,
                                  "rate": info.get("detected_rate"),
                                  "bdshot": bool(info.get("bdshot")),
                                  "frame_rate_hz": round(fr, 1),
                                  "throttle": sorted({x.throttle for x in frames})}
                ds = entry["dshot"]
                print(f"      DSHOT{ds['rate']}: {ds['frames']}帧 CRC错{crc} "
                      f"帧率{ds['frame_rate_hz']/1e3:.2f}kHz 油门{ds['throttle']}"
                      f"{' (BDShot)' if ds['bdshot'] else ''}")
        if ch == expect_ch:
            apply_expects(a, freq_hz, duty_pct, None)
            result["measured"] = {"freq_hz": freq_hz, "duty_pct": duty_pct}
        if a.protocol == "list":
            pd_protocol_list()
        elif a.protocol and ch == channels[0]:
            try:
                pd_decode(cap, a, channels)
                result["protocol"] = a.protocol
            except Exception as e:
                print(f"ERROR: PD 解码失败: {type(e).__name__}: {e}")
                result["protocol_error"] = f"{type(e).__name__}: {e}"
        result["channels"][f"ch{ch}"] = entry
    if a.plot:
        p = plot_la(cap, os.path.join(a.out, "la_wave.png"))
        result["plot"] = p
        print("plot:", p)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 1 if FAILS else 0




# ---------------------------------------------------------------- 官方 PD 解码

PD_CHANNEL_ROLES = {
    "uart": ["rxtx"], "pwm": ["rxtx"],
    "i2c": ["sda", "scl"],
    "spi": ["clk", "mosi", "miso", "cs"],
}


def pd_protocol_list():
    from hil.pd import DEFAULT_DECODERS_ROOT
    root = DEFAULT_DECODERS_ROOT
    if not os.path.isdir(root):
        print(f"未找到解码器目录: {root}（需安装 DSView）")
        return
    names = sorted(os.listdir(root))
    print(f"官方解码器 {len(names)} 个（{root}）:")
    for n in names:
        print(" ", n)


def pd_decode(cap, a, channels):
    """官方 PD 解码，打印人类可读结果。"""
    from hil.pd import run_pd, pd_uart_bytes, pd_pwm_periods
    proto = a.protocol
    sigs = {i: cap.ch(c) for i, c in enumerate(channels)}
    roles = PD_CHANNEL_ROLES.get(proto)
    opts = dict(kv.split("=", 1) for kv in a.pd_opt if "=" in kv)

    def print_anns(runner, limit=30):
        items = []
        for oid, lst in runner.anns.items():
            for ss, es, payload in lst:
                try:
                    cls, texts = payload[0], payload[1]
                except (TypeError, IndexError):
                    continue
                items.append((ss, es, texts))
        items.sort(key=lambda t: t[0])
        print(f"  注释条目: {len(items)}")
        for ss, es, texts in items[:limit]:
            txt = " | ".join(t for t in texts if t)
            print(f"    @{ss:>8}: {txt}")
        if len(items) > limit:
            print(f"    ... 其余 {len(items)-limit} 条省略")

    if proto == "uart":
        data = pd_uart_bytes(sigs[0], cap.samplerate, baud=a.baud,
                             parity=opts.get("parity_type", "none"),
                             invert=opts.get("invert", "no"),
                             data_bits=int(opts.get("num_data_bits", 8)))
        print(f"UART @ {a.baud} baud: 解码 {len(data)} 字节")
        if data:
            print("  hex :", data.hex())
            print("  ascii:", "".join(chr(b) if 32 <= b < 127 else "." for b in data))
    elif proto == "pwm":
        periods = pd_pwm_periods(sigs[0], cap.samplerate)
        print(f"PWM 解码: {len(periods)} 个周期")
        for p_ in periods[:10]:
            print(f"  period={p_['period_s']*1e6:.2f}us duty={p_['duty']*100:.1f}%")
        if len(periods) > 10:
            print(f"  ... 共 {len(periods)} 个")
    elif proto == "i2c":
        runner = run_pd("0-i2c", {"sda": sigs[0], "scl": sigs[1]}, cap.samplerate)
        print("I2C 解码:")
        print_anns(runner)
    elif proto == "spi":
        runner = run_pd("0-spi", {"clk": sigs[0], "mosi": sigs[1],
                                  "miso": sigs[2], "cs": sigs[3]}, cap.samplerate)
        print("SPI 解码:")
        print_anns(runner)
    else:
        chans = {role: sigs[i] for i, role in enumerate(roles or sorted(sigs))}             if roles else {str(i): s for i, s in sigs.items()}
        runner = run_pd(proto, chans, cap.samplerate, options=opts or None)
        print(f"PD '{proto}' 解码:")
        print_anns(runner)


def measure_dual(a):
    """LA + 示波器并发采集同一网络，交叉验证频率/占空比。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.join(a.out, f"dual_{ts}")
    common = ["--timeout", str(a.timeout), "--retry", str(a.retry)]
    cmd_la = [sys.executable, os.path.abspath(__file__), "la",
              "--ch", str(a.la_ch), "--rate", a.la_rate, "--samples", a.samples,
              "--stream", "--vth", str(a.vth), "--out", a.out, "--prefix", f"{base}_la"] + common
    cmd_sc = [sys.executable, os.path.abspath(__file__), "scope",
              "--ch", str(a.sc_ch), "--rate", a.sc_rate, "--samples", a.samples,
              "--vdiv", str(a.vdiv), "--coupling", a.coupling,
              "--out", a.out, "--prefix", f"{base}_sc"] + common
    if a.factor:
        cmd_sc += ["--factor", str(a.factor)]
    if a.decode == "none":
        cmd_la += ["--decode", "none"]
    # 错开启动：示波器先完成 USB 枚举/FPGA 配置，再让 LA 大流量上线
    p_sc = subprocess.Popen(cmd_sc)
    time.sleep(2.5)
    p_la = subprocess.Popen(cmd_la)
    rcs = [p_la.wait(), p_sc.wait()]
    print(f"\n并发采集完成 (la rc={rcs[0]}, scope rc={rcs[1]})")
    if any(rc not in (0, 1) for rc in rcs):
        print("ERROR: 并发采集有子进程失败")
        return 1

    from hil.capture import load_capture
    la_cap = load_capture(f"{base}_la")
    sc_cap = load_capture(f"{base}_sc")

    sig = la_cap.ch(a.la_ch)
    t = timing_us(sig, la_cap.samplerate)
    la_f = la_d = None
    if t:
        _, hi, per = t
        la_f, la_d = 1e6 / per, hi / per * 100
        print(f"LA ch{a.la_ch}:   freq={la_f:.4g}Hz duty={la_d:.2f}%")
    v = sc_cap.volts(a.sc_ch)
    m = analog.measure_channel(v, sc_cap.samplerate)
    sc_f = m["freq"]
    sc_d = m["duty"] * 100 if m["duty"] is not None else None
    print(f"Scope CH{a.sc_ch}: freq={sc_f and round(sc_f, 2)}Hz "
          f"duty={sc_d and round(sc_d, 2)}% "
          f"Vpp={m['vpp']:.3f}V Vmean={m['vmean']:.3f}V")

    if la_f and sc_f:
        d = abs(la_f - sc_f) / la_f * 100
        check("dual_freq_cross", d < 1, f"LA {la_f:.2f}Hz vs Scope {sc_f:.2f}Hz (差 {d:.3f}%)")
    if la_d is not None and sc_d is not None:
        d = abs(la_d - sc_d)
        check("dual_duty_cross", d < 1, f"LA {la_d:.2f}% vs Scope {sc_d:.2f}% (差 {d:.2f}%)")
    apply_expects(a, la_f, la_d, m["vpp"])
    if a.plot:
        p1 = plot_la(la_cap, os.path.join(a.out, "dual_la.png"))
        p2 = plot_scope(sc_cap, os.path.join(a.out, "dual_scope.png"))
        print("plot:", p1, "|", p2)
    if a.json:
        print(json.dumps({"la": {"freq_hz": la_f, "duty_pct": la_d},
                          "scope": {"freq_hz": sc_f, "duty_pct": sc_d,
                                    "vpp": m["vpp"], "vmean": m["vmean"]},
                          "prefix": base}, ensure_ascii=False, indent=1))
    return 1 if FAILS else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="dev", required=True)

    ps = sub.add_parser("scope", help="DSCope 示波器（输出真实伏特，极性已补偿）")
    ps.add_argument("--ch", type=int, default=1, help="测量/断言通道（0/1）")
    ps.add_argument("--rate", default="10M")
    ps.add_argument("--samples", default="1M")
    ps.add_argument("--vdiv", default="1.0", help="V/div（10/20/50/100/200/500mV,1/2V）")
    ps.add_argument("--coupling", default="dc", choices=["dc", "ac", "gnd"])
    ps.add_argument("--bw", default=None, choices=["full", "20m"])
    ps.add_argument("--mode", default="osc", choices=["osc", "daq"],
                    help="daq=低速连续采集（驱动钳制 500kS/s）")
    ps.add_argument("--factor", default=None, help="探头衰减，如 10")
    ps.add_argument("--trigger", default=None,
                    help='JSON，如 {"source":"auto","slope":"r","horiz":10}')
    ps.add_argument("--zero-load", dest="zero_load", action="store_true",
                    help="采集前加载 EEPROM 零校准")
    ps.add_argument("--spectrum", action="store_true",
                    help="输出频谱 top 峰值（FFT，等效 DSView 频谱视图）")
    ps.add_argument("--peaks", type=int, default=6, help="频谱峰值个数")
    ps.add_argument("--prefix", default=None, help="输出文件前缀（dual 模式用）")

    pl = sub.add_parser("la", help="DSLogic 逻辑分析仪")
    pl.add_argument("--ch", default="0,4", help="逗号分隔通道号")
    pl.add_argument("--rate", default="20M")
    pl.add_argument("--samples", default="2M")
    pl.add_argument("--stream", action="store_true", help="stream 模式（默认 buffer）")
    pl.add_argument("--vth", type=float, default=1.65, help="阈值电压")
    pl.add_argument("--th", default=None, choices=["3v3", "5v0"])
    pl.add_argument("--trigger", default=None, help='如 "4:r" 或 "0:f,2:r"')
    pl.add_argument("--decode", default="auto", choices=["auto", "none"],
                    help="自动尝试 DSHOT 解码")
    pl.add_argument("--protocol", default=None,
                    help="官方 PD 解码: uart|i2c|spi|pwm|list|<PD名>"
                         "（通道角色按 --ch 顺序: uart/pwm 单通道; "
                         "i2c: sda,scl; spi: clk,mosi,miso,cs）")
    pl.add_argument("--baud", type=int, default=115200, help="UART 波特率")
    pl.add_argument("--pd-opt", action="append", default=[],
                    help="PD 选项 k=v（可重复，如 invert=yes）")
    pl.add_argument("--expect-ch", type=int, default=None,
                    help="断言目标通道（默认第一个）")
    pl.add_argument("--prefix", default=None, help="输出文件前缀（dual 模式用）")

    pd = sub.add_parser("dual", help="LA + 示波器并发采集同一网络并交叉验证")
    pd.add_argument("--la-ch", type=int, default=0)
    pd.add_argument("--sc-ch", type=int, default=0)
    pd.add_argument("--la-rate", default="20M")
    pd.add_argument("--sc-rate", default="10M")
    pd.add_argument("--samples", default="1M")
    pd.add_argument("--vth", type=float, default=1.65)
    pd.add_argument("--vdiv", default="1.0")
    pd.add_argument("--factor", default=None, help="探头衰减（示波器侧），如 10")
    pd.add_argument("--coupling", default="dc", choices=["dc", "ac", "gnd"])
    pd.add_argument("--decode", default="auto", choices=["auto", "none"])

    for p in (ps, pl, pd):
        g = p.add_argument_group("expect/输出")
        g.add_argument("--expect-freq", type=float, default=None, help="期望频率 Hz")
        g.add_argument("--expect-freq-tol", type=float, default=5.0, help="频率容差 %%")
        g.add_argument("--expect-duty", type=float, default=None, help="期望占空比 %%")
        g.add_argument("--expect-duty-tol", type=float, default=2.0, help="占空比容差（绝对 %%）")
        g.add_argument("--expect-vpp", type=float, default=None, help="期望峰峰值 V")
        g.add_argument("--expect-vpp-tol", type=float, default=10.0, help="Vpp 容差 %%")
        g.add_argument("--plot", action="store_true", help="保存波形 PNG 到输出目录")
        g.add_argument("--out", default=None, help="数据输出目录")
        g.add_argument("--timeout", type=int, default=20)
        g.add_argument("--retry", type=int, default=2)
        g.add_argument("--json", action="store_true", help="附加 JSON 输出")

    a = ap.parse_args()

    if dsview_running():
        print("ERROR: DSView.exe 正在运行（独占 USB），请先关闭后重试")
        return 2
    if a.out is None:
        a.out = out_dir()
    rc = {"scope": measure_scope, "la": measure_la, "dual": measure_dual}[a.dev](a)
    if FAILS:
        print(f"\n结果: {len(FAILS)} 项断言失败: {', '.join(FAILS)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
