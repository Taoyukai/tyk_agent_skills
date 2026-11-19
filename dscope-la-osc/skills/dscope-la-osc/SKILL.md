---
name: dscope-la-osc
description: 驱动 DSLogic Plus 逻辑分析仪和 DSCope U3P100 示波器做硬件信号采集与测量（Vpp/峰峰值、频率、占空比、幅值、噪声、沿速、DSHOT/PWM/UART 解码、自动断言报告）。只要用户提到示波器、逻辑分析仪、LA、DScope、DSLogic、信号采集、抓波形、测量信号、峰峰值/Vpp/占空比/频率/脉宽、DSHOT/UART 解码、HIL 硬件在环测试、或想从真实硬件抓取/验证电信号——即使没有明说"用 hil 工具"——就使用本 skill。
---

# HIL 仪器采集测量（DSLogic Plus + DSCope U3P100）

采集栈**随插件自带**（`<SKILL_DIR>\assets\hil-tools\`：pyhil 包 +
bin/ds-capture.exe + 运行 DLL，自包含），装好插件即可用，无需单独部署：

1. 设置了环境变量 `HIL_TOOLS`（自管部署，指向含 `pyhil\`+`bin\` 的目录）时优先用它
2. 未设置则用插件内置副本；采集数据默认落
   `%LOCALAPPDATA%\hil-tools\captures`（可用 `HIL_OUT_DIR` 覆盖）——
   **不要**写进插件缓存目录（插件更新时会被清掉）

前置依赖只有 **Python 包 numpy**（matplotlib 仅报告绘图需要）：

- 采集栈（pyhil + ds-capture.exe + DLL）和解码器（DSView 官方 149 个协议
  解码脚本）都随插件内置在 `<SKILL_DIR>\assets\hil-tools\`，**无需安装 DSView**
  即可采集与协议解码（DSView 反而会独占 USB，采集时必须处于关闭状态；
  只有用户自己想开 GUI 看波形才需要装它）
- 解码器目录解析：`DSVIEW_DIR` 环境变量（显式指向某 DSView 安装目录）→
  插件内置副本 → 已安装的 DSView（任意盘符自动扫描）
下文用 `<SKILL_DIR>` 指代本 skill 的安装基目录（skill 加载时会给出
"Base directory"，scripts、references 都相对它解析）。设备通过名字自动发现，
无需关心 USB 索引。

## WSL 侧调用（跨边界执行）

从 WSL 里的 agent 触发本 skill 时，**必须用 Windows 的 Python 执行**——采集栈
（pyhil、bin/ds-capture.exe、USB 驱动）都在 Windows 侧，WSL2 看不到这些 USB
设备，WSL 本地 python 跑不了。下文所有 Windows 命令在 WSL 中包一层 interop，
路径保持 Windows 形式：

```bash
# 前置检查同样走 interop
cmd.exe /c 'tasklist /FI "IMAGENAME eq DSView.exe"'

