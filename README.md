# 定时播放器

一个面向 Windows 的本地定时音乐播放器。程序使用固定 1000×750 逻辑像素的无边框深色网页界面，音频和任务数据全部留在本地，不依赖 CDN 或在线服务。

## 运行环境

- Windows 10/11
- Python 3.11+（本仓库约定解释器为 `D:\Python\python.exe`）
- Microsoft Edge WebView2 Runtime
- pygame、pystray、Pillow、pywebview 6.x

安装依赖：

```powershell
D:\Python\python.exe -m pip install "pywebview>=6.2,<7" pygame pystray Pillow pyinstaller
```

如果 WebView2 Runtime 或 pywebview 缺失，程序不会回退到旧的 Tk 界面，而是显示可理解的 Windows 原生错误提示。

## 启动与打包

源码启动：

```powershell
D:\Python\python.exe music.py
```

静默启动（用于开机自启，窗口保留在系统托盘）：

```powershell
D:\Python\python.exe music.py --silent
```

打包：

```powershell
D:\Python\python.exe -m PyInstaller music.spec
```

`music.spec` 生成独立的单文件可执行程序 `dist/music.exe`。静态界面资源（`ui/`）已内置打包，运行时自动解包加载；用户配置与音乐目录（`tasks.json`、`config.json`、`mp3/`、`changyong/`）仍保存在 `music.exe` 所在同级目录下。

## 使用说明

1. 将 `.mp3`、`.flac`、`.wav`、`.ogg`、`.m4a`、`.wma` 或 `.aac` 文件放入程序目录下的 `mp3/` 或 `changyong/`，点击“刷新音乐列表”。
2. 在“播放任务”中创建任务，按四步向导设置开始时间与模式、歌曲及顺序、星期、名称。
3. “固定曲目”按顺序播放一次；“固定时长”循环歌单，达到结束时间停止，可选“次日结束”。
4. 关闭窗口会隐藏到托盘；托盘菜单可重新显示窗口或退出程序。托盘不可用时关闭窗口会直接退出。

任务删除是立即删除并通过 Toast 提示，不提供撤销或确认弹层。所有写操作都由后台桥接层验证，并返回保存后的权威状态。

## 数据与路径

- `tasks.json`：任务列表，保持原有字段结构（`time`、`mode`、`end_time`、`end_next_day`、`files`、`name`、`weekdays`、`enabled`）。音频路径优先保存为相对于程序目录的便携路径。
- `config.json`：首次运行标记等配置。
- `mp3/`、`changyong/`：用户音频目录，程序不会删除、移动或重编码其中内容。

读取 `tasks.json` 时，如果发现 JSON 损坏或显式字段无效，程序会先生成唯一的 `tasks.corrupt-*.json` 备份，再加载可用任务；备份失败时进入只读保护。正常保存使用临时文件、刷新和原子替换，避免半写入文件。

程序目录以 `music.py` 所在目录（打包后为 EXE 所在目录）为资源根，不依赖调用程序时的当前工作目录。单实例由 Windows Mutex 保护，重复启动会唤醒已有窗口。

## 代码结构

```text
music.py          路径、Mutex 与启动装配入口
player_core.py    TaskStore、PlaybackEngine、SchedulerService、控制器
web_app.py        pywebview 桥接、窗口、托盘、串行事件队列
ui/index.html     离线 HTML 与 SVG 图标
ui/app.css        方案 B（夜航播放）视觉令牌与布局
ui/app.js         状态渲染、交互和四步任务向导
music.spec        PyInstaller 配置
tests/            不依赖 GUI 的回归测试
```

调度器每 500ms 检查一次，支持最多 90 秒的延迟补触发；已触发时间点不会重复执行，系统时钟回拨会重置基线而不补播。播放失败时会跳过坏曲目，固定时长结束后停止，所有操作都经过可重复的退出清理。

## 验证

```powershell
D:\Python\python.exe -c "import sys; print(sys.executable)"
D:\Python\python.exe -m py_compile music.py player_core.py web_app.py
D:\Python\python.exe -m unittest discover -s tests -v
D:\Python\python.exe -m PyInstaller music.spec
```

浏览器交互/视觉检查可使用 `ui/index.html?mock=1`。mock 仅在显式查询参数存在时启用，生产页默认只等待真实的 pywebview bridge。
