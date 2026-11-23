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

- 卡片按设备动态生成：先显示 `settings.json` 里配置的两台（鼠标、键盘），扫描到的其它外设
  会追加成新卡片；每台设备都有独立的连接方式色块、来源、任务栏/托盘开关。
- 大号百分比、进度条、最后更新时间（右上角；不显示「（上次）」）。
- 卡片下半部分左右各占一半：左边两行，第一行小字「数据来源」，第二行是具体来源
  （自动去掉括号里的报文细节和 `2.4G` 字样——连接方式已由上面的色块表示——
  过长时按半宽换行）；右边是垂直排列的「任务栏 / 托盘」开关。
- 「鼠标 / 键盘」右侧的小色块显示当前连接方式：绿色 `2.4G`、蓝色 `蓝牙`、灰色 `有线`
  （判断依据见「连接方式识别」）。色块用超采样位图绘制，边缘没有台阶锯齿。
- 颜色：`>=60` 绿色、`25–59` 黄色、`<25` 红色；无数据灰色。
- 设备卡片固定尺寸，从左到右自动换行；高度不够时可用鼠标滚轮或右侧滚动条上下滚动。
- 每张卡片右下角两个开关（垂直排列）：**任务栏** 控制该设备是否出现在任务栏电量格，
  **托盘** 控制该设备是否出现在通知区域。开关状态按设备分别保存到 `settings.json`
  的 `taskbar_devices` / `tray_devices`，只影响这一台设备，不改变另一台。
  **新发现的设备默认两个开关都关闭**：只加一张卡片，不出现在任务栏电量格和通知区域，
  需要时再打开它自己的开关。
- 底部：`开机自启` 勾选框、紧随其后的 `隐藏到托盘` 勾选框、刷新间隔下拉框；第二行是
  `显示托盘图标` / `显示任务栏图标` 两个整体开关和 `扫描设备` 按钮。
- `隐藏到托盘` 只决定窗体叉号（和 Esc）的行为：勾选时关闭窗口 = 隐藏到托盘，不勾选时
  关闭窗口 = 退出程序；勾选状态写入 `settings.json` 的 `hide_to_tray`，点勾选框本身不会
  立即隐藏或退出。
- `扫描设备` 只找**新**设备：枚举所有 HID 接口，对暴露标准电量字段、或被内置厂商协议认领的
  设备（`0C45:FEFE` 状态帧、`A8A5:2255` 等）试读一次，读到百分比就加一张卡片（蓝牙和 2.4G
  都一样）；已经识别过的设备不重复读，交给 `立即刷新` 或定时刷新。结果记在
  `settings.json` 的 `discovered`，下次启动直接恢复卡片；新设备的任务栏/托盘开关默认关闭。
  删除过的设备会按 `VID:PID` 记进 `settings.json` 的 `removed`，重启和自动刷新都不会
  再把它们加回来；只有手动点「扫描设备」才会重新识别并加回卡片。
- 每张卡片右上角有一个三角形删除按钮（里面是垃圾桶图标），点击会弹窗确认；确认后该设备
  从卡片、托盘图标和任务栏电量格移除，同时从 `targets`/`discovered` 里删掉。
- 刷新在后台线程执行，Tk 主线程不阻塞。

### 托盘常驻

- 每台设备一个通知区域图标（数量随卡片增减），图标内容直接是电量数字（GDI 绘制，无图片文件）。某个设备是否显示图标，由它卡片上的「托盘」开关决定（见上）；关掉后该图标立即隐藏，设置写入 `settings.json`。
- Windows 10/11 任务栏时钟左侧增加横向设备格：每格为线稿设备图标（鼠标/键盘/耳机，按设备类型自动选择）+ 电量百分比，格与格之间一条 1px 细竖线；背景透明。图标在深色任务栏为白色、浅色任务栏自动换深灰；电量颜色 `>=60` 绿、`25–59` 黄、`<25` 红。哪些设备出现由卡片上的「任务栏」开关决定，两个都关时整条隐藏。电量格只负责显示，**左键不响应**；右键（点在图标或数字上）打开与托盘相同的菜单，每秒跟随任务栏位置/DPI/浅色主题重排；检测到 TrafficMonitor 时让到它可见数字的左边（用 `TrafficMonitorTaskbarWindow` 的实际绘制矩形，而不是它那个又宽又隐藏的框架窗口，因此不会留出一大块空白）。
- **左键**：打开/前置简洁面板，并立即刷新一次。
- **右键**：弹出菜单：打开主面板 / 立即刷新 /「托盘图标」/「任务栏图标」/ 退出。两个图标项带勾选状态，取消勾选立即隐藏对应图标并写入 `settings.json`；重新运行一次程序会恢复托盘图标。
- **悬停**：`设备名：电量（更新时间）`。
- 键盘厂商接口由后台线程常驻监听，收到 `0x01` / `55 10 18` 状态包立即更新；打开面板或刷新时按网页相同方式发送一次 `AA 10` 只读查询。
- 关闭面板的行为由 `隐藏到托盘` 勾选框决定（见上）；真正退出还可以用右键菜单「退出」或 `--quit`。
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

