# HIL 仪器 API 参考

栈位置：默认 `C:\Users\yukai\hil-tools\`（`pyhil/hil/` Python 包 + `bin/ds-capture.exe`），
换机器/换用户时通过环境变量指向实际部署位置。
路径可用环境变量覆盖：`HIL_TOOLS`、`HIL_DSCAPTURE`、`HIL_OUT_DIR`；
类构造时也可传 `exe=`、`out_dir=`。

## 设备发现与运维

```python
from hil.instrument import (DSLogicLA, DSCopeScope, list_devices,
                            find_device, dsview_running, usb_reset)
list_devices()              # [{index, name}]（索引会漂移，类自动按名字找）
dsview_running()            # True = DSView 独占 USB，必须先关闭
usb_reset()                 # 软件重插（libusb bus reset），卡死时自救
```

## DSLogicLA（逻辑分析仪）

```python
la = DSLogicLA(dev=None, out_dir=..., exe=...)
la.info()                   # 设备状态 dict（threshold/vth/filter/op_mode...）
la_cap = la.capture(
    samplerate="20M",       # 20M/50M/100M…（stream 档位：16ch≤20M/6ch≤50M/3ch≤100M）
    samples="2M",
    channels=[0, 4],        # None=全部 16 通道
    mode="buffer",          # "buffer"(FPGA DDR，默认) | "stream"(USB 实时流)
    threshold=3.3,          # 3.3/5.0 前端电平族（重配 FPGA 比特流）
    vth=1.65,               # 细阈值（伏特）
    filt=None,              # "none"|"1t" 1 时钟周期毛刺滤波
    clock=None, clock_edge=None,   # "int"/"ext"；外部时钟采样
    trigger={4: "r"},       # {通道: "r"|"f"|"h"|"l"} 简单触发
    atrig=None,             # 多级模式触发 "0: 0=h,4=r; 1: 2=l logic=or cnt=3"
    trigpos=0,              # 预触发百分比
    timeout=30, retry=1)    # USB 卡死自动重初始化次数
```

返回 `LogicCapture`：`.samplerate`、`.channels`（名字列表）、
`.ch(0)`/`.ch("4")`（bool 数组——接受列位置或物理通道号）。

## DSCopeScope（示波器）

```python
sc = DSCopeScope(dev=None, out_dir=..., exe=...)
sc.info()                   # bw_limit/trigger_source/slope/instant/ref 窗口...
sc_cap = sc.capture(
    samplerate="10M",       # 最高 1G（驱动钳制）
    samples="1M",           # 每通道样本数
    mode="osc",             # "osc" | "daq"（DAQ 钳制 500kS/s，低速长采）
    vdiv=1.0,               # 伏/div：标量、[1.0, 0.5] 或 {0:1.0,1:0.5}
                            # 合法档位：0.01/0.02/0.05/0.1/0.2/0.5/1/2V
    coupling="dc",          # "dc"|"ac"|"gnd"，可 list/dict 每通道
    factor=None,            # 探头衰减 1/10，可 list/dict
    bw="full",              # "full"|"20m"
    offset=None,            # 前端偏移码位（一般不用）
    instant=True,           # False=单次采集路径（仅 2 通道模式可用）
    trigger={               # None=自由运行
        "source": "auto",   # auto|ch0|ch1|ch0a1|ch0o1
        "slope": "r",       # r|f（物理语义：r=物理上升沿，实测确认）
        "level": {1: 1.6},  # 伏特（内部按反相公式换算码位）
        "level_raw": {1: 100},  # 直接给 ADC 码位（offset=128 时 100≈+1.09V）
        "horiz": 10,        # 预触发百分比 0..90
        "holdoff": 800, "margin": 3},
    zero_load=False,        # 采集前加载 EEPROM 零校准
    zero_cal=False,         # 采集中跑自动零校准并存 EEPROM（与 DSView 共享）
    timeout=30, retry=2)    # USB3 偶发卡死时自动重初始化
```

返回 `AnalogCapture`：`.raw`（ADC 码位矩阵）、`.channels`、
`.volts(ch)`——**真实伏特**（极性反相 + ref 窗口 235 码 + offset 锚点已内置，
`invert=False` 可关）、`.volts_per_count(ch)`、`.zero_code(ch)`、
`.baseline_counts(ch)`。

## 测量与解码

```python
from hil import analog, timing
m = analog.measure_channel(v, samplerate)
# m: vpp/vmax/vmin/vmean/vrms, freq, duty, noise_high, noise_low, edge_ns
analog.fft_peak_freq(v, samplerate, fmin=0)      # 频谱主峰
f_, s_ = analog.spectrum(v, samplerate)          # 完整幅值谱（Hann 窗，等效 DSView 频谱视图）
analog.top_peaks(f_, s_, n=6, fmin=500)          # top-n 谱峰 [(Hz, V), ...]
m = analog.math_channel(v0, v1, "SUB")           # 通道四则 ADD/SUB/MUL/DIV（等效 DSView 数学通道）
timing.frequency(sig_bool, samplerate, invert=False)  # 数字通道频率/占空
timing.pulse_widths(sig_bool, level=True, samplerate=...)  # 脉宽分布

from hil.decode import decode_dshot, decode_uart_bytes, decode_spi, decode_i2c
frames, info = decode_dshot(sig, samplerate, rate=None, bdshot=None)
# frame.throttle/.telemetry_req/.crc_ok/.word；info: 极性/BDShot/rate 自适配

