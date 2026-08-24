import customtkinter as ctk
import os
import sys
import threading
import queue
import pygame
import winreg
import json
import ctypes
import copy
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import pystray
from PIL import Image, ImageDraw

from windowing import (
    StableDpiScalingController,
    DpiStableCTk,
    DpiStableToplevel,
    TaskFlowWindow,
    destroy_widget_tree,
)
from song_widgets import SongRowCanvas
from task_dialogs import (
    TaskRenameDialog,
    WeekdaySelectionDialog,
    MultiSongSelectDialog,
    TimeModeDialog,
    format_clock_for_display,
)

# 获取当前程序（exe或py）所在的真实目录
if getattr(sys, 'frozen', False):
    # 如果是打包后的 exe
    application_path = os.path.dirname(sys.executable)
else:
    # 如果是 python 脚本
    application_path = os.path.dirname(os.path.abspath(__file__))

# 强制将工作目录切换到程序所在目录
os.chdir(application_path)
# ========================================================

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")
TASKS_FILE = "tasks.json"
APPLICATION_DIR = Path(application_path).resolve()
TASKS_PATH = APPLICATION_DIR / TASKS_FILE
MUSIC_DIRECTORIES = ("mp3", "changyong")
SCHEDULE_GRACE_SECONDS = 90


def resolve_music_path(path_value):
    """将任务中保存的相对路径或旧绝对路径解析到当前程序目录。"""
    path = Path(path_value)

    if not path.is_absolute():
        return str((APPLICATION_DIR / path).resolve())

    # 兼容移动程序目录前保存的绝对路径。
    for index, part in enumerate(path.parts):
        if part.lower() in MUSIC_DIRECTORIES:
            current_candidate = APPLICATION_DIR.joinpath(*path.parts[index:]).resolve()
            if current_candidate.exists() or not path.exists():
                return str(current_candidate)

    if path.exists():
        return str(path.resolve())

    return str(path)


def make_portable_music_path(path_value):
    """优先将音频路径保存为相对程序目录的便携路径。"""
    resolved_path = Path(resolve_music_path(path_value))
    try:
        return resolved_path.relative_to(APPLICATION_DIR).as_posix()
    except ValueError:
        # 兼容未来可能由其他入口选择的程序目录外文件。
        return str(resolved_path)


def calculate_task_end_at(task, started_at):
    """Calculate the concrete stop datetime for a duration-mode task."""
    if task.get("mode", "song") != "duration":
        return None
    try:
        end_clock = datetime.strptime(task.get("end_time", ""), "%H:%M:%S").time()
    except (TypeError, ValueError):
        return None

    end_at = datetime.combine(started_at.date(), end_clock)
    if bool(task.get("end_next_day", False)):
        end_at += timedelta(days=1)
    return end_at


