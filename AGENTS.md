# AGENTS.md

本文件适用于仓库根目录及其所有子目录。所有在本项目中工作的自动化代理都必须遵守以下规则。

## Python 环境（强制）

- 只能使用 `D:\Python` 目录下的 Python，默认解释器固定为 `D:\Python\python.exe`。
- 执行任何 Python 代码、脚本、模块、测试或构建命令时，必须显式写出完整解释器路径；不得调用 PATH 中的 `python`、`python3`、`py`、`pip` 或其他 Python 安装。
- 安装、查询或升级依赖时统一使用：`D:\Python\python.exe -m pip ...`。
- 运行 PyInstaller 时统一使用：`D:\Python\python.exe -m PyInstaller music.spec`。
- 创建虚拟环境时也必须由该解释器创建，例如：`D:\Python\python.exe -m venv .venv`。进入虚拟环境后，在没有用户明确许可的情况下，仍优先直接使用 `D:\Python\python.exe`，避免解释器来源不清。
- 执行前如需确认环境，使用：`D:\Python\python.exe -c "import sys; print(sys.executable)"`。若输出不位于 `D:\Python`，立即停止，不得改用其他解释器凑合执行。

## 项目说明

- 这是一个面向 Windows 的本地定时音乐播放器，主程序为 `music.py`。
- GUI 使用 CustomTkinter；音频播放使用 pygame；系统托盘使用 pystray/Pillow；程序还依赖 Windows 注册表和 Mutex 等平台能力。
- `music.spec` 是 PyInstaller 打包配置。
- `mp3/` 与 `changyong/` 存放用户音频；`config.json` 与 `tasks.json` 属于运行时/用户数据。

## 修改原则

- 修改前先阅读相关代码和配置，只改完成当前任务所必需的内容；保留用户已有改动。
- 未经用户明确要求，不得删除、重命名、批量移动或改写 `mp3/`、`changyong/` 中的文件，也不得重置 `config.json`、`tasks.json`。
- 不手工修改 `build/`、`dist/`、`__pycache__/` 等生成目录；需要更新时通过构建命令重新生成。
- 保持 Windows 兼容性。涉及路径时使用 `pathlib` 或正确的 Windows 路径处理，避免硬编码当前用户目录。
- 源文件和文档统一保存为 UTF-8；不要因为终端乱码而擅自转换原文件编码，先用明确的 UTF-8 参数重新读取确认。
- 不在日志、示例或提交内容中写入真实个人路径、隐私数据、访问令牌或其他敏感信息。

## 代码约定

- 优先做小而清晰的改动，沿用 `music.py` 的现有结构和命名风格；除非任务需要，不进行大规模重构。
- UI 更新必须在 Tk 主线程执行；后台线程不得直接操作控件。共享的播放状态、任务状态和文件数据需要考虑线程安全。
- 所有文件、音频设备、托盘对象和后台线程都应有明确的异常处理与清理逻辑。
- 面向用户的错误应给出可理解的提示；诊断信息应保留足够上下文，但不得泄露敏感数据。
- 新增第三方依赖前先说明理由，并同步更新项目文档或依赖说明；不要仅为一个简单功能引入大型依赖。

## 验证与交付

- 每次修改 Python 代码后，至少执行语法检查：
  `D:\Python\python.exe -m py_compile music.py`
- 若新增了测试，使用 `D:\Python\python.exe -m pytest` 运行；若未安装 pytest，不得悄悄切换到其他 Python，应报告缺失依赖。
- 涉及 GUI、系统托盘、开机启动或音频播放的改动，应说明哪些部分已自动验证、哪些仍需要在 Windows 桌面环境中人工验证。
- 涉及打包的改动，使用 `D:\Python\python.exe -m PyInstaller music.spec` 验证，并检查生成程序的启动、资源加载和退出行为。
- 完成后简要列出修改内容、执行过的验证以及尚未验证的风险；不得声称未实际执行的检查已经通过。
