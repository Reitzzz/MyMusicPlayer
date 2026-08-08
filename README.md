# 音乐定时播放系统 Pro

一个面向 Windows 的本地定时音乐播放器。它可以按照指定时间和星期自动播放歌单，支持固定曲目、固定时长循环、系统托盘和开机自启动。

## 主要功能

- 按星期和精确时间创建播放任务。
- 固定曲目模式：按顺序播放选中的歌曲，播放完毕后停止。
- 固定时长模式：循环播放歌单，到达结束时间后自动停止。
- 支持创建、修改、删除、启用和禁用任务。
- 自动扫描程序目录下的 `mp3/` 和 `changyong/` 文件夹。
- 支持 MP3、FLAC、WAV、OGG、M4A、WMA 和 AAC 文件。
- 关闭主窗口后继续在系统托盘运行。
- 支持 Windows 当前用户开机自启动和静默启动。
- 使用 Windows Mutex 防止同一用户会话中重复运行。
- 任务使用相对音乐路径，移动整个程序目录后仍可继续使用。

## 下载和使用 EXE

1. 从 GitHub Releases 下载 ZIP 压缩包。
2. 将 ZIP 完整解压到一个可写目录，不要直接在压缩包中运行。
3. 运行 `music.exe`。
4. 把音乐文件放入与 `music.exe` 同级的 `mp3/` 或 `changyong/` 文件夹。
5. 返回程序并点击“刷新音乐列表”。
6. 在“任务列表”中点击“+ 创建新任务”，按向导完成设置。

程序会在同级目录保存：

- `tasks.json`：定时任务和歌单配置。
- `config.json`：首次运行等程序设置。

请不要只移动 `music.exe`。建议始终整体移动包含 EXE、配置和音乐目录的文件夹。

## 任务模式

### 固定曲目

按照用户设置的歌曲顺序播放一次，全部播放完成后停止。

### 固定时长

在开始时间到结束时间之间循环播放歌单，到达结束时间后停止。当前版本要求开始时间和结束时间处于同一天，不支持跨午夜时间段。

如果任务中的歌曲全部不存在或无法播放，程序会停止该任务并显示错误，不会无限重试。

## 系统要求

运行打包后的 EXE：

- Windows 10 或 Windows 11
- 可用的音频输出设备
- 无需另外安装 Python

从源码运行或打包：

- Python 3.12
- CustomTkinter
- pygame
- pystray
- Pillow
- PyInstaller（仅打包需要）

## 从源码运行

本仓库维护环境固定使用 `D:\Python\python.exe`：

```powershell
D:\Python\python.exe -m pip install customtkinter pygame pystray Pillow pyinstaller
D:\Python\python.exe music.py
```

代码语法检查：

```powershell
D:\Python\python.exe -m py_compile music.py windowing.py song_widgets.py task_dialogs.py
```

## 打包 Windows EXE

仓库中的 `music.spec` 是正式 PyInstaller 配置。请在项目根目录执行：

```powershell
D:\Python\python.exe -m PyInstaller music.spec
```

打包完成后：

- 最终程序位于 `dist/music.exe`。
- `build/` 是临时构建缓存，可以删除。
- `dist/` 中的 EXE 是发布产物，不要在发布前删除。

建议发布 ZIP 结构：

```text
MusicScheduler-Pro/
├── music.exe
├── mp3/
└── changyong/
```

`config.json` 和 `tasks.json` 可以不放进首次发布包，程序会按需创建；如果希望提供空白文件，可分别使用 `{"first_run": false}` 和 `[]`。

## 项目结构

```text
MusicPlayer/
├── music.py          # 程序入口、主界面、任务协调、调度与播放逻辑
├── windowing.py      # DPI 控制、窗口基类和父子窗口状态同步
├── song_widgets.py   # 轻量歌曲行、文字省略与悬停跑马灯
├── task_dialogs.py   # 时间、歌曲、星期和命名任务向导
├── music.spec        # PyInstaller 打包配置
├── config.json       # 本地程序配置
├── tasks.json        # 本地任务数据
├── mp3/              # 普通音乐目录
├── changyong/        # 常用音乐目录
├── AGENTS.md         # 自动化代理协作约定
└── README.md
```

### 当前代码拆分原则

当前拆分属于不改变程序行为的第一阶段整理：

- `music.py` 仍是唯一启动入口，`music.spec` 不需要改用其他入口。
- `windowing.py` 不依赖主程序，只提供可复用的 DPI 和窗口能力。
- `song_widgets.py` 只负责歌曲行绘制和交互，不读取任务或配置。
- `task_dialogs.py` 通过父窗口与回调连接主程序，不反向导入 `music.py`。
- `music.py` 继续重导出拆出的窗口和控件类，兼容已有的 `import music` 调试代码。

依赖方向保持为：`song_widgets.py` 和 `windowing.py` → `task_dialogs.py` → `music.py`。禁止基础模块反向导入 `music.py`，避免循环导入。

### 什么时候进入第二阶段拆分

目前不应仅因为文件行数继续拆分。出现以下任一情况时，再考虑提取独立的任务存储、音频播放和调度服务：

- 任务 JSON 开始需要版本迁移、字段校验、自动备份、恢复或被多个模块同时读写。
- 音频功能加入暂停、音量、淡入淡出、输出设备切换、播放失败恢复，导致播放状态继续散落在主界面中。
- 调度需要支持跨午夜、系统休眠后补触发、时区、冲突任务或无需 GUI 的后台运行。
- 连续的功能修改必须同时改动主界面、播放状态和调度循环，已经难以单独验证其中一项。
- `MusicSchedulerApp` 的共享状态使后台线程直接依赖控件，或问题只能通过启动完整 GUI 才能复现。

进入第二阶段前，应先为以下纯逻辑补充自动化测试：

- 任务数据的读取、保存、默认值和旧数据兼容。
- 音频队列的下一首、循环、失败跳过和停止状态转换。
- 下次运行时间、星期匹配、跨日期边界和重复触发保护。
- 音频路径的相对化、程序目录迁移和文件缺失处理。

测试稳定后，再依次提取 `TaskStore`、`PlaybackEngine` 和 `SchedulerService`。这些服务应通过普通参数、返回值、回调或线程安全队列与 UI 通信，Tk 控件仍只允许在主线程更新。

## 使用注意事项

- 定时播放期间请关闭 Windows 自动睡眠，否则系统休眠时任务无法按时触发。
- 首次启用开机自启动后，如果移动了程序目录，请在新位置重新勾选“开机自启”。程序会识别并提示失效的旧启动路径。
- 删除或重命名任务中的音乐文件后，请刷新音乐列表并修改对应任务。
- 主窗口右上角的关闭按钮只会隐藏窗口；需要彻底退出时，请使用托盘菜单中的“退出程序”。

## 数据与隐私

程序在本地运行，不会上传音乐、任务或配置。任务数据和设置保存在程序目录中，请在升级或迁移前备份 `tasks.json`。