## 开机自启与移动项目

项目放在哪个盘、哪个目录都可以：启动项和桌面快捷方式都**不保存项目路径**。

运行一次：

```powershell
python my_devices.py --install
```

会创建：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\电量托盘.lnk
```

这个快捷方式指向一个固定位置的小引导程序：

```
%LOCALAPPDATA%\电量托盘\电量托盘启动器.pyw
%LOCALAPPDATA%\电量托盘\location.json      ← 只记“项目在哪”的一条指针
```

引导程序里没有项目路径，启动顺序是：

1. 读 `location.json`，指向的项目还在就直接启动（正常情况，几乎不耗时）；
2. 指针失效（项目搬到别的盘、改了文件夹名）就扫描本机所有本地固定磁盘，
   按 `my_devices.py` + `wbtool\app.py` 两个文件识别项目，优先选名为“电量托盘项目”的目录；
3. 找到后更新 `location.json` 再启动。

因此**移动项目后不需要重装**，下次开机由引导程序自己找到新位置；托盘每次启动也会顺手
刷新 `location.json` 和桌面的项目文件夹链接。

快捷方式目标为 `pythonw.exe`，不会弹黑窗，也能在 **任务管理器 →「启动」** 中看到「电量托盘」。

面板里的「开机自启」勾选框只读取真实状态：

- `电量托盘.lnk` 存在且 `StartupApproved` 未禁用 → 勾选；
- 用户在任务管理器禁用 → 勾选框不勾选；
- 启动时不会因为状态不一致而自动删除或重新启用启动项；
- 旧版本留下的“直接指向项目目录”的启动项会在启动时自动迁移成固定位置引导程序（不改变禁用状态）；
- 只有用户点击勾选框时才写入 `StartupApproved`（2/3 状态）。
- 面板模式也做了单实例：重复启动只会把已有面板前置。

## 桌面入口

`--install` 同时创建：

| 桌面条目 | 类型 | 说明 |
|---|---|---|
| `电量托盘项目` | 目录联接 | 指向项目目录，方便打开文件夹；项目搬动后会自动重建 |
| `电量托盘.lnk` | 快捷方式 | 指向固定位置的引导程序，和开机启动项一样与项目路径无关 |

桌面入口不复制项目文件，也不保存项目路径，删掉它不影响项目和开机启动。

## 程序图标

`wbtool/app.ico` 是程序自己的图标：深蓝圆角底、白色电池轮廓、绿色电量，包含
16/20/24/32/40/48/64/96/128/256 十个尺寸；16–32 的小尺寸单独把电池放大，避免糊成一团。

- 面板和调试窗口通过 `iconbitmap` 使用它，任务栏和 Alt-Tab 不再出现 Tk/Python 的默认图标；
- `--install` 把它复制到 `%LOCALAPPDATA%\电量托盘\电量托盘.ico`，桌面和开机启动的 `.lnk`
  图标位置指向这个固定副本，项目搬动后图标不受影响；
- 每次启动 `sync_pointers()` 会刷新该副本，替换 `app.ico` 后重启程序即可看到新图标。

## 配置与手动标定

设置文件：`settings.json`（不是日志文件）。
读数缓存：`last_state.json`（只保留每台设备最后一次成功读数和时间，不是日志）。

```json
{
  "refresh_seconds": 60,
  "show_tray_icon": true,
  "show_taskbar_strip": true,
  "hide_to_tray": true,
  "tray_devices": ["mouse", "keyboard"],
  "taskbar_devices": ["mouse", "keyboard"],
  "discovered": [],
  "removed": [],
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

## 设备类型识别

卡片和图标上的类型（键盘、鼠标、耳机、外设）来自设备自报的 HID 顶层集合，而不是
`settings.json` 里写死的 `kind`：

- 取该 VID:PID 的全部接口，按接口号 `MI_xx` 排序，**最低接口号上的主集合**决定类型。
  2.4G 接收器会额外暴露键盘集合（快捷键、音量键），键盘也会额外暴露鼠标集合
  （多媒体键），所以不能简单地「先看到键盘就判键盘」。
- `0x0001/0x0006` → 键盘，`0x0001/0x0002` → 鼠标；只有 `0x000C/0x0001`
  （Consumer Control）而没有键鼠集合 → 耳机；都没有 → 外设。
- 每次刷新都用枚举到的数据校正一次类型：与 `settings.json` 不一致时以设备为准，并
  同步写回 `targets` / `discovered` 条目；设备未连接、枚举不到时才沿用上次保存的类型。

## 连接方式识别

卡片上设备类型后面的小色块显示当前的连接方式：

| 色块 | 含义 | 判断依据 |
|---|---|---|
| 绿色 `2.4G` | 2.4G 接收器（USB Dongle） | HID 接口的祖先设备是 `USB\VID_...`，且产品名/厂商名包含 `Dongle`、`Receiver`、`2.4G`、`Wireless` 或 `接收器` |
| 蓝色 `蓝牙` | 蓝牙，含 BLE | 祖先设备的枚举器是 `BTHENUM` 或 `BTHLE` |
| 灰色 `有线` | USB 线直连 | 祖先设备是 USB，但没有接收器特征（避免把数据线误报成 2.4G） |

实现方式：HID 顶层集合本身永远挂在 `HID\VID_...` 节点下，光看接口路径分不出
USB 接收器和蓝牙，所以 `enumerate_hid()` 用 `CM_Get_Parent` 逐级往上走，取第一个
非 `HID` 父节点的枚举器，存进 `HidInterface.enumerator`；再由
`HidInterface.transport` 归类成上面三种文字。`BatteryService` 把它放进
`DeviceView.transport`，并随 `last_state.json` 一起缓存，所以设备休眠、枚举不到时
仍显示最后一次的连接方式。

色块是 `_rounded_image()` 在 CPU 上 6 倍超采样再降采样得到的 `PhotoImage`
（和胶囊开关同一套做法），因此胶囊边缘是渐变而不是 Tk 多边形的一像素台阶。

## 兜底优先级

每个目标按以下顺序尝试，上一步在**该设备的全部接口**上都没有得到合法百分比时，才进入下一步：

1. 通用 HID 电量字段：先在每个接口上试 Feature Report（`0x0006/0x0020` Battery Strength，
   以及 `0x000D/0x003B`、`0x0085` 电量页），再试 Input Report 中的同类字段；
2. 厂商协议表（已经适配过的设备才走这里）：Endgame Gear `0x3367`、VAXEE/ZYGEN `0x3057`、
   Nordic `0x1915`、AULA/MCHOSE 20 字节 `0x13/0x4A`、SONiX `0x20/0x01`、
   AULA F87ProV2D 状态帧 `55 10 18`、K-snake/EWEADN `AA 30`、罗技 HID++ 2.0
   （`0x1004` 统一电池、`0x1000` 电池状态、`0x1001` 电压）；
3. 手动标定 JSON。

所以新发现的设备总是先用通用协议读一次；通用字段没有响应，才会尝试按 VID/PID
匹配到的厂商协议，厂商协议再失败才用标定表。

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
| Tk 画布没有抗锯齿，任务栏小图标有台阶 | 电量条图标按像素覆盖率渲染成 `PhotoImage`（鼠标/键盘/耳机/电池），直线对齐像素保持清晰，圆角和斜线平滑 |
| 从任务栏/托盘打开的窗口停在别的程序后面 | 点击已经给本进程前台权限，`show()` 再用 `SetForegroundWindow` 前置，不依赖 Tk 的进程内 `lift` |

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
  icon.py                  窗口/快捷方式图标加载
  app.ico                  多尺寸程序图标（16–256，PNG 压缩）
  debugui.py               完整调试版
  shortcuts.py             .lnk COM、StartupApproved、目录联接
  installer.py             安装/卸载入口
  config.py                settings.json
  state.py                 last_state.json（最后一次读数缓存）
settings.json              配置（运行时可写，不是日志）
电量托盘启动器.pyw          与路径无关的引导程序（--install 复制到 %LOCALAPPDATA%）
```

## 验证结果

在本机 Windows 10 19044 上：

```text
python my_devices.py --once
YJX-CHIP：59%，放电中  [AULA 2.4G 厂商协议（AA 30）]
AULA F87 Pro V2 键盘：70%  [AULA 状态帧（55 10 18）]
```

- 鼠标读数与协议回复逐字节核对通过；请求头同时兼容 AULA Hub 官方版和 K-snake 社区版。
- 鼠标标题采用 HID `Manufacturer`，本机显示为 `YJX-CHIP`。
- 键盘状态帧的 `payload[25]` 与 Aula Hub 自身日志 `[battery] reading` 的 52% 一致；
  状态帧约每 5 秒一次，工具在 5.6 秒窗口内捕获。键盘进入深度休眠后不再广播，
  此时保留上次读数及时间，不会把电量改成未读取或 0%。
- 启动项已验证出现在 `Win32_StartupCommand`（Name=`电量托盘`，Location=`Startup`）。
- 启动项与桌面启动项只指向 `%LOCALAPPDATA%\电量托盘\电量托盘启动器.pyw`，不含项目路径；
  桌面项目文件夹为 Junction，指向项目目录，移动后自动重建。
- 移动/改名/换盘实测：把项目目录改名后再用启动项启动，引导程序在 1 秒内找到新位置并更新
  `location.json`，托盘正常运行；改回原名后同样自动恢复。
- 托盘窗口、两个图标、右键菜单（打开/刷新/退出）、左键打开面板并刷新、`--quit` 退出、托盘/面板单实例均已接入并验证。
- `--install` 可重复执行；重复执行不会产生副本或重复启动项。
- 键盘状态帧需要约 5 秒的监听窗口；即使关闭 Aula Hub，`--once` 仍能独立读到 51%，不依赖厂商软件。
- 托盘空闲 CPU 实测：75 秒内 `0.172` 秒 CPU，低于 0.5 秒验收线。
- 面板卡片的更新时间在右上角；卡片下半部分左侧是「数据来源」两行，右侧是两个垂直排列的
  「任务栏 / 托盘」开关，点击立即生效并写入 `settings.json`。
- 开关实测：关掉鼠标的「任务栏」后，任务栏电量格只剩键盘一格；关掉键盘的「托盘」后，
  `tray_devices` 只剩鼠标；两项各自独立，互不影响。
- 连接方式实测：键盘 `0C45:FEFE`（产品名 `AULA F87ProV2D Dongle`）和鼠标
  `A8A5:2255`（`USB Receiver`）的父设备枚举器都是 `USB`，卡片显示绿色 `2.4G`；
  代码对 `BTHENUM` / `BTHLE` 判定为蓝色 `蓝牙`，对无接收器特征的 USB 直连显示灰色 `有线`。
- 色块抗锯齿实测：把卡片放大 8 倍截图，胶囊四角为渐变过渡，没有 1px 台阶；
  单个色块首次生成约 18 ms，之后命中缓存。
- 项目内没有日志文件；`--debug` 才输出控制台信息。

## 说明

- 本工具不修改设备固件，也不写入鼠标/键盘的配置；厂商协议只发只读查询命令。
- 设备休眠或接收器在而键盘/鼠标关机时，界面保留上次读数并标记「上次」，不会显示假数据。
- 最后一次成功读数写入 `last_state.json`（仅保留最新值，不是日志）；即使键盘深度休眠或重启应用，
  面板/托盘仍显示最后一次电量，失败刷新不会覆盖它。
- 如果换设备，改 `settings.json` 的 VID/PID，或先用 `--debug-ui` 的「手动标定」摸协议。
- 托盘图标可能被 Windows 自动收进「隐藏的图标」溢出区，这是系统行为；可在任务栏设置里固定。