from hil.decode.dshot import decode_bdshot_responses, BdshotResponse
resp = decode_bdshot_responses(sig, samplerate, cmd_frames, dshot_rate=600)
# 每指令帧后 30us 的 ESC GCR 回复: 21bit@5/4 速率 → 20 GCR 位 → 4×5bit 码字
# → 16bit = eee(3)+周期基值(9)+CRC(4)；BdshotResponse: .period_us/.erpm/.crc_ok
```

官方 DSView 解码器运行时（PD）：149 种协议无头运行。
```python
from hil.pd import run_pd, pd_uart_bytes, pd_pwm_periods, DEFAULT_DECODERS_ROOT
out = run_pd("0-i2c", {"sda": sig1, "scl": sig2}, samplerate)
out.anns   # {out_id: [(ss, es, [ann_class, [text,...]])]}
pd_uart_bytes(sig, samplerate, baud=115200)   # → bytes
```
measure.py 的 `--protocol` 参数封装了常用协议（uart/i2c/spi/pwm/list/<任意PD名>），
通道角色按 --ch 顺序：uart/pwm 单通道；i2c: sda,scl；spi: clk,mosi,miso,cs；
`--pd-opt k=v` 透传解码器选项。

## 断言与报告

```python
from hil import expect, report
expect.expect_true(cond, "check_name", "detail")            # 通用
expect.expect_pwm(cap, ch, freq_hz=1000, duty=0.5, invert=False)
expect.expect_vpp_range(cap, ch, v_min, v_max, scale)
expect.expect_uart_bytes(cap, ch, baud, b"BOOT OK")
expect.expect_dshot(cap, ch, rate=600, min_frames=100, require_crc_ok=True)
print(expect.summary())                                     # "8/8 checks passed"
report.generate("report.md", "标题", meta_lines=[...], plots=[png路径])
report.plot_channels(cap, ch_list=[0,4], path="la.png", t_max=300e-6)
```

## 硬件特性（为什么这样设计）

- **示波器码位反相**：ADC 码位与电压反相（0V 在 offset≈128 码位，电压越高码位
  越低），`volts()` 已补偿。DSView 屏幕显示补偿后极性，但其保存的 .dsl 原始
  数据未补偿——`load_capture()` 解析 .dsl 时同样自动补偿
- **电压分母是 ref 窗口**（U3P100: 10..245 = 235 码/10 div），不是名义 256；
  与 DSView 测量算法一致（机型常量，不随档位/通道变）
- **零点基线 = DSView 的 hw_offset 算法**（2026-11-16 对照 DSView 源码核对）：
  DSView 按 `V = (hw_offset − code) × vdiv × factor × 10 / 235` 换算，其中
  hw_offset 是 FPGA 每个 DSO 包头报告的真实零点码位（libsigrok4DSL dsl.c
  `get_measure()`），且随档位不同（实测 100mV/div→130，1V/div→129）。exe 的
  JSON 只记录 offset 寄存器值（128），`.raw` 不含包头，instant 模式驱动也
  不解析——因此 pyhil 在 `capture.py HW_OFFSET_CAL` 内置每档标定值，复现
  DSView 算法。未标定档位回退 128（勿用于绝对幅度测量）
- **绝对幅度测量用 100mV/div**：该档标定完整且与电源表头/DSView 一致
  （≤1%）；1V/div 档前端增益本身偏离标称约 6%（零点修正救不了尺度，
  DSView 在该档同样偏差），只可作相对/定性观察
- **改档位有继电器动作**：150ms 建立时间已内置；档位必须落在 vga 表
  （10/20/50/100/200/500mV、1/2V），否则通道读平线（CLI 有告警）
- **前端状态跨进程持久**：vdiv/coupling/factor 会残留，但每次采集都显式重写
  全部前端配置，状态确定
- **零校准**：`--zero-cal`/`zero_cal=True` 现场校准并存 EEPROM（与 DSView
  双向共享）；`--zero-load`/`zero_load=True` 加载；默认不用（锚点固定 128）
- **DAQ 模式**：`mode="daq"`，驱动钳制 500kS/s，适合慢信号长时监控
- **USB3 卡死**：自动重试（重开设备栈）；顽固卡死 `usb_reset()` 或重插
- **GND 耦合已修复**（2026-11-16）：上游驱动在 config_set 里把 GND 强制改写
  为 DC（dso_cmd_gen 的 GND 继电器分支成死码）；已打补丁移除改写并重编译，
  真机实证 gnd 将 2V 输入拉到地（meta 正确记录 "gnd"）。用途：测前端噪声底
  /零基线。小档位（≤50mV/div）未校准时 gnd 读数含百 mV 级零点偏移，差值
  测量不受影响
- **DSO 采样率实测安全档**：10/20/50/100/250/500M、**1G（最高，双通道）**；
  ≤5M 和 200M 零数据超时（200M 分频 2.5→3 非整，避开）。慢信号用
  `mode="daq"`（500k~10M 实测可采；**DAQ 固定 1V/div×1，--vdiv/--factor
  不生效**，配 10x 探头时 volts() 读数为输入端电压，需自行 ×10）

## 已知边界

- 非 instant 单次采集路径仅 2 通道模式可用（驱动限制）
- RLE 压缩、LA 循环模式、串行触发、时间制采集（LIMIT_MSEC）未实现
  （详见 hil/AUDIT.md 功能对齐审计）
- 外部时钟采样（`clock="ext"`）链路已通，未真机验证