# ========================================================
# 4. 主程序逻辑
# ========================================================
class MusicSchedulerApp(DpiStableCTk):
    def __init__(self):
        super().__init__()

        self.title("音乐定时播放系统 Pro")
        self.geometry("1000x750")

        # 修复：获取参数前也需要确保工作目录，已在文件头处理
        if "--silent" in sys.argv:
            self.withdraw()
        else:
            self.deiconify()

        try:
            pygame.init()
            pygame.mixer.init()
            pygame.mixer.music.set_volume(1.0)
        except Exception as e:
            print(f"音频初始化警告: {e}")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tasks = []
        self._tasks_load_state = "pending"
        self._tasks_backup_path = None
        self.music_files = []
        self.active_task_dialog = None
        self._help_win = None
        self._help_win_forced = False
        
        # 播放状态变量
        self.playlist_queue = []      
        self.current_track_index = 0  
        self.is_playlist_active = False 
        self.current_task_name = "" 
        self.current_task_mode = "song" 
        self.current_task_end_at = None

        # 主循环与跨线程事件状态
        self.running = True
        self._closing = False
        self.last_tick_dt = None
        self.ui_event_queue = queue.Queue()
        self.tray_icon = None
        self.tray_thread = None
        self._tray_unavailable = False
        self._tray_alert_shown = False
        
        self.auto_start_var = ctk.BooleanVar(value=False)

        self.create_sidebar()
        self.create_main_area()
        self.create_footer()
        self.check_startup_status()
        
        # 延时加载，确保UI先出来
        self.after(100, self.load_music_files)
        self.after(200, self.load_tasks) 
        
        self.setup_tray_icon()

        # 调度和 UI 事件都由 Tk 主线程处理。
        self.after(50, self.process_ui_events)
        self.after(500, self.check_schedule_tick)

        self.after(500, self.check_first_run)

    # --- 持久化存储 ---
    def _set_tasks_load_state(self, state):
        """更新任务存储状态，并同步任务编辑入口。"""
        if state not in {"pending", "ready", "failed"}:
            raise ValueError(f"未知任务加载状态: {state}")
        self._tasks_load_state = state
        create_button = getattr(self, "create_task_btn", None)
        if create_button is not None:
            try:
                create_button.configure(state="normal" if state == "ready" else "disabled")
            except Exception:
                pass

    def _tasks_are_writable(self):
        return getattr(self, "_tasks_load_state", "pending") == "ready"

    def _warn_tasks_read_only(self):
        state = getattr(self, "_tasks_load_state", "pending")
        if state == "pending":
            message = "任务数据仍在加载，请稍后再试"
            detail = None
        else:
            message = "任务文件读取失败，本次运行已进入只读保护"
            backup_path = getattr(self, "_tasks_backup_path", None)
            detail = (
                f"原文件备份为 {Path(backup_path).name}，请修复 tasks.json 后重启程序。"
                if backup_path
                else "原文件未被覆盖，请检查 tasks.json 后重启程序。"
            )
        self.status_label.configure(text=message, text_color="red")
        self.show_error_alert(message, title="任务数据只读", detail=detail)

    def _backup_tasks_file(self, tasks_path):
        """复制原始任务文件到唯一的损坏备份，不改动源文件。"""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = tasks_path.with_name(
            f"{tasks_path.stem}.corrupt-{timestamp}{tasks_path.suffix}"
        )
        counter = 1
        while backup_path.exists():
            backup_path = tasks_path.with_name(
                f"{tasks_path.stem}.corrupt-{timestamp}-{counter}{tasks_path.suffix}"
            )
            counter += 1
        try:
            shutil.copy2(tasks_path, backup_path)
        except Exception:
            try:
                backup_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        self._tasks_backup_path = backup_path
        return backup_path

    @staticmethod
    def _is_valid_clock(value):
        if not isinstance(value, str):
            return False
        try:
            parsed = datetime.strptime(value, "%H:%M:%S")
        except ValueError:
            return False
        return parsed.strftime("%H:%M:%S") == value

    def _sanitize_task(self, raw_task):
        """返回 (净化任务或 None, 有损修正数)。

        只有真正丢失信息才计入有损修正：丢弃条目，或把"显式存在但非法"的值
        替换为默认值。无损归一化不计入，避免旧格式任务每次启动都被判为损坏：
        缺失字段补默认值、音频路径便携化、任务名去除首尾空白。
        """
        if not isinstance(raw_task, dict):
            return None, 1

        task = dict(raw_task)
        lossy = 0

        time_value = raw_task.get("time")
        if not self._is_valid_clock(time_value):
            return None, 1
        task["time"] = time_value

        raw_weekdays = raw_task.get("weekdays", [])
        weekdays = []
        if isinstance(raw_weekdays, list):
            for day in raw_weekdays:
                if (
                    isinstance(day, int)
                    and not isinstance(day, bool)
                    and 0 <= day <= 6
                    and day not in weekdays
                ):
                    weekdays.append(day)
            if len(weekdays) != len(raw_weekdays):
                lossy += 1
        elif "weekdays" in raw_task:
            lossy += 1
        task["weekdays"] = weekdays

        raw_name = raw_task.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            name = raw_name.strip()
        else:
            name = "未命名任务"
            if "name" in raw_task:
                lossy += 1
        task["name"] = name

        raw_files = raw_task.get("files", [])
        files = []
        if isinstance(raw_files, list):
            for path_value in raw_files:
                if not isinstance(path_value, str) or not path_value.strip():
                    continue
                try:
                    files.append(make_portable_music_path(path_value))
                except Exception:
                    continue
            if len(files) != len(raw_files):
                lossy += 1
        elif "files" in raw_task:
            lossy += 1
        task["files"] = files

        raw_mode = raw_task.get("mode", "song")
        mode = raw_mode if raw_mode in {"song", "duration"} else "song"
        if "mode" in raw_task and raw_mode != mode:
            lossy += 1
        task["mode"] = mode

        raw_end_time = raw_task.get("end_time", "")
        if mode == "duration":
            if not self._is_valid_clock(raw_end_time):
                return None, lossy + 1
            end_time = raw_end_time
        else:
            end_time = raw_end_time if isinstance(raw_end_time, str) else ""
            if "end_time" in raw_task and raw_end_time != end_time:
                lossy += 1
        task["end_time"] = end_time

        raw_end_next_day = raw_task.get("end_next_day", False)
        end_next_day = raw_end_next_day if isinstance(raw_end_next_day, bool) else False
        if "end_next_day" in raw_task and raw_end_next_day != end_next_day:
            lossy += 1
        task["end_next_day"] = end_next_day

        raw_enabled = raw_task.get("enabled", True)
        enabled = raw_enabled if isinstance(raw_enabled, bool) else False
        if "enabled" in raw_task and raw_enabled != enabled:
            lossy += 1
        task["enabled"] = enabled
        return task, lossy

    def save_tasks(self):
        if not self._tasks_are_writable():
            self._warn_tasks_read_only()
            return False

        tasks_path = Path(TASKS_PATH)
        temp_path = tasks_path.with_name(f"{tasks_path.name}.tmp")
        try:
            tasks_to_save = []
            for task in self.tasks:
                saved_task = dict(task)
                saved_task["files"] = [
                    make_portable_music_path(path)
                    for path in task.get("files", [])
                ]
                tasks_to_save.append(saved_task)

            with temp_path.open('w', encoding='utf-8') as f:
                json.dump(tasks_to_save, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, tasks_path)
            return True
        except Exception as e:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.status_label.configure(text=f"保存失败: {e}", text_color="red")
            self.show_error_alert(
                "任务设置未能保存，请检查程序目录的写入权限或磁盘空间。",
                title="保存失败",
                detail=str(e),
            )
            return False

    def _save_tasks_or_restore(self, previous_tasks):
        """保存当前任务；失败时恢复修改前的任务和界面。"""
        if self.save_tasks():
            return True
        self.tasks = previous_tasks
        self.refresh_task_list()
        return False

    def load_tasks(self):
        tasks_path = Path(TASKS_PATH)
        self._tasks_backup_path = None
        self._set_tasks_load_state("pending")

        if not tasks_path.exists():
            self.tasks = []
            self._set_tasks_load_state("ready")
            self.refresh_task_list()
            self.status_label.configure(text="无历史任务记录", text_color="gray")
            return

        try:
            with tasks_path.open('r', encoding='utf-8-sig') as f:
                loaded_data = json.load(f)
            if not isinstance(loaded_data, list):
                raise ValueError("tasks.json 顶层必须是任务列表")
        except Exception as e:
            backup_error = None
            try:
                backup_path = self._backup_tasks_file(tasks_path)
            except Exception as backup_exception:
                backup_path = None
                backup_error = backup_exception

            self.tasks = []
            self._set_tasks_load_state("failed")
            self.refresh_task_list()
            if backup_path is not None:
                detail = f"原文件已备份为 {backup_path.name}。请修复 tasks.json 后重启程序。"
            else:
                detail = f"原文件未被覆盖，但自动备份失败：{backup_error}"
            self.status_label.configure(text=f"读取任务失败: {e}", text_color="red")
            self.show_error_alert(
                "任务文件无法读取，本次运行已进入只读保护。",
                title="读取任务失败",
                detail=detail,
            )
            return

        sanitized_tasks = []
        skipped_count = 0
        lossy_count = 0
        for raw_task in loaded_data:
            task, lossy = self._sanitize_task(raw_task)
            if task is None:
                skipped_count += 1
                continue
            sanitized_tasks.append(task)
            lossy_count += lossy

        needs_backup = skipped_count > 0 or lossy_count > 0
        backup_path = None
        backup_error = None
        if needs_backup:
            try:
                backup_path = self._backup_tasks_file(tasks_path)
            except Exception as e:
                backup_error = e

        self.tasks = sanitized_tasks
        self._set_tasks_load_state(
            "ready" if not needs_backup or backup_path is not None else "failed"
        )
        self.refresh_task_list()

        if needs_backup and backup_path is not None:
            message = (
                f"已加载 {len(self.tasks)} 个任务，跳过 {skipped_count} 个，"
                f"修正 {lossy_count} 个字段"
            )
            self.status_label.configure(text=message, text_color="orange")
            self.show_error_alert(
                "任务文件中存在无效数据，已保留可用任务。",
                title="任务数据已修复",
                detail=f"原文件已备份为 {backup_path.name}。",
            )
        elif needs_backup:
            self.status_label.configure(
                text="任务数据存在问题且备份失败，已进入只读保护",
                text_color="red",
            )
            self.show_error_alert(
                "任务文件包含无效数据，自动备份失败，本次运行仅可查看。",
                title="任务数据只读",
                detail=str(backup_error),
            )
        else:
            self.status_label.configure(
                text=f"已加载 {len(self.tasks)} 个任务",
                text_color="gray",
            )

    def show_error_alert(self, msg, *, title="操作提示", detail=None):
        detail_text = " ".join(str(detail).split()) if detail else ""
        if len(detail_text) > 180:
            detail_text = f"{detail_text[:177]}..."

        width = 360 if detail_text else 320
        height = 230 if detail_text else 180
        err_win = DpiStableToplevel(self)
        err_win.title(title)
        err_win.geometry(f"{width}x{height}")
        err_win.resizable(False, False)
        err_win.attributes("-topmost", True)
        err_win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (width // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (height // 2)
        if x < 0: x = 100
        if y < 0: y = 100
        err_win.geometry(f"+{x}+{y}")
        
        err_win.grab_set()

        ctk.CTkLabel(err_win, text="⚠️", font=ctk.CTkFont(size=40)).pack(pady=(15, 0))
        ctk.CTkLabel(err_win, text=msg, font=ctk.CTkFont(size=14), text_color="#CC0000", 
                     wraplength=width - 40, justify="center").pack(expand=True, padx=10, pady=5)
        if detail_text:
            ctk.CTkLabel(
                err_win,
                text=f"详细信息: {detail_text}",
                font=ctk.CTkFont(size=11),
                text_color="gray",
                wraplength=width - 40,
                justify="left",
            ).pack(fill="x", padx=20, pady=(0, 8))
        
        ctk.CTkButton(err_win, text="确定", command=err_win.destroy, 
                      width=100, fg_color="#CC0000", hover_color="#990000").pack(pady=(0, 20))

    def create_tray_image(self):
        width = 64
        height = 64
        color_bg = "#1F6AA5" 
        color_fg = "white"   
        image = Image.new('RGB', (width, height), color_bg)
        d = ImageDraw.Draw(image)
        d.rectangle((16, 16, 48, 48), fill=color_fg)
        return image

    def setup_tray_icon(self):
        try:
            menu = (
                pystray.MenuItem('显示窗口', self.show_window_from_tray, default=True),
                pystray.MenuItem('退出程序', self.quit_app_from_tray)
            )
            icon_image = self.create_tray_image()
            self.tray_icon = pystray.Icon("MusicScheduler", icon_image, "定时播放器", menu)
            self.tray_thread = threading.Thread(target=self._run_tray_icon, daemon=True)
            self.tray_thread.start()
        except Exception as e:
            self.tray_icon = None
            self.tray_thread = None
            self._handle_tray_failure(e)

    def _run_tray_icon(self):
        """运行托盘循环，并将线程内错误交回 Tk 主线程。"""
        icon = self.tray_icon
        if icon is None:
            return
        try:
            icon.run()
        except Exception as e:
            self.ui_event_queue.put(("tray_failed", str(e)))
        else:
            if self.running and not self._closing:
                self.ui_event_queue.put(("tray_failed", "托盘线程意外停止"))

    def _handle_tray_failure(self, detail=None):
        """在 Tk 主线程中降级为无托盘模式。"""
        self.tray_icon = None
        self.tray_thread = None
        self._tray_unavailable = True
        self.deiconify()
        self.lift()
        # 延后提示：同步失败发生在 __init__ 内，若立即写状态栏会被随后的
        # load_tasks 覆盖；延后同时保证弹窗在主循环内创建。
        self.after(300, lambda: self._announce_tray_unavailable(detail))

    def _announce_tray_unavailable(self, detail=None):
        """重申托盘不可用状态，并只弹一次告警。"""
        if not self._tray_unavailable:
            return
        try:
            self.status_label.configure(
                text="系统托盘不可用，关闭窗口将直接退出",
                text_color="orange",
            )
        except Exception:
            pass
        if self._tray_alert_shown:
            return
        self._tray_alert_shown = True
        self.show_error_alert(
            "系统托盘不可用，程序将以窗口模式运行；关闭主窗口会直接退出。",
            title="系统托盘不可用",
            detail=detail,
        )

    def show_window_from_tray(self, icon=None, item=None):
        self.ui_event_queue.put("show")

    def quit_app_from_tray(self, icon=None, item=None):
        self.ui_event_queue.put("quit")

    def process_ui_events(self):
        """在 Tk 主线程中处理来自托盘线程的窗口操作。"""
        try:
            while True:
                event = self.ui_event_queue.get_nowait()
                if isinstance(event, tuple):
                    action = event[0]
                    detail = event[1] if len(event) > 1 else None
                else:
                    action = event
                    detail = None
                if action == "show":
                    self.deiconify()
                    self.lift()
                elif action == "quit":
                    self.on_real_close()
                    return
                elif action == "tray_failed":
                    self._handle_tray_failure(detail)
        except queue.Empty:
            pass

        if self.running:
            self.after(50, self.process_ui_events)

    def on_close(self):
        if self.tray_icon is None:
            self.on_real_close()
        else:
            self.withdraw()

    def on_real_close(self):
        if self._closing:
            return

        self._closing = True
        self.running = False
        tray_icon = self.tray_icon
        tray_thread = self.tray_thread
        self.tray_icon = None
        self.tray_thread = None
        try:
            if tray_icon is not None:
                tray_icon.stop()
        except Exception:
            pass
        try:
            if (
                tray_thread is not None
                and tray_thread is not threading.current_thread()
                and tray_thread.is_alive()
            ):
                tray_thread.join(timeout=2.0)
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        self.destroy()

    def create_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="定时播放器 Pro", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.sidebar_button_refresh = ctk.CTkButton(self.sidebar_frame, text="刷新音乐列表", command=self.load_music_files)
        self.sidebar_button_refresh.grid(row=1, column=0, padx=20, pady=10)
        
        self.help_btn = ctk.CTkButton(self.sidebar_frame, text="查看使用说明", fg_color="gray", 
                                      command=lambda: self.show_help_window(forced_countdown=False))
        self.help_btn.grid(row=2, column=0, padx=20, pady=5)

        self.label_settings = ctk.CTkLabel(self.sidebar_frame, text="系统设置", anchor="w")
        self.label_settings.grid(row=3, column=0, padx=20, pady=(10, 0))

        self.check_startup = ctk.CTkCheckBox(self.sidebar_frame, text="开机自启", variable=self.auto_start_var, command=self.toggle_startup)
        self.check_startup.grid(row=4, column=0, padx=20, pady=10, sticky="w")

        self.stop_btn = ctk.CTkButton(self.sidebar_frame, text="停止播放", fg_color="#FF4D4D", hover_color="#CC0000", text_color="white", command=self.stop_music)
        self.stop_btn.grid(row=7, column=0, padx=20, pady=20)

    def create_main_area(self):
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew")
        self.main_frame.grid_rowconfigure(1, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        self.top_info_frame = ctk.CTkFrame(self.main_frame)
        self.top_info_frame.grid(row=0, column=0, padx=20, pady=20, sticky="ew")
        
        self.time_label = ctk.CTkLabel(self.top_info_frame, text="00:00:00", font=ctk.CTkFont(size=48, weight="bold"), text_color="#1F6AA5")
        self.time_label.pack(side="left", padx=20, pady=10)
        
        self.next_task_label = ctk.CTkLabel(self.top_info_frame, text="无任务", font=ctk.CTkFont(size=16), anchor="e")
        self.next_task_label.pack(side="right", padx=20, fill="x")

        self.tab_view = ctk.CTkTabview(self.main_frame)
        self.tab_view.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        
        self.tab_schedule = self.tab_view.add("任务列表")
        self.tab_music = self.tab_view.add("音乐库")
        self.setup_schedule_tab()
        self.setup_music_tab()

    def setup_schedule_tab(self):
        self.tab_schedule.grid_columnconfigure(0, weight=1)
        self.tab_schedule.grid_rowconfigure(1, weight=1) 

        input_frame = ctk.CTkFrame(self.tab_schedule)
        input_frame.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")

        # 将原来的输入框逻辑替换为一个“创建新任务”的大按钮
        ctk.CTkLabel(input_frame, text="任务管理:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=20)
        
        self.create_task_btn = ctk.CTkButton(
            input_frame,
            text="+ 创建新任务",
            width=150,
            height=35,
            fg_color="#1F6AA5",
            font=ctk.CTkFont(weight="bold"),
            command=self.initiate_add_task_flow,
            state="disabled",
        )
        self.create_task_btn.pack(side="left", padx=10, pady=15)
        
        ctk.CTkLabel(input_frame, text="支持: 固定歌曲播放 / 固定时长播放", text_color="gray", font=ctk.CTkFont(size=12)).pack(side="right", padx=20)

        self.schedule_scroll = ctk.CTkScrollableFrame(self.tab_schedule, label_text="已定任务 (自动保存)")
        self.schedule_scroll.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")

    def setup_music_tab(self):
        self.tab_music.grid_columnconfigure(0, weight=1)
        self.tab_music.grid_rowconfigure(0, weight=1)
        self.music_list_scroll = ctk.CTkScrollableFrame(self.tab_music, label_text="发现的音频文件")
        self.music_list_scroll.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

    def create_footer(self):
        self.footer_frame = ctk.CTkFrame(self, height=40, corner_radius=0)
        self.footer_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.status_label = ctk.CTkLabel(self.footer_frame, text="就绪", text_color="gray")
        self.status_label.pack(side="left", padx=20)
        ctk.CTkLabel(self.footer_frame, text="v1.2.0 Pro", font=ctk.CTkFont(size=10)).pack(side="right", padx=20)
    
    def check_first_run(self):
        # 强制切换工作目录后，直接读 config.json 即可
        config_file = "config.json"
        if not os.path.exists(config_file):
            self.show_help_window(forced_countdown=True)
            try:
                with open(config_file, 'w') as f:
                    json.dump({"first_run": False}, f)
            except:
                pass

    def show_help_window(self, forced_countdown=False):
        existing = getattr(self, "_help_win", None)
        existing_alive = False
        if existing is not None:
            try:
                existing_alive = bool(existing.winfo_exists())
            except Exception:
                existing_alive = False

        if existing_alive:
            if forced_countdown and not getattr(self, "_help_win_forced", False):
                try:
                    existing.destroy()
                except Exception:
                    pass
            else:
                try:
                    existing.lift()
                    existing.focus_force()
                except Exception:
                    pass
                return

        help_win = DpiStableToplevel(self)
        self._help_win = help_win
        self._help_win_forced = bool(forced_countdown)
        help_win.title("使用说明")
        help_win.geometry("480x550") 
        help_win.attributes("-topmost", True)

        def clear_help_reference(event=None):
            if event is not None and getattr(event, "widget", help_win) is not help_win:
                return
            if getattr(self, "_help_win", None) is help_win:
                self._help_win = None
                self._help_win_forced = False

        def close_help_window():
            clear_help_reference()
            try:
                if help_win.winfo_exists():
                    help_win.destroy()
            except Exception:
                pass

        try:
            help_win.bind("<Destroy>", clear_help_reference, add="+")
        except Exception:
            pass

        if forced_countdown:
            help_win.protocol("WM_DELETE_WINDOW", lambda: None)
        else:
            help_win.protocol("WM_DELETE_WINDOW", close_help_window)
        
        ctk.CTkLabel(help_win, text="使用说明", font=ctk.CTkFont(size=20, weight="bold"), text_color="#1F6AA5").pack(pady=(20, 10))
        
        scroll_help = ctk.CTkScrollableFrame(help_win, fg_color="transparent")
        scroll_help.pack(fill="both", expand=True, padx=20, pady=5)
        
        text_process = (
            "1. 【创建任务】\n"
            "   ① 将音频文件放入软件目录下的 mp3 或 changyong 文件夹\n"
            "   ② 点击左侧“刷新音乐列表”，再点击“+ 创建新任务”\n"
            "   ③ 设置开始时间和播放行为\n"
            "   ④ 按向导依次完成：选择歌曲 -> 设置星期 -> 设置任务名称\n\n"
            "2. 【时间与播放模式】\n"
            "   时间由小时和分钟两栏组成；可输入数字、使用上下键、\n"
            "   鼠标滚轮或右侧按钮调整，也可粘贴如 13:00 或 1300。\n"
            "   “固定曲目”会按顺序播放一次；“固定时长”会循环播放。\n"
            "   如需跨午夜，请在固定时长模式勾选“次日结束”。\n\n"
            "3. 【修改任务】\n"
            "   在任务列表中点击“修改”按钮，可以重新设置时间、歌曲、\n"
            "   播放星期和名称。\n\n"
            "4. 【托盘与注意事项】\n"
            "   普通启动会显示主窗口；开机自启会静默启动并显示托盘图标。\n"
            "   托盘正常时，点击右上角关闭按钮会隐藏窗口；请在托盘菜单中\n"
            "   选择“退出程序”才能彻底退出。托盘不可用时关闭窗口会直接退出。\n"
            "   - 支持格式：mp3, flac, wav, ogg, m4a, wma, aac\n"
            "   - 请务必禁用电脑自动休眠，以免影响播放"
        )
        ctk.CTkLabel(scroll_help, text=text_process, justify="left", font=ctk.CTkFont(size=14), anchor="w").pack(fill="x", pady=5)
        
        ctk.CTkLabel(scroll_help, text="   - 建议开启开机启动 (在左侧设置中勾选)", text_color="#FF4D4D", 
                     justify="left", font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x")
        
        btn_text = "我知道了"
        btn_state = "normal"
        if forced_countdown:
            btn_text = "请阅读 (10s)"
            btn_state = "disabled"

        btn_know = ctk.CTkButton(
            help_win,
            text=btn_text,
            state=btn_state,
            command=close_help_window,
        )
        btn_know.pack(pady=20)

        if forced_countdown:
            countdown_val = 10

            def update_countdown():
                nonlocal countdown_val
                try:
                    if not help_win.winfo_exists():
                        return
                    countdown_val -= 1
                    if countdown_val > 0:
                        btn_know.configure(text=f"请阅读 ({countdown_val}s)")
                        help_win.after(1000, update_countdown)
                    else:
                        btn_know.configure(text="我知道了", state="normal")
                        help_win.protocol("WM_DELETE_WINDOW", close_help_window)
                except Exception:
                    try:
                        window_alive = bool(help_win.winfo_exists())
                    except Exception:
                        window_alive = False
                    if not window_alive:
                        clear_help_reference()

            help_win.after(1000, update_countdown)

    # --- 开机自启逻辑 ---
    def get_startup_command(self):
        """返回当前程序位置对应的开机启动命令。"""
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}" --silent'

        python_exe = sys.executable
        script_path = Path(__file__).resolve()
        return f'"{python_exe}" "{script_path}" --silent'

    def check_startup_status(self):
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "MusicSchedulerByStudent"
        expected_command = self.get_startup_command()

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, app_name)
            is_current = value.strip().casefold() == expected_command.strip().casefold()
            self.auto_start_var.set(is_current)

            if value and not is_current:
                self.status_label.configure(
                    text="检测到已失效的旧自启动路径，请重新开启",
                    text_color="orange"
                )
        except FileNotFoundError:
            self.auto_start_var.set(False)
        except Exception as e:
            self.auto_start_var.set(False)
            self.status_label.configure(text=f"读取自启动状态失败: {e}", text_color="red")

    def toggle_startup(self):
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "MusicSchedulerByStudent"
        
        if not self.auto_start_var.get():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, app_name)
                self.status_label.configure(text="已关闭开机自启", text_color="gray")
            except FileNotFoundError:
                pass
            except Exception as e:
                self.status_label.configure(text=f"关闭自启失败: {e}", text_color="red")
            return

        try:
            cmd = self.get_startup_command()
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
            self.status_label.configure(text="已开启开机自启", text_color="green")
                
        except Exception as e:
            self.auto_start_var.set(False)
            self.status_label.configure(text="设置自启失败", text_color="red")
            self.show_error_alert(
                "无法开启开机自启，可能被安全软件拦截。请检查权限或添加白名单。",
                title="设置失败",
                detail=str(e),
            )

    def load_music_files(self):
        self.music_files = []
        # 由于已经在开头强制切换了工作目录，这里直接写相对路径即可
        directories = MUSIC_DIRECTORIES
        
        # 清空列表
        for widget in self.music_list_scroll.winfo_children():
            destroy_widget_tree(widget)
            
        found_files = []
        allowed_extensions = ('.mp3', '.flac', '.wav', '.ogg', '.m4a', '.wma', '.aac')
        
        for folder in directories:
            # os.path.abspath 会基于当前工作目录（即exe所在目录）生成路径
            abs_folder = os.path.abspath(folder)
            
            if not os.path.exists(abs_folder):
                try: os.makedirs(abs_folder)
                except: continue
                
            for file in os.listdir(abs_folder):
                if file.lower().endswith(allowed_extensions):
                    full_path = os.path.join(abs_folder, file)
                    found_files.append(full_path)
                    
                    row_frame = ctk.CTkFrame(self.music_list_scroll)
                    row_frame.pack(fill="x", padx=5, pady=2)
                    ctk.CTkLabel(row_frame, text=f"[{folder}] {file}").pack(side="left", padx=10)
                    ctk.CTkButton(row_frame, text="播放", width=50, height=24, 
                                command=lambda p=full_path: self.play_single_file_manually(p)).pack(side="right", padx=5, pady=5)
        
        self.music_files = found_files
        self.status_label.configure(text=f"刷新成功，找到 {len(found_files)} 个音频文件", text_color="green")

    # --- 新的添加任务流程 ---
    def focus_active_task_dialog(self):
        """激活当前任务向导，并阻止并行打开第二条创建或修改流程。"""
        dialog = self.active_task_dialog
        if dialog is None:
            return False
        try:
            if dialog.winfo_exists():
                dialog.focus_task_window()
                return True
        except Exception:
            pass
        self.active_task_dialog = None
        return False

    def initiate_add_task_flow(self):
        if not self._tasks_are_writable():
            self._warn_tasks_read_only()
            return
        if self.focus_active_task_dialog():
            return
        if not self.music_files:
            self.show_error_alert("没有扫描到音频文件\n请先点击左侧【刷新音乐列表】")
            return
        # 步骤 0: 设置时间与模式
        TimeModeDialog(
            self,
            callback=lambda config: self.start_task_wizard_draft(config)
        )

    def ensure_edit_task_available(self, edit_task):
        """确认修改目标仍存在，避免非模态向导提交到错误任务。"""
        if any(task is edit_task for task in self.tasks):
            return True

        message = "原任务已被删除，修改已取消"
        self.status_label.configure(text=message, text_color="orange")
        self.show_error_alert(message)
        return False

    def start_task_wizard_draft(self, config_data, edit_task=None):
        """创建只存在于内存的向导草稿，完成前不修改任务数据。"""
        if edit_task is None:
            draft = {
                "config": dict(config_data),
                "files": [],
                "weekdays": None,
                "name": None,
                "edit_task": None,
            }
        else:
            if not self.ensure_edit_task_available(edit_task):
                return
            draft = {
                "config": dict(config_data),
                "files": list(edit_task.get("files", [])),
                "weekdays": list(edit_task.get("weekdays", [])),
                "name": edit_task.get("name", ""),
                "edit_task": edit_task,
            }
        self.show_wizard_song_step(draft)

    def show_wizard_song_step(self, draft):
        initial_files = list(draft["files"]) if draft["files"] else None
        MultiSongSelectDialog(
            self,
            self.music_files,
            callback=lambda files: self.on_wizard_songs_next(draft, files),
            back_callback=lambda files: self.on_wizard_songs_back(draft, files),
            initial_selection=initial_files
        )

    def on_wizard_songs_back(self, draft, file_list):
        draft["files"] = list(file_list)
        TimeModeDialog(
            self,
            callback=lambda config: self.on_wizard_time_next(draft, config),
            initial_data=draft["config"]
        )

    def on_wizard_time_next(self, draft, config_data):
        draft["config"] = dict(config_data)
        self.show_wizard_song_step(draft)

    def on_wizard_songs_next(self, draft, file_list):
        draft["files"] = list(file_list)
        self.show_wizard_weekday_step(draft)

    def show_wizard_weekday_step(self, draft):
        initial_weekdays = draft["weekdays"]
        if initial_weekdays is not None:
            initial_weekdays = list(initial_weekdays)
        WeekdaySelectionDialog(
            self,
            draft["config"]["time"],
            len(draft["files"]),
            callback=lambda weekdays: self.on_wizard_weekdays_next(draft, weekdays),
            back_callback=lambda weekdays: self.on_wizard_weekdays_back(draft, weekdays),
            initial_selection=initial_weekdays
        )

    def on_wizard_weekdays_back(self, draft, weekdays):
        draft["weekdays"] = list(weekdays)
        self.show_wizard_song_step(draft)

    def on_wizard_weekdays_next(self, draft, weekdays):
        draft["weekdays"] = list(weekdays)
        self.show_wizard_name_step(draft)

    def show_wizard_name_step(self, draft):
        default_name = os.path.basename(draft["files"][0])
        if len(draft["files"]) > 1:
            default_name += f" 等{len(draft['files'])}首"
        TaskRenameDialog(
            self,
            default_name,
            callback=lambda name: self.finish_task_wizard(draft, name),
            back_callback=lambda name: self.on_wizard_name_back(draft, name),
            initial_name=draft["name"]
        )

    def on_wizard_name_back(self, draft, draft_name):
        draft["name"] = draft_name
        self.show_wizard_weekday_step(draft)

    def finish_task_wizard(self, draft, display_name):
        draft["name"] = display_name
        if draft["edit_task"] is None:
            self.finalize_add_task(
                draft["config"],
                draft["files"],
                draft["name"],
                draft["weekdays"]
            )
        else:
            self.finalize_modify(
                draft["edit_task"],
                draft["config"],
                draft["files"],
                draft["weekdays"],
                draft["name"]
            )

    def finalize_add_task(self, config, f_list, display_name, weekdays_indices):
        if not self._tasks_are_writable():
            self._warn_tasks_read_only()
            return
        previous_tasks = copy.deepcopy(self.tasks)
        self.tasks.append({
            "time": config['time'], 
            "mode": config['mode'],
            "end_time": config['end_time'],
            "end_next_day": bool(config.get('end_next_day', False)),
            "files": [make_portable_music_path(path) for path in f_list],
            "name": display_name,
            "weekdays": weekdays_indices,
            "enabled": True  # 默认开启
        })
        if not self._save_tasks_or_restore(previous_tasks):
            return
        self.refresh_task_list()
        self.status_label.configure(text=f"成功添加任务: {display_name}", text_color="green")

    # --- 新的修改任务流程 ---
    def start_modify_task(self, index):
        if not self._tasks_are_writable():
            self._warn_tasks_read_only()
            return
        if self.focus_active_task_dialog():
            return
        if not (0 <= index < len(self.tasks)): return
        
        task = self.tasks[index]
        self.status_label.configure(text=f"正在修改: {task['name']}", text_color="blue")
        
        # 步骤 0: 修改时间与模式
        initial_config = {
            "time": task['time'],
            "mode": task.get('mode', 'song'),
            "end_time": task.get('end_time', ''),
            "end_next_day": bool(task.get('end_next_day', False)),
        }
        
        TimeModeDialog(self, 
                       callback=lambda cfg, edit_task=task: self.start_task_wizard_draft(cfg, edit_task),
                       initial_data=initial_config)

    def finalize_modify(self, edit_task, config, f_list, weekdays_indices, display_name):
        if not self._tasks_are_writable():
            self._warn_tasks_read_only()
            return
        if not self.ensure_edit_task_available(edit_task):
            return

        previous_tasks = copy.deepcopy(self.tasks)
        edit_task['time'] = config['time']
        edit_task['mode'] = config['mode']
        edit_task['end_time'] = config['end_time']
        edit_task['end_next_day'] = bool(config.get('end_next_day', False))
        edit_task['files'] = [make_portable_music_path(path) for path in f_list]
        edit_task['weekdays'] = weekdays_indices
        edit_task['name'] = display_name
        # 保持原有的启用/禁用状态，如果没有则默认为 True
        edit_task['enabled'] = edit_task.get('enabled', True)
        
        if not self._save_tasks_or_restore(previous_tasks):
            return
        self.refresh_task_list()
        self.status_label.configure(text=f"任务修改成功: {display_name}", text_color="green")

    # --- 任务启用/禁用逻辑 ---
    def toggle_task_enabled(self, index, switch_var):
        if not self._tasks_are_writable():
            self._warn_tasks_read_only()
            self.refresh_task_list()
            return
        if 0 <= index < len(self.tasks):
            previous_tasks = copy.deepcopy(self.tasks)
            new_state = bool(switch_var.get())
            self.tasks[index]["enabled"] = new_state
            if not self._save_tasks_or_restore(previous_tasks):
                return
            state_str = "启用" if new_state else "禁用"
            self.status_label.configure(text=f"已{state_str}任务: {self.tasks[index]['name']}", text_color="#1F6AA5")

    def refresh_task_list(self):
        for widget in self.schedule_scroll.winfo_children():
            destroy_widget_tree(widget)
        self.tasks.sort(
            key=lambda task: task.get("time", "") if isinstance(task, dict) else ""
        )
        
        week_map = ["一", "二", "三", "四", "五", "六", "日"]
        controls_state = (
            "normal"
            if getattr(self, "_tasks_load_state", "ready") == "ready"
            else "disabled"
        )

        for idx, task in enumerate(self.tasks):
            if not isinstance(task, dict):
                continue
            time_value = task.get("time")
            if not MusicSchedulerApp._is_valid_clock(time_value):
                continue
            f = ctk.CTkFrame(self.schedule_scroll)
            f.pack(fill="x", padx=5, pady=2)
            
            raw_days_idx = task.get("weekdays", [])
            days_idx = []
            if isinstance(raw_days_idx, list):
                days_idx = [
                    day for day in raw_days_idx
                    if isinstance(day, int)
                    and not isinstance(day, bool)
                    and 0 <= day < len(week_map)
                ]
            if len(days_idx) == 7: days_str = "每天"
            elif len(days_idx) == 0: days_str = "关"
            else:
                d_names = [week_map[i] for i in days_idx]
                days_str = f"周{','.join(d_names)}"
            
            # 显示时间段 (如果是模式二)
            mode = task.get("mode", "song")
            time_display = format_clock_for_display(time_value)
            if mode == "duration":
                next_day_prefix = "次日 " if task.get("end_next_day", False) else ""
                end_display = format_clock_for_display(task.get("end_time", "?"))
                time_display += f" - {next_day_prefix}{end_display}"
                mode_str = "[时长]"
            else:
                mode_str = "[曲目]"

            task_name = task.get("name")
            if not isinstance(task_name, str) or not task_name.strip():
                task_name = "未命名任务"
            task_files = task.get("files", [])
            if not isinstance(task_files, list):
                task_files = []
            info_text = f"{time_display} {mode_str}\n{task_name} ({len(task_files)}首) {days_str}"
            
            # 任务信息
            lbl = ctk.CTkLabel(f, text=info_text, anchor="w", justify="left")
            lbl.pack(side="left", padx=10, pady=5)
            
            # 右侧按钮区域
            
            # 1. 删除按钮
            del_btn = ctk.CTkButton(f, text="删除", width=50, fg_color="#CC0000", hover_color="#AA0000", text_color="white",
                                  command=lambda i=idx: self.delete_task(i), state=controls_state)
            del_btn.pack(side="right", padx=5, pady=5)
            
            # 2. 修改按钮
            edit_btn = ctk.CTkButton(f, text="修改", width=50, fg_color="#1F6AA5", hover_color="#144d7a",
                                   command=lambda i=idx: self.start_modify_task(i), state=controls_state)
            edit_btn.pack(side="right", padx=5, pady=5)

            # 3. 启用/禁用开关
            is_enabled = task.get("enabled", True)
            switch_var = ctk.IntVar(value=1 if is_enabled else 0)
            
            cmd_toggle = lambda i=idx, v=switch_var: self.toggle_task_enabled(i, v)
            
            enable_switch = ctk.CTkSwitch(f, text="开启", variable=switch_var, command=cmd_toggle, 
                                          width=60, onvalue=1, offvalue=0, state=controls_state)
            enable_switch.pack(side="right", padx=(5, 10), pady=5)
        
        self.update_top_status()

    def delete_task(self, index):
        if not self._tasks_are_writable():
            self._warn_tasks_read_only()
            return
        if 0 <= index < len(self.tasks):
            previous_tasks = copy.deepcopy(self.tasks)
            deleted_task = self.tasks.pop(index)
            if not self._save_tasks_or_restore(previous_tasks):
                return
            self.refresh_task_list()
            self.status_label.configure(
                text=f"已删除任务: {deleted_task.get('name', '未命名任务')}",
                text_color="green"
            )

    def start_playlist(self, task):
        self.playlist_queue = list(task.get("files", []))
        self.current_track_index = 0
        self.is_playlist_active = True
        self.current_task_name = task["name"]
        self.current_task_mode = task.get("mode", "song")
        self.current_task_end_at = calculate_task_end_at(task, datetime.now())
        
        self.play_next_in_queue()

    def play_next_in_queue(self):
        total_files = len(self.playlist_queue)
        if total_files == 0:
            self.stop_music()
            self.status_label.configure(text="任务停止：播放列表为空", text_color="red")
            return

        attempted = 0
        last_error = ""

        while attempted < total_files:
            if self.current_track_index >= total_files:
                if self.current_task_mode == "duration":
                    self.current_track_index = 0
                else:
                    self.stop_music()
                    self.status_label.configure(text="任务播放完毕", text_color="green")
                    return

            path = self.playlist_queue[self.current_track_index]
            self.current_track_index += 1
            attempted += 1

            success, msg = self.play_music_file(path)
            if success:
                self.update_top_status()
                self.status_label.configure(
                    text=f"正在播放: {os.path.basename(path)}",
                    text_color="#1F6AA5"
                )
                return

            last_error = msg

        self.stop_music()
        error_suffix = f"（{last_error}）" if last_error else ""
        self.status_label.configure(
            text=f"任务停止：所有歌曲均无法播放{error_suffix}",
            text_color="red"
        )

    def get_next_run(self, task, now):
        """计算任务在未来 7 天内的下一次实际运行时间。"""
        weekdays = {
            day for day in task.get("weekdays", [])
            if isinstance(day, int) and 0 <= day <= 6
        }
        if not weekdays:
            return None

        try:
            task_time = datetime.strptime(task["time"], "%H:%M:%S").time()
        except (KeyError, TypeError, ValueError):
            return None

        for day_offset in range(8):
            candidate_date = now.date() + timedelta(days=day_offset)
            if candidate_date.weekday() not in weekdays:
                continue

            candidate = datetime.combine(candidate_date, task_time)
            if candidate > now:
                return candidate

        return None

    def update_top_status(self):
        if self.is_playlist_active:
            mode_desc = "循环中" if self.current_task_mode == "duration" else "顺序播放"
            display_text = f"{self.current_task_name} - {mode_desc}"
            self.next_task_label.configure(text=display_text, text_color="#1F6AA5")
        else:
            now = datetime.now()
            candidates = []
            for task in self.tasks:
                if not task.get("enabled", True):
                    continue
                next_run = self.get_next_run(task, now)
                if next_run is not None:
                    candidates.append((next_run, task))

            if candidates:
                next_run, next_task = min(candidates, key=lambda item: item[0])
                day_offset = (next_run.date() - now.date()).days
                if day_offset == 0:
                    day_text = "今天"
                elif day_offset == 1:
                    day_text = "明天"
                else:
                    week_names = ["一", "二", "三", "四", "五", "六", "日"]
                    day_text = f"周{week_names[next_run.weekday()]}"

                display_text = (
                    f"下次: {day_text} {format_clock_for_display(next_run.strftime('%H:%M:%S'))} "
                    f"{next_task.get('name', '未命名任务')}"
                )
                self.next_task_label.configure(text=display_text, text_color="gray")
            elif self.tasks:
                self.next_task_label.configure(text="下次播放: 全部暂停或未设置日期", text_color="gray")
            else:
                self.next_task_label.configure(text="下次播放: 无任务", text_color="gray")

    def play_single_file_manually(self, path):
        self.is_playlist_active = False 
        self.current_task_name = ""
        success, msg = self.play_music_file(path)
        if success:
            self.status_label.configure(text=f"手动播放: {os.path.basename(path)}", text_color="#1F6AA5")
            self.update_top_status()
        else:
            self.show_error_alert(f"播放失败: {msg}")

    def play_music_file(self, path):
        path = resolve_music_path(path)
        if not os.path.exists(path): return False, "文件不存在"
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            file_obj = open(path, 'rb')
            pygame.mixer.music.load(file_obj)
            pygame.mixer.music.play()
            return True, ""
        except Exception as e:
            return False, str(e)

    def stop_music(self):
        self.is_playlist_active = False
        self.current_task_name = ""
        self.current_task_end_at = None
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass

        self.status_label.configure(text="播放已停止", text_color="gray")
        self.update_top_status()

    def check_schedule_tick(self):
        if not self.running:
            return

        try:
            now = datetime.now()
            current_time_str = now.strftime("%H:%M:%S")
            self.time_label.configure(text=current_time_str)

            # 1. 检查是否有任务需要开始
            previous_tick = self.last_tick_dt
            if previous_tick is not None and now < previous_tick:
                # 系统时钟回拨时不补播，避免旧任务重复触发。
                self.last_tick_dt = now
            else:
                if previous_tick is None:
                    interval_start = now.replace(microsecond=0) - timedelta(microseconds=1)
                else:
                    interval_start = previous_tick
                grace_start = now - timedelta(seconds=SCHEDULE_GRACE_SECONDS)
                scan_start = max(interval_start, grace_start)

                due_candidates = []
                candidate_date = scan_start.date()
                while candidate_date <= now.date():
                    for task_index, task in enumerate(list(self.tasks)):
                        if not isinstance(task, dict) or not task.get("enabled", True):
                            continue
                        time_value = task.get("time")
                        if not MusicSchedulerApp._is_valid_clock(time_value):
                            continue
                        weekdays = task.get("weekdays", [])
                        if (
                            not isinstance(weekdays, list)
                            or candidate_date.weekday() not in weekdays
                        ):
                            continue

                        task_time = datetime.strptime(time_value, "%H:%M:%S").time()
                        candidate = datetime.combine(candidate_date, task_time)
                        if (
                            interval_start < candidate <= now
                            and candidate >= grace_start
                        ):
                            due_candidates.append((candidate, task_index, task))
                    candidate_date += timedelta(days=1)

                # 先推进基线，即使播放启动失败也不在后续 tick 中反复尝试。
                self.last_tick_dt = now
                if due_candidates:
                    _, _, task_to_start = max(
                        due_candidates,
                        key=lambda item: (item[0], item[1]),
                    )
                    self.start_playlist(task_to_start)

            # 2. 检查当前播放是否需要处理
            if self.is_playlist_active:
                if self.current_task_mode == "duration" and self.current_task_end_at:
                    if now >= self.current_task_end_at:
                        self.stop_music()
                        self.status_label.configure(text="已达到设定结束时间", text_color="orange")

                if self.is_playlist_active and not pygame.mixer.music.get_busy():
                    self.play_next_in_queue()

            if not self.is_playlist_active:
                self.update_top_status()
        except Exception as e:
            self.status_label.configure(text=f"调度检查失败: {e}", text_color="red")
        finally:
            if self.running:
                self.after(500, self.check_schedule_tick)

if __name__ == "__main__":
    mutex_name = "Global_MusicScheduler_Instance_Lock"
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, mutex_name)
    last_error = kernel32.GetLastError()

    if last_error == 183: # ERROR_ALREADY_EXISTS
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "音乐定时播放系统 Pro")
        if hwnd:
            user32.ShowWindow(hwnd, 9) 
            user32.SetForegroundWindow(hwnd)
        sys.exit(0)

    app = MusicSchedulerApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