# measure.py：cmd.exe / powershell.exe 二选一，参数用绝对路径
cmd.exe /c 'python "<SKILL_DIR>\scripts\measure.py" scope --ch 1 --json'
powershell.exe -NoProfile -Command "python '<SKILL_DIR>\scripts\measure.py' la --ch 4 --json"
```

要点：

1. **cmd.exe 的 UNC 警告无害**：WSL 工程目录（/home/...）对 cmd.exe 是 UNC
   路径，会警告并回退 Windows 目录，参数全是绝对路径时不受影响；
   powershell.exe 无此问题
2. **Python API 场景**：临时脚本写到 Windows 侧数据目录再执行，避免让
   Windows python 从 `\\wsl.localhost` 路径加载脚本。WSL 与 Windows 路径互转
   用 `wslpath`：先取 Windows 侧数据目录（`WIN=$(cmd.exe /c 'echo %LOCALAPPDATA%\hil-tools' | tr -d '\r')`），
   `wslpath -u "$WIN"` 得 WSL 侧路径用于写入 `run_tmp.py`，再
   `cmd.exe /c "python <WIN路径>\run_tmp.py"` 执行
3. **结果读取**：captures 固定落在数据目录（默认
   `%LOCALAPPDATA%\hil-tools\captures`，WSL 侧用 `wslpath -u` 换算；
   设置了 `HIL_TOOLS` 则为 `%HIL_TOOLS%\captures`）；要把 WSL 工程文件传给
   Windows 程序，用 `wslpath -w <路径>` 转成 UNC 形式传参（Windows 程序可写
   UNC 路径）


## 第 0 步：前置检查

1. 确认 DSView.exe 未运行（独占 USB）：`tasklist /FI "IMAGENAME eq DSView.exe"`；
   若在运行，请用户关闭，**不要**自行 taskkill（用户可能正在 DSView 里工作）
2. Python 依赖：numpy（matplotlib 仅报告绘图需要）

## 日常测量：直接跑一键脚本（首选）

大多数"测一下信号"的需求，跑 `scripts/measure.py` 即可，不要自己现写采集代码：

```bash
# 示波器：真实伏特输出（极性已补偿，直接可用）
python "<SKILL_DIR>\scripts\measure.py" scope --ch 1 --vdiv 1

# 逻辑分析仪：时序 + DSHOT 自动解码
python "<SKILL_DIR>\scripts\measure.py" la --ch 0,4 --rate 20M --stream --vth 1.65

# 带触发 / 机器可读输出
python "...measure.py" scope --ch 1 --trigger '{"source":"auto","slope":"r","horiz":10}' --json
python "...measure.py" la --ch 4 --trigger "4:r" --json

# 双仪器并发对比同一网络（LA+示波器交叉验证，差值 <1% 判 PASS）
python "...measure.py" dual --la-ch 0 --sc-ch 0 --vdiv 1

# 期望断言（PASS/FAIL 判定，退出码反映结果，可用于 CI）
python "...measure.py" scope --ch 0 --expect-freq 1000 --expect-duty 50 --expect-vpp 3.0
python "...measure.py" la --ch 4 --expect-freq 8000 --plot   # --plot 附波形 PNG

# 官方协议解码（149 种 DSView 解码器，无头运行）
python "...measure.py" la --ch 4 --protocol uart --baud 115200
python "...measure.py" la --ch 4,5 --protocol i2c            # 通道角色: sda,scl
python "...measure.py" la --ch 4,5,6,7 --protocol spi       # clk,mosi,miso,cs
python "...measure.py" la --ch 0 --protocol pwm
python "...measure.py" la --ch 0 --protocol list            # 列出全部解码器
python "...measure.py" la --ch 0 --protocol can --pd-opt "bitrate_config=..."  # 任意 PD
```

输出自动包含：Vpp/Vmax/Vmin/Vmean、频率、占空比、沿速、噪声（示波器）；
频率/占空比/脉宽 + DSHOT 帧/CRC/油门/BDShot ESC 回复 eRPM 解码（LA）。
`--json` 附加结构化输出。DSHOT 双向：指令帧自动解码 + 每帧后的 ESC GCR
回复（20 位 → 16 位 = eee+9bit 周期 + CRC，输出 period_us 与 eRPM）。
`dual` 子命令自动并发双仪器（错开 2.5s 启动避开 USB 初始化竞争）并做频率/
占空比交叉验证；`--expect-*` 断言失败时退出码为 1。

参数要点：示波器 `--vdiv` 只接受 10/20/50/100/200/500mV、1/2V（前端 vga 表）；
`--mode daq` 为低速连续采集（驱动钳制 500kS/s）；LA `--stream` 实时流
（16ch ≤20MHz），默认 buffer 模式。

## 需要更复杂逻辑时：Python API

```python
import os, sys
TOOLS = os.environ.get("HIL_TOOLS") or r"<SKILL_DIR>\assets\hil-tools"  # 插件内置副本
sys.path.insert(0, os.path.join(TOOLS, "pyhil"))

