# AGENTS.md

适用于本仓库根目录及所有子目录。产品功能、目录结构与调度边界见 [`README.md`](README.md)。

## Python 运行环境（强制）

- 所有 Python、依赖管理、测试与打包命令固定使用 `D:\Python\python.exe`，严禁调用裸 `python` 或其他解释器：

  ```powershell
  D:\Python\python.exe -m pip ...
  D:\Python\python.exe -m ...
  ```

- 单文件打包统一执行 `D:\Python\python.exe -m PyInstaller music.spec`，产物为 `dist/music.exe`。`build/`、`dist/`、`__pycache__/` 为构建生成目录，不手工编辑。

## 架构与用户状态不变量

- **模块边界**：`music.py` 负责单实例互斥、路径装配与启动入口；`player_core.py` 承载任务存储、音频播放、定时调度与核心状态控制；`web_app.py` 负责 `pywebview` 原生窗口、桥接层、系统托盘与串行事件队列；`ui/` 为离线前端页面与样式。
- **程序根目录**：源码运行取 `music.py` 所在目录，打包运行取 EXE 所在目录，严禁依赖启动时的当前工作目录或硬编码个人路径。
- **用户状态保护**：`mp3/`、`changyong/`、`config.json`、`tasks.json` 属于用户持久化状态，默认只读保留；测试与验证必须使用隔离的临时目录或副本，严禁触发真实用户任务改写或音频目录改动。
- **线程与生命周期安全**：前端仅通过桥接层异步接口读写后台权威状态；后台调度线程与托盘线程必须经线程安全队列派发原生窗口操作，严禁跨线程直接操纵窗口或前端对象。修改启动、播放或退出流程时，必须保证音频设备、托盘图标、后台线程与窗口的清理逻辑幂等且可重复执行。

## 验证命令

修改代码后按受影响范围执行以下验证，并如实区分自动化测试结果与需在 Windows 桌面人工确认的 GUI/托盘/音频行为：

```powershell
D:\Python\python.exe -m py_compile music.py player_core.py web_app.py
D:\Python\python.exe -m unittest discover -s tests -v
node --check ui/app.js
node tests/test_ui.js
```
