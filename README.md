# 电量托盘 · Windows 无线外设电量

一个 **零第三方依赖** 的 Windows 10/11 无线外设电量小工具。只用 Python 标准库 +
`ctypes` 直接调用 `setupapi.dll` / `hid.dll` / `kernel32.dll` / `user32.dll`，
GUI 使用 `tkinter`。没有 hidapi、pystray、Pillow、pywin32。

本项目针对本机的两台设备：

| 设备 | VID:PID | 读取接口 | 协议 |
|---|---|---|---|
| 无线鼠标（K-snake / EWEADN 方案） | `A8A5:2255` | Usage `0xFF01:0x0010`，65 字节 Output/Input | `00 55 30 A5 0B 2E 01 01 ...`，回复 `AA 30 A5 ...`，电量在回复 `[8]` |
| AULA F87 Pro V2 键盘 | `0C45:FEFE` | MI_03，Usage `0xFF60:0x0061`，33 字节 | AULA/SONiX 状态帧 `55 10 18 ...`，电量在载荷第 25 字节（含 Report ID 时原始第 26 字节）；同时保留 `20 01 00 <level>` 兼容路径 |

> 参考 [atk-langzhuF65-2.4G-battery](https://github.com/yiwangc018-ctrl/atk-langzhuF65-2.4G-battery)：
> 狼蛛 F65 / AULA `3554:FA09` 使用 20 字节 `0x13/0x4A` 帧，本项目也实现了这条
> 通用回退路径；但本机键盘是 `0C45:FEFE`（F87ProV2D），实际可用的是
> `55 10 18` 周期状态帧。两者不混用。
>
> 实机验证说明：两台设备都已读通。鼠标来自 AULA Hub 同款 `AA 30` 厂商帧；
> 键盘来自 AULA F87ProV2D 周期状态帧 `55 10 18`，读取值为 52%，与 Aula Hub
> 自身日志里的 `[battery] reading`（52%）逐值一致。程序不会伪造读数。

## 快速开始

```powershell
python my_devices.py --once       # 打印两台设备后退出
python my_devices.py --panel      # 简洁面板（默认）
python my_devices.py --tray       # 托盘常驻
python my_devices.py --list       # 列出全部 HID 接口
python my_devices.py --debug-ui   # 面板 + 完整调试版
```

建议用 `pythonw.exe` 启动托盘，避免黑色控制台窗口：

```powershell
pythonw my_devices.py --tray
```

### 命令行参数

| 参数 | 作用 |
|---|---|
| `--once` | 读取一次并输出两行结果 |
| `--panel` | 打开简洁面板（默认） |
| `--tray` | 托盘常驻，两个数字图标 |
| `--list` | 列出所有 HID 设备/接口、Usage、报告长度 |
| `--debug-ui` | 面板 + 完整调试版 |
| `--install` | 创建启动文件夹快捷方式 + 桌面目录链接和文件链接 |
| `--uninstall` | 删除本工具的启动项和桌面链接（不删除项目） |
| `--quit` | 让正在运行的托盘实例退出 |
| `--debug` | 只在控制台输出调试信息，不写任何日志文件 |
| `--version` | 版本号 |

## 三种形态

### 简洁面板

- 固定两张卡片：鼠标、键盘。
- 大号百分比、进度条、数据来源、最后更新时间。
- 颜色：`>=60` 绿色、`25–59` 黄色、`<25` 红色；无数据灰色。
- 设备卡片固定尺寸，从左到右自动换行；高度不够时可用鼠标滚轮或右侧滚动条上下滚动。
- 底部：开机自启勾选框、显示托盘图标/显示任务栏图标勾选框、退出。
- 刷新在后台线程执行，Tk 主线程不阻塞。

### 托盘常驻

- 通知区域两个图标，图标内容直接是电量数字（GDI 绘制，无图片文件）。设备卡片右下角显示绿色三角表示该设备图标正在托盘显示；点击整张卡片即可切换，选择写入 `settings.json`。
- Windows 10/11 任务栏时钟左侧增加横向设备格：每格为线稿设备图标（鼠标/键盘/耳机，按设备类型自动选择）+ 电量百分比，格与格之间一条 1px 细竖线；背景透明。图标在深色任务栏为白色、浅色任务栏自动换深灰；电量颜色 `>=60` 绿、`25–59` 黄、`<25` 红。点击打开面板，右键打开与托盘相同的菜单，每秒跟随任务栏位置/DPI/浅色主题重排；检测到 TrafficMonitor 时自动让到它左边。
- **左键**：打开/前置简洁面板，并立即刷新一次。
- **右键**：弹出菜单：打开主面板 / 立即刷新 /「托盘图标」/「任务栏图标」/ 退出。两个图标项带勾选状态，取消勾选立即隐藏对应图标并写入 `settings.json`；重新运行一次程序会恢复托盘图标。
- **悬停**：`设备名：电量（更新时间）`。
- 键盘厂商接口由后台线程常驻监听，收到 `0x01` / `55 10 18` 状态包立即更新；打开面板或刷新时按网页相同方式发送一次 `AA 10` 只读查询。
- 关闭面板只是隐藏到托盘；真正退出用右键菜单「退出」、面板里的「退出托盘」或 `--quit`。
- 已取消定时自动刷新；打开面板、托盘左键、右键「立即刷新」或面板「立即刷新」时才读取。
- 单实例互斥：重复启动只会让已运行实例打开面板。

### 完整调试版

- 列出所有 HID 设备/接口：VID:PID、Usage、In/Out/Feature 长度、产品名、接口路径。
- 转储 Feature Report（可按 Report ID 扫描）。
- 只读探测 Report ID（Feature + 监听 Input Report）。
- 监听 Input Report 3 秒并显示原始字节。
- 试发 Output / Feature 命令并收集回复。
- 手动标定：Report ID、电量字节下标、倍率、偏移、充电字节下标、来源模式，
  保存到 `settings.json` 的 `calibrations` 字段。

## 开机自启

运行一次：

```powershell
python my_devices.py --install
```

会创建：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\电量托盘.lnk
```

快捷方式目标为 `pythonw.exe`，参数为 `"…\my_devices.py" --tray`，工作目录为项目目录，
因此不会弹黑窗，也能在 **任务管理器 →「启动」** 中看到「电量托盘」。

面板里的「开机自启」勾选框只读取真实状态：

- `电量托盘.lnk` 存在且 `StartupApproved` 未禁用 → 勾选；
- 用户在任务管理器禁用 → 勾选框不勾选；
- **托盘/面板启动时不会因为状态不一致而自动删除或重建启动项**；
- 只有用户点击勾选框时才写入 `StartupApproved`（2/3 状态）或创建快捷方式。
- 面板模式也做了单实例：重复启动只会把已有面板前置。

## 桌面入口

`--install` 同时创建：

| 桌面条目 | 类型 | 说明 |
|---|---|---|
| `电量托盘项目` | 目录链接 | 优先真符号链接；无权限时降级 `mklink /J` junction |
| `电量托盘.pyw` | 文件链接 | 优先符号链接；无权限时 `os.link` 硬链接到 `电量托盘启动器.pyw` |

不会复制项目文件。`电量托盘启动器.pyw` 是安装时生成的一个约 400 字节的引导脚本，
硬链接入口通过它定位项目目录（硬链接本身无法从桌面路径反推项目位置）。

当前机器的普通用户没有创建符号链接的权限，因此实测降级为：

```
电量托盘项目  Junction
电量托盘.pyw  HardLink
```

## 配置与手动标定

设置文件：`settings.json`（不是日志文件）。
读数缓存：`last_state.json`（只保留每台设备最后一次成功读数和时间，不是日志）。

```json
{
  "refresh_seconds": 60,
  "targets": [
    {
      "id": "mouse",
      "kind": "mouse",
      "name": "无线鼠标",
      "vid": "A8A5",
      "pid": "2255",
      "profile": "kysona"
    },
    {
      "id": "keyboard",
      "kind": "keyboard",
      "name": "AULA F87 Pro V2 键盘",
      "vid": "0C45",
      "pid": "FEFE",
      "profile": "sonix"
    }
  ],
  "calibrations": {}
}
```

手动标定支持两种来源：

- `mode: "input"`：监听 Input Report，`index` 是原始报文字节下标（含 Report ID，
  如果设备会返回 Report ID），`scale` / `offset` 换算成百分比；
- `mode: "feature"`：读取 Feature Report，同样按字节下标提取。

`calibrations` 以 `"VID:PID"` 为键，示例：

```json
"calibrations": {
  "A8A5:2255": {
    "name": "鼠标自定义",
    "report_id": "00",
    "index": 9,
    "scale": 1,
    "offset": 0,
    "charging_index": 10,
    "mode": "input"
  }
}
```

调试版「保存标定」会自动写入这个结构，并立即触发一次刷新。

## 兜底优先级

每个目标按以下顺序尝试，第一条得到合法百分比的结果生效：

1. 标准 HID 电量字段：`0x0006/0x0020` Battery Strength，以及 `0x000D/0x003B`、
   `0x0085` 电量页；
2. 厂商协议表：Endgame Gear `0x3367`、VAXEE/ZYGEN `0x3057`、Nordic `0x1915`、
   AULA/MCHOSE 20 字节 `0x13/0x4A`、SONiX `0x20/0x01`、AULA F87ProV2D 状态帧
   `55 10 18`、K-snake/EWEADN `AA 30`；
3. 罗技 HID++ 2.0：`0x1004` 统一电池、`0x1000` 电池状态、`0x1001` 电压；
4. 监听 Input Report 中的标准电量字段；
5. 手动标定 JSON。

标准 HID 电量字段的 Feature Report 和 Input Report 都支持。

## 已知的坑与实现方式

| 坑 | 本项目处理 |
|---|---|
| 同一 HID 句柄阻塞读 + 并发写会崩进程 | 读句柄 `GENERIC_READ + FILE_FLAG_OVERLAPPED`；写句柄独立；超时用 `CancelIoEx` |
| 鼠标/键盘/笔/触摸屏顶层集合被系统独占 | 枚举和 Feature Report 使用 `dwDesiredAccess=0`；写操作只对共享的厂商接口 |
| `DestroyWindow` 后进程变僵尸 | `WM_DESTROY` 中删除图标、`PostQuitMessage(0)`；单实例互斥锁随进程释放 |
| Tkinter 跨线程 | 后台线程只写 `queue.Queue`；Tk 主线程 `after(100, pump)` 读取 |
| 托盘图标删了再加会丢 | 刷新只用 `NIM_MODIFY`；只有 Explorer 重启（`TaskbarCreated`）才重新 `NIM_ADD` |
| 高 DPI 图标发糊 | 启动时 `SetProcessDPIAware` / Per-Monitor V2，按 DPI 生成 GDI 图标 |
| `.lnk` 的 COM vtable | 纯 ctypes 调 `IShellLinkW` / `IPersistFile`，使用题目给出的 vtable 下标 |
| GUID 解析 | `split("-", 3)`，正确处理最后一节里的连字符 |
| 常驻程序自动纠正启动项 | 只在用户点击时写；启动时只读 |

## 新增设备协议

厂商协议统一继承 `wbtool.providers.DeviceProvider`，只实现两个方法：

```python
from wbtool.hid import HidDevice
from wbtool.providers import (
    BatteryReading, DeviceProvider, register_provider,
)

class MyMouseProvider(DeviceProvider):
    name = "my-mouse"

    def matches(self, iface, target):
        return iface.vid == 0x1234 and iface.pid == 0x5678 and iface.output_len >= 8

    def read(self, iface):
        def parse(raw):
            payload = self.strip_report_id(raw)
            if len(payload) < 10 or payload[:2] != b"\xAA\x30":
                return None
            level = payload[8]
            if not 1 <= level <= 100:
                return None
            return BatteryReading(
                level=level,
                charging=bool(payload[9]),
                source="My vendor protocol",
                raw=raw,
            )

        with HidDevice(iface) as dev:
            return self.write_and_match(
                dev, bytes((0x00, 0xAA, 0x30)), parse, timeout_ms=800
            )

register_provider(MyMouseProvider())
```

基类已封装通用逻辑：

- `read_until()`：带超时读取输入报告
- `write_and_match()`：下发 Output Report 并等待匹配回复
- `feature_exchange()`：Feature Report 写入/读取
- `strip_report_id()`：兼容带/不带 Report ID 的回报

内置协议分别是 `SonixAulaProvider`、`Aula13Provider`、`KysonaProvider`、
`Aula20Provider`、`EndgameProvider`、`VaxeeProvider`、`NordicProvider`、
`LogitechHidppProvider`。注册顺序即匹配优先级。

## 文件结构

```
my_devices.py              入口（--once / --panel / --tray / --install …）
wbtool/
  win32.py                 Windows 常量、GUID、DPI、Known Folder
  hid.py                   HID 枚举、安全句柄、Feature/Input/Output
  providers.py             标准字段、厂商协议、HID++、手动标定
  service.py               两目标刷新与视图模型
  keyboard_watch.py        键盘状态包常驻监听 + AA 10 只读查询
  taskbar_strip.py         Windows 10/11 任务栏电量小条
  panel.py                 简洁面板
  tray.py                  通知区域、单实例、消息循环
  icons.py                 GDI 数字图标
  debugui.py               完整调试版
  shortcuts.py             .lnk COM、StartupApproved、符号链接/硬链接
  installer.py             安装/卸载入口
  config.py                settings.json
  state.py                 last_state.json（最后一次读数缓存）
settings.json              配置（运行时可写，不是日志）
电量托盘启动器.pyw          --install 生成的桌面文件链接目标
```

## 验证结果

在本机 Windows 10 19044 上：

```text
python my_devices.py --once
YJX-CHIP：59%，放电中  [AULA 2.4G 厂商协议（AA 30）]
AULA F87 Pro V2 键盘：70%（上次）  [AULA 状态帧（55 10 18）]
```

- 鼠标读数与协议回复逐字节核对通过；请求头同时兼容 AULA Hub 官方版和 K-snake 社区版。
- 鼠标标题采用 HID `Manufacturer`，本机显示为 `YJX-CHIP`。
- 键盘状态帧的 `payload[25]` 与 Aula Hub 自身日志 `[battery] reading` 的 52% 一致；
  状态帧约每 5 秒一次，工具在 5.6 秒窗口内捕获。键盘进入深度休眠后不再广播，
  此时保留上次读数及时间，不会把电量改成未读取或 0%。
- 启动项已验证出现在 `Win32_StartupCommand`（Name=`电量托盘`，Location=`Startup`）。
- 桌面目录为 Junction，桌面文件为 HardLink，均不是副本。
- 托盘窗口、两个图标、右键菜单（打开/刷新/退出）、左键打开面板并刷新、`--quit` 退出、托盘/面板单实例均已接入并验证。
- `--install` 可重复执行；重复执行不会产生副本或重复启动项。
- 键盘状态帧需要约 5 秒的监听窗口；即使关闭 Aula Hub，`--once` 仍能独立读到 51%，不依赖厂商软件。
- 托盘空闲 CPU 实测：75 秒内 `0.172` 秒 CPU，低于 0.5 秒验收线。
- 面板卡片的更新时间在右上角，数据来源/协议行在卡片底部。
- 项目内没有日志文件；`--debug` 才输出控制台信息。

## 说明

- 本工具不修改设备固件，也不写入鼠标/键盘的配置；厂商协议只发只读查询命令。
- 设备休眠或接收器在而键盘/鼠标关机时，界面保留上次读数并标记「上次」，不会显示假数据。
- 最后一次成功读数写入 `last_state.json`（仅保留最新值，不是日志）；即使键盘深度休眠或重启应用，
  面板/托盘仍显示最后一次电量，失败刷新不会覆盖它。
- 如果换设备，改 `settings.json` 的 VID/PID，或先用 `--debug-ui` 的「手动标定」摸协议。
- 托盘图标可能被 Windows 自动收进「隐藏的图标」溢出区，这是系统行为；可在任务栏设置里固定。