from hil.instrument import DSLogicLA, DSCopeScope, dsview_running, usb_reset
from hil import analog, expect, report
from hil.decode import decode_dshot

assert not dsview_running()

la = DSLogicLA(out_dir=os.path.join(TOOLS, "captures"))
la_cap = la.capture(samplerate="20M", samples="2M", channels=[0, 4],
                    mode="stream", vth=1.65, trigger={4: "r"})

sc = DSCopeScope(out_dir=os.path.join(TOOLS, "captures"))
sc_cap = sc.capture(samplerate="10M", samples="1M", vdiv=1.0,
                    coupling="dc", bw="full",
                    trigger={"source": "auto", "slope": "r", "horiz": 10})

v = sc_cap.volts(1)                      # 真实伏特（极性/刻度已内置补偿）
m = analog.measure_channel(v, sc_cap.samplerate)
frames, info = decode_dshot(la_cap.ch(4), la_cap.samplerate)

expect.expect_true(m["duty"] > 0.4, "duty_ok", f"{m['duty']*100:.1f}%")
report.generate(os.path.join(TOOLS, "captures", "report.md"), "测试标题")
```

完整 API（全部参数、协议解码、断言库、DAQ 模式、高级触发、串口/时钟选项）
读 `references/api.md`。硬件特性与坑（极性、继电器、ref 窗口、校准）也在其中。

## 硬件注意事项（违反会得到错误结果）

1. **DSView 运行时无法采集**（独占 USB）——先确认已关闭
2. **电压已是真实值**：`sc_cap.volts(ch)` 内置了极性反相与 ref 窗口（235 码）
   补偿，与 DSView 屏幕显示一致；不要再做符号/刻度调整
3. **触发伏特直给**：`trigger={"level": 1.6}` 即物理 1.6V（内部已按反相公式换
   算码位）；拿不准时用 `"level_raw"` 直接给 ADC 码位
4. **USB 卡死自愈**：采集失败会自动重试重初始化；仍失败用
   `usb_reset()`（软件重插）或提示用户重插 USB
5. 改档位有继电器动作（150ms 建立时间已内置）；**不要在循环里高频改档位**
   （继电器磨损）
6. **小信号 AC 纹波测量**（≤50mV/div）：AC 耦合电容充电需数百 ms——先跑
   一次同设置采集"预充电"，紧接着的第二次采集才是稳态；或只分析长采集的
   尾段。10/20mV/div 档未校准时存在百 mV 级恒定零点偏移（差值测量不受
   影响，绝对电压用 --zero-load 或大档位）
7. **10x 探头**：`--factor 10` 必传（已接入 scope/dual）；volts() 输出即
   真值。10x 下小纹波分辨率降 10 倍，测 <10mV 纹波建议 1x 探头
8. **LA buffer 模式实测至 400M**（100M/200M/400M 数据经 DSHOT 解码验证真实）；
   stream 模式 16ch≤20M；**DSO 采样率用实测安全档**：10/20/50/100/250/500M、1G 均实测通过（最高
   **1 GHz**，双通道）；**≤5M 零数据超时、200M 零数据**（避开）；慢信号用
   `--mode daq`（实测 500k~10M 均可）。DAQ 模式固定 1V/div×1 标尺（--vdiv/
   --factor 不生效），volts() 按元数据换算仍正确
9. **`--coupling gnd` 可用**（已修复驱动强制改写）：输入继电器接地，用于测
   前端噪声底/零基线；小档位未校准时读数含零点偏移，看差值

## 数据位置

采集数据（.raw/.json 对）落在数据目录：默认 `%LOCALAPPDATA%\hil-tools\captures`；
设置了 `HIL_TOOLS` 时为其下 `captures`；`HIL_OUT_DIR` 可整体覆盖。
`.json` 记录本次实际生效的全部配置；`load_capture(prefix)` 重新加载分析。
DSView 保存的 .dsl 会话也可解析：`load_capture(path去掉.dsl)`。
