import customtkinter as ctk
import os
import sys
import threading
import queue
import pygame
import winreg
import json
import functools
import ctypes
import time
import tkinter as tk
from tkinter import font as tkfont
from datetime import datetime, timedelta
from pathlib import Path
import pystray
from PIL import Image, ImageDraw

# ========================================================
# 核心修复：强制设置工作目录为 exe 所在目录
# ========================================================
def resource_path(relative_path):
    """ 获取资源的绝对路径，适配开发环境和打包后的 exe 环境 """
    try:
        # PyInstaller 创建临时文件夹，路径存储在 _MEIPASS 中
        base_path = sys._MEIPASS
    except Exception:
        # 正常运行模式，使用当前脚本所在的目录
        base_path = os.path.abspath(os.path.dirname(sys.argv[0]))
        # 如果是 exe 运行，sys.argv[0] 就是 exe 的路径，dirname 就是 exe 所在文件夹
    
    return os.path.join(base_path, relative_path)

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
MUSIC_DIRECTORIES = ("mp3", "changyong")


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


class StableDpiScalingController:
    """等待 Windows DPI 稳定后只执行一次 CustomTkinter 缩放重算。"""

    POLL_INTERVAL_MS = 80
    STABLE_DELAY_SECONDS = 0.25
    _pending_changes = {}

    @classmethod
    def install(cls):
        ctk.ScalingTracker.check_dpi_scaling = classmethod(cls._patched_check)

    @staticmethod
    def _patched_check(tracker):
        StableDpiScalingController.check(tracker)

    @classmethod
    def check(cls, tracker):
        now = time.monotonic()
        windows = list(tracker.window_widgets_dict.keys())

        for window in windows:
            try:
                if not window.winfo_exists() or window.state() in ("iconic", "withdrawn"):
                    cls._pending_changes.pop(window, None)
                    continue

                detected_scaling = tracker.get_window_dpi_scaling(window)
                applied_scaling = tracker.window_dpi_scaling_dict[window]
                if detected_scaling == applied_scaling:
                    cls._pending_changes.pop(window, None)
                    continue

                pending_change = cls._pending_changes.get(window)
                if pending_change is None or pending_change[0] != detected_scaling:
                    cls._pending_changes[window] = (detected_scaling, now)
                    continue

                if now - pending_change[1] < cls.STABLE_DELAY_SECONDS:
                    continue

                tracker.window_dpi_scaling_dict[window] = detected_scaling
                window.block_update_dimensions_event()
                try:
                    tracker.update_scaling_callbacks_for_window(window)
                except Exception as error:
                    tracker.window_dpi_scaling_dict[window] = applied_scaling
                    cls._pending_changes[window] = (detected_scaling, now)
                    print(f"DPI 缩放更新失败: {error}")
                else:
                    cls._pending_changes.pop(window, None)
                    window.event_generate("<<DpiScalingChanged>>", when="tail")
                finally:
                    window.unblock_update_dimensions_event()
            except Exception:
                cls._pending_changes.pop(window, None)

        active_windows = set(windows)
        for old_window in list(cls._pending_changes):
            if old_window not in active_windows:
                cls._pending_changes.pop(old_window, None)

        for app in windows:
            try:
                if app.winfo_exists():
                    app.after(cls.POLL_INTERVAL_MS, tracker.check_dpi_scaling)
                    return
            except Exception:
                continue

        tracker.update_loop_running = False

    @classmethod
    def forget_window(cls, window):
        cls._pending_changes.pop(window, None)


StableDpiScalingController.install()


class DpiStableCTk(ctk.CTk):
    """修正 CustomTkinter 5.2.2 在 DPI 更新期间未锁定尺寸事件的问题。"""

    def block_update_dimensions_event(self):
        self._block_update_dimensions_event = True

    def destroy(self):
        StableDpiScalingController.forget_window(self)
        super().destroy()


class DpiStableToplevel(ctk.CTkToplevel):
    """为所有子窗口提供稳定的 Windows DPI 尺寸更新。"""

    def block_update_dimensions_event(self):
        self._block_update_dimensions_event = True

    def destroy(self):
        StableDpiScalingController.forget_window(self)
        super().destroy()


class TaskFlowWindow(DpiStableToplevel):
    """不使用 grab 的单实例任务向导，并同步父子窗口的最小化状态。"""

    def __init__(self, parent, defer_show=False):
        super().__init__(parent)
        self.parent_window = parent
        self._destroying = False
        self._group_minimized = False
        self._initial_show_pending = defer_show
        if defer_show:
            self.withdraw()
        self._parent_map_bind_id = parent.bind("<Map>", self._on_parent_map, add="+")
        self._parent_unmap_bind_id = parent.bind("<Unmap>", self._on_parent_unmap, add="+")
        self.bind("<Map>", self._on_window_map, add="+")
        self.bind("<Unmap>", self._on_window_unmap, add="+")
        self.bind("<<DpiScalingChanged>>", self._on_dpi_scaling_changed, add="+")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.attributes("-topmost", True)
        parent.active_task_dialog = self
        if not defer_show:
            self.after_idle(self.focus_task_window)

    def show_when_ready(self, prepare_callback=None):
        """完成隐藏状态下的首轮布局后，再一次性显示任务窗口。"""
        if self._destroying or not self.winfo_exists():
            return
        if not self._initial_show_pending:
            self.focus_task_window()
            return

        self.update_idletasks()
        if prepare_callback is not None:
            prepare_callback()
        if self._destroying or not self.winfo_exists():
            return

        self._initial_show_pending = False
        self.deiconify()
        self.after_idle(self.focus_task_window)

    def focus_task_window(self):
        if self._destroying or self._initial_show_pending or not self.winfo_exists():
            return
        if self.parent_window.state() == "iconic":
            self.parent_window.deiconify()
        if self.state() == "iconic":
            self.deiconify()
        self.attributes("-topmost", True)
        self.lift()
        self.focus_force()

    def _on_window_unmap(self, event):
        if event.widget is self and not self._destroying:
            self.after_idle(self._sync_minimize_from_window)

    def _sync_minimize_from_window(self):
        if self._destroying or not self.winfo_exists() or self.state() != "iconic":
            return
        self._group_minimized = True
        self.attributes("-topmost", False)
        if self.parent_window.winfo_exists() and self.parent_window.state() != "iconic":
            self.parent_window.iconify()

    def _on_parent_unmap(self, event):
        if event.widget is self.parent_window and not self._destroying:
            self.after_idle(self._sync_minimize_from_parent)

    def _sync_minimize_from_parent(self):
        if self._destroying or not self.parent_window.winfo_exists():
            return
        if self.parent_window.state() != "iconic":
            return
        self._group_minimized = True
        self.attributes("-topmost", False)
        if self.winfo_exists() and self.state() != "iconic":
            self.iconify()

    def _on_window_map(self, event):
        if event.widget is not self or not self._group_minimized or self._destroying:
            return
        if self.parent_window.winfo_exists() and self.parent_window.state() == "iconic":
            self.parent_window.deiconify()

    def _on_parent_map(self, event):
        if event.widget is self.parent_window and self._group_minimized and not self._destroying:
            self.after_idle(self._restore_window_group)

    def _restore_window_group(self):
        if self._destroying or not self.winfo_exists():
            return
        self.deiconify()
        self._group_minimized = False
        self.focus_task_window()

    def _on_dpi_scaling_changed(self, _event=None):
        pass

    def destroy(self):
        if self._destroying:
            return
        self._destroying = True

        if getattr(self.parent_window, "active_task_dialog", None) is self:
            self.parent_window.active_task_dialog = None
        for sequence, bind_id in (
            ("<Map>", self._parent_map_bind_id),
            ("<Unmap>", self._parent_unmap_bind_id),
        ):
            try:
                self.parent_window.unbind(sequence, bind_id)
            except Exception:
                pass
        super().destroy()


class SongRowCanvas(tk.Canvas):
    """以单个 Canvas 绘制歌名和操作区，避免多层控件在缩放时反复重排。"""

    INITIAL_DELAY_MS = 350
    FRAME_INTERVAL_SECONDS = 1 / 60
    SPEED_PIXELS_PER_SECOND = 45.0
    TEXT_GAP = "     •     "

    def __init__(
        self,
        master,
        full_text,
        text_font,
        control_font,
        background,
        scale=1.0,
        index_text=None,
        actions=None,
        layout_scheduler=None,
    ):
        self.full_text = full_text
        self.text_font = text_font
        self.control_font = control_font
        self.background = background
        self.index_text = index_text
        self.actions = [dict(action) for action in (actions or []) if action]
        self._layout_scheduler = layout_scheduler
        self._scale = max(0.5, float(scale))
        self._destroying = False
        self._animation_job = None
        self._text_hovered = False
        self._hovered_action = None
        self._cursor_name = ""
        self._marquee_active = False
        self._has_overflow = False
        self._offset_pixels = 0.0
        self._cycle_width = 0
        self._last_frame_time = None
        self._next_frame_time = None
        self._text_left = 0
        self._text_right = 0
        self._action_hitboxes = {}
        self._ellipsis_cache = {}
        self._full_text_width = 0
        self._prefix_widths = []
        self._ellipsis_widths = {}
        self._rebuild_measurements()

        super().__init__(
            master,
            height=self._px(32),
            background=self.background,
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
        )

        self._text_item = self.create_text(
            0,
            0,
            text="",
            anchor="w",
            fill="#202020",
            font=self.text_font,
        )
        self._text_copy_item = self.create_text(
            0,
            0,
            text="",
            anchor="w",
            fill="#202020",
            font=self.text_font,
            state="hidden",
        )
        self._left_mask_item = self.create_rectangle(
            0, 0, 0, 0, fill=self.background, outline=self.background
        )
        self._right_mask_item = self.create_rectangle(
            0, 0, 0, 0, fill=self.background, outline=self.background
        )
        self._index_item = None
        if self.index_text is not None:
            self._index_item = self.create_text(
                0,
                0,
                text=self.index_text,
                anchor="w",
                fill="#858585",
                font=self.text_font,
            )

        for action in self.actions:
            action["shape_item"] = self.create_polygon(
                0, 0, 1, 0, 1, 1, 0, 1,
                smooth=True,
                splinesteps=12,
                fill=action["fill"],
                outline="",
            )
            action["text_item"] = self.create_text(
                0,
                0,
                text=action["text"],
                fill=action["text_color"],
                font=self.control_font,
            )

        self.bind("<Configure>", self._on_configure)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _px(self, logical_value):
        return max(1, round(logical_value * self._scale))

    def _on_configure(self, _event=None):
        if not self._destroying and self._layout_scheduler is not None:
            self._layout_scheduler(self)

    @staticmethod
    def _rounded_points(x1, y1, x2, y2, radius):
        radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
        return (
            x1 + radius, y1,
            x2 - radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1 + radius,
            x1, y1,
        )

    def set_scale(self, scale):
        self._scale = max(0.5, float(scale))
        self.configure(height=self._px(32))
        try:
            self.pack_configure(pady=self._px(2))
        except Exception:
            pass
        self._ellipsis_cache.clear()
        self._rebuild_measurements()

    def _rebuild_measurements(self):
        """预先测量字符宽度，连续缩放时不再跨 Tcl 重复测量每个候选前缀。"""
        cumulative_width = 0
        self._prefix_widths = [0]
        for character in self.full_text:
            cumulative_width += self.text_font.measure(character)
            self._prefix_widths.append(cumulative_width)
        self._full_text_width = self.text_font.measure(self.full_text)
        self._ellipsis_widths = {
            "...": self.text_font.measure("..."),
            "…": self.text_font.measure("…"),
        }

    def _refresh_layout(self):
        if self._destroying or not self.winfo_exists():
            return

        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        center_y = height / 2
        if self.index_text is None:
            self._text_left = self._px(5)
            control_width = self._px(40)
        else:
            self._text_left = self._px(36)
            control_width = self._px(96)
        self._text_right = max(self._text_left, width - control_width - self._px(3))

        self.coords(self._left_mask_item, 0, 0, self._text_left, height)
        self.coords(self._right_mask_item, self._text_right, 0, width, height)
        if self._index_item is not None:
            self.coords(self._index_item, self._px(5), center_y)

        self._action_hitboxes.clear()
        if self.index_text is None:
            button_width = self._px(30)
            button_height = self._px(24)
            left = width - self._px(5) - button_width
            action_positions = {0: left}
        else:
            button_width = self._px(25)
            button_height = self._px(20)
            gap = self._px(5)
            left = width - self._px(5) - button_width * 3 - gap * 2
            action_positions = {
                0: left,
                1: left + button_width + gap,
                2: left + (button_width + gap) * 2,
            }

        top = (height - button_height) / 2
        bottom = top + button_height
        for action in self.actions:
            action_left = action_positions[action["slot"]]
            action_right = action_left + button_width
            self._action_hitboxes[action["key"]] = (
                action_left,
                top,
                action_right,
                bottom,
            )
            self.coords(
                action["shape_item"],
                *self._rounded_points(
                    action_left,
                    top,
                    action_right,
                    bottom,
                    self._px(6),
                ),
            )
            self.coords(
                action["text_item"],
                (action_left + action_right) / 2,
                center_y,
            )

        self._refresh_text()

    def _fit_text(self, available_width):
        available_width = max(0, int(available_width))
        cached = self._ellipsis_cache.get(available_width)
        if cached is not None:
            return cached
        if self._full_text_width <= available_width:
            result = self.full_text
        else:
            suffix = "..."
            if self._ellipsis_widths[suffix] > available_width:
                suffix = "…"
            suffix_width = self._ellipsis_widths[suffix]
            if suffix_width > available_width:
                result = ""
            else:
                low = 0
                high = len(self._prefix_widths) - 1
                prefix_limit = available_width - suffix_width
                while low < high:
                    middle = (low + high + 1) // 2
                    if self._prefix_widths[middle] <= prefix_limit:
                        low = middle
                    else:
                        high = middle - 1
                result = self.full_text[:low] + suffix
        self._ellipsis_cache[available_width] = result
        return result

    def _refresh_text(self):
        available_width = max(0, self._text_right - self._text_left)
        self._has_overflow = self._full_text_width > available_width
        center_y = max(1, self.winfo_height()) / 2

        if not self._has_overflow:
            self._cancel_animation()
            self.itemconfigure(self._text_item, text=self.full_text)
            self.itemconfigure(self._text_copy_item, state="hidden")
            self.coords(self._text_item, self._text_left, center_y)
            return

        if self._marquee_active:
            cycle_text = self.full_text + self.TEXT_GAP
            self._cycle_width = self.text_font.measure(cycle_text)
            self.itemconfigure(self._text_item, text=cycle_text)
            self.itemconfigure(self._text_copy_item, text=cycle_text, state="normal")
            self._position_marquee_text(center_y)
        else:
            self.itemconfigure(self._text_item, text=self._fit_text(available_width))
            self.itemconfigure(self._text_copy_item, state="hidden")
            self.coords(self._text_item, self._text_left, center_y)

    def _position_marquee_text(self, center_y=None):
        if center_y is None:
            center_y = max(1, self.winfo_height()) / 2
        start_x = self._text_left - round(self._offset_pixels)
        self.coords(self._text_item, start_x, center_y)
        self.coords(self._text_copy_item, start_x + self._cycle_width, center_y)

    def _set_text_hovered(self, hovered):
        if self._text_hovered == hovered:
            return
        self._text_hovered = hovered
        if hovered and self._has_overflow:
            self._animation_job = self.after(self.INITIAL_DELAY_MS, self._start_animation)
        elif not hovered:
            self._cancel_animation()
            self._refresh_text()

    def _action_at(self, x, y):
        for key, (x1, y1, x2, y2) in self._action_hitboxes.items():
            if x1 <= x < x2 and y1 <= y < y2:
                return key
        return None

    def _on_motion(self, event):
        action_key = self._action_at(event.x, event.y)
        if action_key != self._hovered_action:
            self._hovered_action = action_key
            for action in self.actions:
                fill = action["hover_fill"] if action["key"] == action_key else action["fill"]
                self.itemconfigure(action["shape_item"], fill=fill)

        cursor_name = "hand2" if action_key is not None else ""
        if cursor_name != self._cursor_name:
            self._cursor_name = cursor_name
            self.configure(cursor=cursor_name)

        self._set_text_hovered(
            self._text_left <= event.x < self._text_right and 0 <= event.y < self.winfo_height()
        )

    def _on_leave(self, _event=None):
        self._set_text_hovered(False)
        self._hovered_action = None
        for action in self.actions:
            self.itemconfigure(action["shape_item"], fill=action["fill"])
        if self._cursor_name:
            self._cursor_name = ""
            self.configure(cursor="")

    def _on_click(self, event):
        action_key = self._action_at(event.x, event.y)
        if action_key is None:
            return
        for action in self.actions:
            if action["key"] == action_key:
                action["command"]()
                return

    def _start_animation(self):
        self._animation_job = None
        if self._destroying or not self._text_hovered or not self._has_overflow:
            return
        self._marquee_active = True
        self._offset_pixels = 0.0
        self._last_frame_time = time.perf_counter()
        self._next_frame_time = self._last_frame_time + self.FRAME_INTERVAL_SECONDS
        self._refresh_text()
        self._schedule_next_frame()

    def _schedule_next_frame(self):
        if self._next_frame_time is None:
            return
        delay_ms = max(1, round((self._next_frame_time - time.perf_counter()) * 1000))
        self._animation_job = self.after(delay_ms, self._animate_frame)

    def _animate_frame(self):
        self._animation_job = None
        if self._destroying or not self._text_hovered or not self._has_overflow:
            self._cancel_animation()
            return

        now = time.perf_counter()
        elapsed = min(now - self._last_frame_time, 0.05) if self._last_frame_time else 0
        self._last_frame_time = now
        if self._cycle_width > 0:
            self._offset_pixels = (
                self._offset_pixels + self.SPEED_PIXELS_PER_SECOND * elapsed
            ) % self._cycle_width
        self._position_marquee_text()

        self._next_frame_time += self.FRAME_INTERVAL_SECONDS
        if self._next_frame_time <= now:
            missed_frames = int(
                (now - self._next_frame_time) / self.FRAME_INTERVAL_SECONDS
            ) + 1
            self._next_frame_time += missed_frames * self.FRAME_INTERVAL_SECONDS
        self._schedule_next_frame()

    def _cancel_animation(self):
        if self._animation_job is not None:
            try:
                self.after_cancel(self._animation_job)
            except Exception:
                pass
            self._animation_job = None
        self._marquee_active = False
        self._offset_pixels = 0.0
        self._last_frame_time = None
        self._next_frame_time = None

    def destroy(self):
        if self._destroying:
            return
        self._destroying = True
        self._cancel_animation()
        super().destroy()


def destroy_widget_tree(widget):
    """逐层销毁动态控件，确保 CustomTkinter 注销每个 DPI 回调。"""
    for child in widget.winfo_children():
        destroy_widget_tree(child)
    widget.destroy()

# ========================================================
# 1. 弹窗类：步骤 3/3 任务命名
# ========================================================
class TaskRenameDialog(TaskFlowWindow):
    def __init__(self, parent, default_name, callback, back_callback, initial_name=None):
        super().__init__(parent)
        self.callback = callback
        self.back_callback = back_callback
        self.title("步骤 3/3: 任务命名")
        self.geometry("350x250")
        self.minsize(350, 250)
        self.resizable(False, False)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, minsize=72)

        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(content_frame, text="为这个任务起个名字", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#1F6AA5").pack(pady=(25, 15))

        self.name_entry = ctk.CTkEntry(content_frame, width=250, height=35, font=ctk.CTkFont(size=14))
        if initial_name is None:
            clean_name = os.path.splitext(default_name)[0]
            if len(clean_name) > 20:
                clean_name = clean_name[:20] + "..."
        else:
            clean_name = initial_name
        self.name_entry.insert(0, clean_name)
        self.name_entry.pack(pady=10)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, sticky="nsew", padx=40, pady=(6, 18))
        btn_frame.grid_columnconfigure(1, weight=1)
        btn_frame.grid_rowconfigure(0, weight=1)

        self.btn_back = ctk.CTkButton(
            btn_frame,
            text="上一步",
            command=self.on_back,
            width=110,
            height=42,
            corner_radius=21,
            fg_color="transparent",
            border_width=2,
            text_color="#1F6AA5",
            font=ctk.CTkFont(size=15, weight="bold")
        )
        self.btn_back.grid(row=0, column=0, sticky="w")

        self.btn_confirm = ctk.CTkButton(btn_frame, text="完成", command=self.on_confirm,
                                       width=120, height=42, corner_radius=21,
                                       font=ctk.CTkFont(size=15, weight="bold"))
        self.btn_confirm.grid(row=0, column=2, sticky="e")
        
        self.name_entry.bind("<Return>", lambda event: self.on_confirm())
        self.name_entry.focus_set()

    def on_confirm(self):
        new_name = self.name_entry.get().strip()
        if not new_name:
            new_name = "未命名任务"
        callback = self.callback
        self.destroy()
        callback(new_name)

    def on_back(self):
        draft_name = self.name_entry.get()
        callback = self.back_callback
        self.destroy()
        callback(draft_name)

# ========================================================
# 2. 弹窗类：步骤 2/3 选择星期
# ========================================================
class WeekdaySelectionDialog(TaskFlowWindow):
    def __init__(self, parent, time_str, song_count, callback, back_callback, initial_selection=None):
        super().__init__(parent)
        self.callback = callback
        self.back_callback = back_callback
        self.title("步骤 2/3: 选择播放日期")
        self.geometry("400x580")
        self.minsize(400, 580)
        self.resizable(False, False)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, minsize=78)

        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(content_frame, text=f"任务时间: {time_str}", font=ctk.CTkFont(size=22, weight="bold"), text_color="#1F6AA5").pack(pady=(25, 5))
        ctk.CTkLabel(content_frame, text=f"包含歌曲数量: {song_count} 首", text_color="gray", font=ctk.CTkFont(size=12)).pack(pady=(0, 14))

        ctk.CTkFrame(content_frame, height=2, fg_color="#E0E0E0").pack(fill="x", padx=30, pady=5)

        ctk.CTkLabel(content_frame, text="请勾选需要播放的星期:", font=ctk.CTkFont(size=15, weight="bold")).pack(pady=12)

        self.checkboxes = []
        days = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        
        self.check_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        self.check_frame.pack(pady=5)

        for i, day in enumerate(days):
            is_checked = True
            if initial_selection is not None:
                is_checked = (i in initial_selection)
            
            var = ctk.BooleanVar(value=is_checked)
            cb = ctk.CTkCheckBox(self.check_frame, text=day, variable=var, font=ctk.CTkFont(size=14), 
                                 checkbox_width=24, checkbox_height=24, border_width=2)
            cb.grid(row=i, column=0, pady=8, padx=40, sticky="w")
            self.checkboxes.append(var)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, sticky="nsew", padx=30, pady=(6, 18))
        btn_frame.grid_columnconfigure(1, weight=1)
        btn_frame.grid_rowconfigure(0, weight=1)

        self.btn_back = ctk.CTkButton(
            btn_frame,
            text="上一步",
            command=self.on_back,
            width=100,
            height=44,
            corner_radius=22,
            fg_color="transparent",
            border_width=2,
            border_color="#1F6AA5",
            text_color="#1F6AA5",
            font=ctk.CTkFont(size=15, weight="bold")
        )
        self.btn_back.grid(row=0, column=0, sticky="w")

        self.btn_cancel = ctk.CTkButton(btn_frame, text="取消", command=self.destroy,
                                      height=44, corner_radius=22, width=78,
                                      fg_color="transparent", border_width=2, border_color="gray", text_color="gray",
                                      hover_color="#EEEEEE")
        self.btn_cancel.grid(row=0, column=2, padx=(12, 10), sticky="e")

        self.btn_confirm = ctk.CTkButton(btn_frame, text="下一步", command=self.on_confirm, 
                                       width=110, height=44, corner_radius=22,
                                       font=ctk.CTkFont(size=16, weight="bold"),
                                       fg_color="#1F6AA5", hover_color="#144d7a")
        self.btn_confirm.grid(row=0, column=3, sticky="e")

    def on_confirm(self):
        selected_indices = [i for i, var in enumerate(self.checkboxes) if var.get()]
        callback = self.callback
        self.destroy()
        callback(selected_indices)

    def on_back(self):
        selected_indices = [i for i, var in enumerate(self.checkboxes) if var.get()]
        callback = self.back_callback
        self.destroy()
        callback(selected_indices)

# ========================================================
# 3. 弹窗类：步骤 1/3 选择歌曲
# ========================================================
class MultiSongSelectDialog(TaskFlowWindow):
    def __init__(self, parent, all_music_files, callback, back_callback, initial_selection=None):
        super().__init__(parent, defer_show=True)
        self.all_music_files = all_music_files
        self.callback = callback
        self.back_callback = back_callback
        self.selected_files = list(initial_selection) if initial_selection else []
        self.library_song_rows = []
        self.playlist_song_rows = []
        self._row_refresh_job = None
        self._dirty_song_rows = set()
        self._refresh_all_song_rows = False
        self._canvas_scale = self._get_canvas_scale()
        font_theme = ctk.ThemeManager.theme["CTkFont"]
        font_size = max(1, round(font_theme["size"] * self._canvas_scale))
        self.song_canvas_font = tkfont.Font(
            family=font_theme["family"],
            size=-font_size,
            weight=font_theme["weight"],
        )
        self.control_canvas_font = tkfont.Font(
            family=font_theme["family"],
            size=-font_size,
            weight="normal",
        )

        self.title("步骤 1/3: 选择歌曲并排序")
        self.geometry("700x500")

        ctk.CTkLabel(self, text="请从左侧添加歌曲，在右侧调整播放顺序", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)

        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.pack(expand=True, fill="both", padx=10, pady=(0, 10))

        left_frame = ctk.CTkFrame(content_frame)
        left_frame.pack(side="left", fill="both", expand=True, padx=5)
        ctk.CTkLabel(left_frame, text="音乐库 (点击添加)", text_color="gray").pack(pady=5)
        
        self.scroll_library = ctk.CTkScrollableFrame(left_frame)
        self.scroll_library.pack(fill="both", expand=True, padx=5, pady=5)
        self.library_row_background = self._resolve_appearance_color(
            self.scroll_library.cget("fg_color")
        )
        
        self.populate_library()

        right_frame = ctk.CTkFrame(content_frame)
        right_frame.pack(side="right", fill="both", expand=True, padx=5)
        ctk.CTkLabel(right_frame, text="播放顺序 (从上到下)", text_color="#1F6AA5", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        
        self.scroll_playlist = ctk.CTkScrollableFrame(right_frame)
        self.scroll_playlist.pack(fill="both", expand=True, padx=5, pady=5)
        self.playlist_row_background = self._resolve_appearance_color(
            self.scroll_playlist.cget("fg_color")
        )
        
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", pady=15, fill="x", padx=20)
        
        self.btn_back = ctk.CTkButton(
            btn_frame,
            text="上一步",
            command=self.on_back,
            width=100,
            height=40,
            corner_radius=20,
            fg_color="transparent",
            border_width=2,
            border_color="#1F6AA5",
            text_color="#1F6AA5",
            font=ctk.CTkFont(weight="bold")
        )
        self.btn_back.pack(side="left", padx=(10, 6))

        self.status_lbl = ctk.CTkLabel(btn_frame, text="已选: 0 首", text_color="gray")
        self.status_lbl.pack(side="left", padx=(4, 10))

        self.btn_next = ctk.CTkButton(btn_frame, text="下一步", command=self.on_next, width=120, height=40, font=ctk.CTkFont(weight="bold"))
        self.btn_next.pack(side="right", padx=10)
        self.btn_cancel = ctk.CTkButton(btn_frame, text="取消", command=self.destroy, fg_color="transparent", border_width=1, text_color="gray", width=80, height=40)
        self.btn_cancel.pack(side="right", padx=10)

        # 依赖的状态标签创建完成后再绘制初始播放列表。
        self.update_playlist_ui()
        self.after_idle(self._finish_initial_show)

    @staticmethod
    def _resolve_appearance_color(color_value):
        if isinstance(color_value, (tuple, list)):
            return color_value[ctk.AppearanceModeTracker.get_mode()]
        if color_value == "transparent":
            return "gray81"
        return color_value

    def _get_canvas_scale(self):
        try:
            return ctk.ScalingTracker.get_widget_scaling(self)
        except Exception:
            return 1.0

    def _finish_initial_show(self):
        if self._destroying or not self.winfo_exists():
            return
        self.show_when_ready(self._refresh_song_rows)

    def populate_library(self):
        for f_path in self.all_music_files:
            try:
                add_cmd = functools.partial(self.add_song, f_path)
                row = SongRowCanvas(
                    self.scroll_library,
                    full_text=os.path.basename(f_path),
                    text_font=self.song_canvas_font,
                    control_font=self.control_canvas_font,
                    background=self.library_row_background,
                    scale=self._canvas_scale,
                    layout_scheduler=self._schedule_row_refresh,
                    actions=[{
                        "key": "add",
                        "slot": 0,
                        "text": "+",
                        "fill": "#3B8ED0",
                        "hover_fill": "#36719F",
                        "text_color": "#FFFFFF",
                        "command": add_cmd,
                    }],
                )
                row.pack(fill="x", pady=max(1, round(2 * self._canvas_scale)))
                self.library_song_rows.append(row)
            except Exception:
                pass

    def add_song(self, path):
        try:
            self.selected_files.append(path)
            self.update_playlist_ui()
        except Exception:
            pass

    def remove_song(self, index):
        if 0 <= index < len(self.selected_files):
            del self.selected_files[index]
            self.update_playlist_ui()

    def move_up(self, index):
        if index > 0:
            self.selected_files[index], self.selected_files[index-1] = self.selected_files[index-1], self.selected_files[index]
            self.update_playlist_ui()

    def move_down(self, index):
        if index < len(self.selected_files) - 1:
            self.selected_files[index], self.selected_files[index+1] = self.selected_files[index+1], self.selected_files[index]
            self.update_playlist_ui()

    def update_playlist_ui(self):
        try:
            for row in self.playlist_song_rows:
                row.destroy()
            self.playlist_song_rows.clear()
            
            self.status_lbl.configure(text=f"已选: {len(self.selected_files)} 首")

            for idx, f_path in enumerate(self.selected_files):
                actions = []
                if idx > 0:
                    actions.append({
                        "key": "up",
                        "slot": 0,
                        "text": "↑",
                        "fill": "#DDDDDD",
                        "hover_fill": "#BBBBBB",
                        "text_color": "#202020",
                        "command": functools.partial(self.move_up, idx),
                    })
                if idx < len(self.selected_files) - 1:
                    actions.append({
                        "key": "down",
                        "slot": 1,
                        "text": "↓",
                        "fill": "#DDDDDD",
                        "hover_fill": "#BBBBBB",
                        "text_color": "#202020",
                        "command": functools.partial(self.move_down, idx),
                    })
                actions.append({
                    "key": "delete",
                    "slot": 2,
                    "text": "✕",
                    "fill": self.playlist_row_background,
                    "hover_fill": "#FFEEEE",
                    "text_color": "#D92323",
                    "command": functools.partial(self.remove_song, idx),
                })

                row = SongRowCanvas(
                    self.scroll_playlist,
                    full_text=os.path.basename(f_path),
                    text_font=self.song_canvas_font,
                    control_font=self.control_canvas_font,
                    background=self.playlist_row_background,
                    scale=self._canvas_scale,
                    index_text=f"{idx + 1}.",
                    actions=actions,
                    layout_scheduler=self._schedule_row_refresh,
                )
                row.pack(
                    fill="x",
                    padx=max(1, round(2 * self._canvas_scale)),
                    pady=max(1, round(2 * self._canvas_scale)),
                )
                self.playlist_song_rows.append(row)

            self._schedule_row_refresh()
        except Exception:
            pass

    def _schedule_row_refresh(self, row=None):
        if self._destroying:
            return
        if row is None:
            self._refresh_all_song_rows = True
            self._dirty_song_rows.clear()
        elif not self._refresh_all_song_rows:
            self._dirty_song_rows.add(row)
        if self._row_refresh_job is None:
            self._row_refresh_job = self.after_idle(self._refresh_song_rows)

    def _refresh_song_rows(self):
        self._row_refresh_job = None
        if self._refresh_all_song_rows:
            rows = self.library_song_rows + self.playlist_song_rows
        else:
            rows = list(self._dirty_song_rows)
        self._refresh_all_song_rows = False
        self._dirty_song_rows.clear()
        for row in rows:
            try:
                if row.winfo_exists():
                    row._refresh_layout()
            except Exception:
                continue

    def _on_dpi_scaling_changed(self, _event=None):
        self._canvas_scale = self._get_canvas_scale()
        font_theme = ctk.ThemeManager.theme["CTkFont"]
        font_size = max(1, round(font_theme["size"] * self._canvas_scale))
        self.song_canvas_font.configure(size=-font_size)
        self.control_canvas_font.configure(size=-font_size)
        for row in self.library_song_rows + self.playlist_song_rows:
            try:
                if row.winfo_exists():
                    row.set_scale(self._canvas_scale)
            except Exception:
                continue
        self._schedule_row_refresh()

    def destroy(self):
        if self._destroying:
            return

        if self._row_refresh_job is not None:
            try:
                self.after_cancel(self._row_refresh_job)
            except Exception:
                pass
            self._row_refresh_job = None
        self._dirty_song_rows.clear()
        self._refresh_all_song_rows = False
        for row in self.library_song_rows + self.playlist_song_rows:
            try:
                row.destroy()
            except Exception:
                pass
        self.library_song_rows.clear()
        self.playlist_song_rows.clear()

        super().destroy()

    def on_next(self):
        if not self.selected_files:
            return 
        callback = self.callback
        selected_files = list(self.selected_files)
        self.destroy()
        callback(selected_files)

    def on_back(self):
        callback = self.back_callback
        selected_files = list(self.selected_files)
        self.destroy()
        callback(selected_files)

# ========================================================
# NEW: 弹窗类：步骤 0/3 设置时间和模式
# ========================================================
class TimeModeDialog(TaskFlowWindow):
    def __init__(self, parent, callback, initial_data=None):
        super().__init__(parent)
        self.callback = callback
        self.title("任务设置: 时间与模式")
        self.resizable(False, False)

        # 默认值
        init_start = ""
        init_mode = "song" # 'song' or 'duration'
        init_end = ""
        
        if initial_data:
            init_start = initial_data.get("time", "")
            init_mode = initial_data.get("mode", "song")
            init_end = initial_data.get("end_time", "")

        # 1. 开始时间
        ctk.CTkLabel(self, text="1. 设置开始时间", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5), anchor="w", padx=30)
        self.start_entry = ctk.CTkEntry(self, width=200, placeholder_text="例如 1300 或 13:00")
        self.start_entry.insert(0, init_start)
        self.start_entry.pack(pady=5)

        # 2. 播放模式
        ctk.CTkLabel(self, text="2. 选择播放模式", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5), anchor="w", padx=30)
        
        self.mode_var = ctk.StringVar(value=init_mode)
        
        self.radio_song = ctk.CTkRadioButton(self, text="模式一：固定曲目 (播完即止)", variable=self.mode_var, value="song", command=self.toggle_mode)
        self.radio_song.pack(pady=5, anchor="w", padx=50)
        
        self.radio_duration = ctk.CTkRadioButton(self, text="模式二：固定时长 (循环/截断)", variable=self.mode_var, value="duration", command=self.toggle_mode)
        self.radio_duration.pack(pady=5, anchor="w", padx=50)

        # 3. 结束时间 (仅模式二) - 使用 Frame 包裹以便整体隐藏
        self.end_time_frame = ctk.CTkFrame(self, fg_color="transparent")
        # 初始不 pack，由 toggle_mode 决定
        
        ctk.CTkLabel(self.end_time_frame, text="结束时间:", text_color="#1F6AA5", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(50, 10))
        self.end_entry = ctk.CTkEntry(self.end_time_frame, width=120, placeholder_text="例如 13:40")
        self.end_entry.insert(0, init_end)
        self.end_entry.pack(side="left")

        # 底部按钮
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(side="bottom", pady=20)
        ctk.CTkButton(self.btn_frame, text="下一步", command=self.on_confirm, width=120).pack(side="left", padx=10)
        ctk.CTkButton(self.btn_frame, text="取消", command=self.destroy, fg_color="transparent", border_width=1, text_color="gray", width=80).pack(side="left", padx=10)

        # 初始化显示状态
        self.toggle_mode()

    def toggle_mode(self):
        if self.mode_var.get() == "duration":
            # 模式二：显示结束时间输入框，窗口变高
            self.end_time_frame.pack(pady=10, fill="x", before=self.btn_frame)
            self.geometry("400x420")
        else:
            # 模式一：隐藏结束时间输入框，窗口变矮
            self.end_time_frame.pack_forget()
            self.geometry("400x340")

    def parse_time(self, t_input):
        t_clean = t_input.strip().replace("：", ":").replace(" ", "")
        candidate = ""
        if ":" in t_clean:
            parts = t_clean.split(":")
            if len(parts) == 2: parts.append("00")
            if len(parts) == 3: candidate = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
        else:
            if len(t_clean) <= 2: candidate = f"{t_clean.zfill(2)}:00:00"
            elif len(t_clean) == 4: candidate = f"{t_clean[:2]}:{t_clean[2:]}:00"
            elif len(t_clean) == 6: candidate = f"{t_clean[:2]}:{t_clean[2:4]}:{t_clean[4:]}"
        
        if candidate:
            try:
                datetime.strptime(candidate, "%H:%M:%S")
                return candidate
            except ValueError:
                return None
        return None

    def show_alert(self, msg):
        top = DpiStableToplevel(self)
        top.geometry("250x150")
        top.attributes("-topmost", True)
        ctk.CTkLabel(top, text=msg, wraplength=220).pack(expand=True)
        ctk.CTkButton(top, text="OK", command=top.destroy).pack(pady=10)

    def on_confirm(self):
        start_t = self.parse_time(self.start_entry.get())
        if not start_t:
            self.show_alert("开始时间格式错误\n请输入如 1300 或 13:00")
            return
        
        mode = self.mode_var.get()
        end_t = ""
        
        if mode == "duration":
            end_t = self.parse_time(self.end_entry.get())
            if not end_t:
                self.show_alert("结束时间格式错误\n请输入如 1340 或 13:40")
                return
            if end_t <= start_t:
                self.show_alert("结束时间必须晚于开始时间")
                return
        else:
            # 模式一：强制清空结束时间，确保不保存脏数据
            end_t = ""

        callback = self.callback
        config_data = {
            "time": start_t,
            "mode": mode,
            "end_time": end_t
        }
        self.destroy()
        callback(config_data)

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
        self.music_files = []
        self.active_task_dialog = None
        
        # 播放状态变量
        self.playlist_queue = []      
        self.current_track_index = 0  
        self.is_playlist_active = False 
        self.current_task_name = "" 
        self.current_task_mode = "song" 
        self.current_task_end_time = "" 

        # 主循环与跨线程事件状态
        self.running = True
        self._closing = False
        self.last_trigger_date = None
        self.triggered_tasks = set()
        self.ui_event_queue = queue.Queue()
        
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
    def save_tasks(self):
        try:
            for task in self.tasks:
                task["files"] = [
                    make_portable_music_path(path)
                    for path in task.get("files", [])
                ]
            with open(TASKS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.tasks, f, ensure_ascii=False, indent=2)
            self.status_label.configure(text="设置已保存", text_color="green")
        except Exception as e:
            self.status_label.configure(text=f"保存失败: {e}", text_color="red")

    def load_tasks(self):
        # 由于已经在开头强制切换了工作目录，这里直接读取文件名即可
        if os.path.exists(TASKS_FILE):
            try:
                with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                    self.tasks = json.load(f)
                for task in self.tasks:
                    task["files"] = [
                        make_portable_music_path(path)
                        for path in task.get("files", [])
                    ]
                self.refresh_task_list()
                self.status_label.configure(text=f"已加载 {len(self.tasks)} 个任务", text_color="gray")
            except Exception as e:
                self.status_label.configure(text=f"读取任务失败: {e}", text_color="red")
        else:
            # 如果文件不存在，说明是新环境或没保存过
            self.status_label.configure(text="无历史任务记录", text_color="gray")

    def show_error_alert(self, msg):
        err_win = DpiStableToplevel(self)
        err_win.title("操作提示")
        err_win.geometry("320x180")
        err_win.resizable(False, False)
        err_win.attributes("-topmost", True)
        err_win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 160
        y = self.winfo_y() + (self.winfo_height() // 2) - 90
        if x < 0: x = 100
        if y < 0: y = 100
        err_win.geometry(f"+{x}+{y}")
        
        err_win.grab_set()

        ctk.CTkLabel(err_win, text="⚠️", font=ctk.CTkFont(size=40)).pack(pady=(15, 0))
        ctk.CTkLabel(err_win, text=msg, font=ctk.CTkFont(size=14), text_color="#CC0000", 
                     wraplength=280, justify="center").pack(expand=True, padx=10, pady=5)
        
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
        menu = (
            pystray.MenuItem('显示窗口', self.show_window_from_tray, default=True),
            pystray.MenuItem('退出程序', self.quit_app_from_tray)
        )
        icon_image = self.create_tray_image()
        self.tray_icon = pystray.Icon("MusicScheduler", icon_image, "定时播放器", menu)
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def show_window_from_tray(self, icon=None, item=None):
        self.ui_event_queue.put("show")

    def quit_app_from_tray(self, icon=None, item=None):
        self.ui_event_queue.put("quit")

    def process_ui_events(self):
        """在 Tk 主线程中处理来自托盘线程的窗口操作。"""
        try:
            while True:
                action = self.ui_event_queue.get_nowait()
                if action == "show":
                    self.deiconify()
                    self.lift()
                elif action == "quit":
                    self.on_real_close()
                    return
        except queue.Empty:
            pass

        if self.running:
            self.after(50, self.process_ui_events)

    def on_close(self):
        self.withdraw()

    def on_real_close(self):
        if self._closing:
            return

        self._closing = True
        self.running = False
        try:
            self.tray_icon.stop()
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
        
        create_task_btn = ctk.CTkButton(input_frame, text="+ 创建新任务", width=150, height=35,
                                        fg_color="#1F6AA5", font=ctk.CTkFont(weight="bold"),
                                        command=self.initiate_add_task_flow)
        create_task_btn.pack(side="left", padx=10, pady=15)
        
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
        ctk.CTkLabel(self.footer_frame, text="v7.1 Pro", font=ctk.CTkFont(size=10)).pack(side="right", padx=20)
    
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
        help_win = DpiStableToplevel(self)
        help_win.title("使用说明")
        help_win.geometry("480x550") 
        help_win.attributes("-topmost", True)
        
        if forced_countdown:
            help_win.protocol("WM_DELETE_WINDOW", lambda: None)
        
        ctk.CTkLabel(help_win, text="使用说明", font=ctk.CTkFont(size=20, weight="bold"), text_color="#1F6AA5").pack(pady=(20, 10))
        
        scroll_help = ctk.CTkScrollableFrame(help_win, fg_color="transparent")
        scroll_help.pack(fill="both", expand=True, padx=20, pady=5)
        
        text_process = (
            "1. 【使用流程】\n"
            "   ① 将音频文件放入软件目录下的 mp3 或 changyong 文件夹\n"
            "   ② 点击左侧“刷新音乐列表”\n"
            "   ③ 输入时间，点击“开始创建任务”\n"
            "   ④ 按向导依次完成：选择歌曲 -> 设置星期 -> 设置任务名称\n\n"
            "2. 【修改任务】\n"
            "   在任务列表中点击“修改”按钮，可以重新设置该任务的\n"
            "   时间、歌曲、播放星期和名称。\n\n"
            "3. 【静默运行】\n"
            "   程序启动时会自动隐藏界面，仅在右下角托盘显示图标。\n"
            "   点击右上角关闭按钮，程序也会最小化到托盘继续运行。\n\n"
            "4. 【注意事项】\n"
            "   - 支持格式：mp3, flac, wav, ogg, m4a, wma, aac\n"
            "   - 时间示例：130000, 13:00:00, 13：00：00\n"
            "     (支持纯数字、英文冒号及中文冒号)\n"
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

        self.btn_know = ctk.CTkButton(help_win, text=btn_text, state=btn_state, command=help_win.destroy)
        self.btn_know.pack(pady=20)

        if forced_countdown:
            self.countdown_val = 10
            def update_countdown():
                self.countdown_val -= 1
                if self.countdown_val > 0:
                    self.btn_know.configure(text=f"请阅读 ({self.countdown_val}s)")
                    help_win.after(1000, update_countdown)
                else:
                    self.btn_know.configure(text="我知道了", state="normal")
                    help_win.protocol("WM_DELETE_WINDOW", help_win.destroy)
            help_win.after(1000, update_countdown)

    def show_startup_error(self, error_msg):
        err_win = DpiStableToplevel(self)
        err_win.title("设置失败")
        err_win.geometry("300x200")
        err_win.attributes("-topmost", True)
        ctk.CTkLabel(err_win, text="无法开启开机自启", font=ctk.CTkFont(size=16, weight="bold"), text_color="red").pack(pady=20)
        ctk.CTkLabel(err_win, text="可能被杀毒软件拦截\n请手动添加白名单", text_color="gray").pack()
        ctk.CTkButton(err_win, text="确定", width=80, command=err_win.destroy).pack(pady=20)

    # --- 开机自启逻辑 ---
    def get_startup_command(self):
        """返回当前程序位置对应的开机启动命令。"""
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}" --silent'

        python_exe = sys.executable
        script_path = os.path.abspath(sys.argv[0])
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
            self.show_startup_error(str(e))

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

    def start_task_wizard_draft(self, config_data, edit_index=None):
        """创建只存在于内存的向导草稿，完成前不修改任务数据。"""
        if edit_index is None:
            draft = {
                "config": dict(config_data),
                "files": [],
                "weekdays": None,
                "name": None,
                "edit_index": None,
            }
        else:
            if not (0 <= edit_index < len(self.tasks)):
                return
            task = self.tasks[edit_index]
            draft = {
                "config": dict(config_data),
                "files": list(task.get("files", [])),
                "weekdays": list(task.get("weekdays", [])),
                "name": task.get("name", ""),
                "edit_index": edit_index,
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
        if draft["edit_index"] is None:
            self.finalize_add_task(
                draft["config"],
                draft["files"],
                draft["name"],
                draft["weekdays"]
            )
        else:
            self.finalize_modify(
                draft["edit_index"],
                draft["config"],
                draft["files"],
                draft["weekdays"],
                draft["name"]
            )

    def finalize_add_task(self, config, f_list, display_name, weekdays_indices):
        self.tasks.append({
            "time": config['time'], 
            "mode": config['mode'],
            "end_time": config['end_time'],
            "files": [make_portable_music_path(path) for path in f_list],
            "name": display_name,
            "weekdays": weekdays_indices,
            "enabled": True  # 默认开启
        })
        self.save_tasks() # 保存
        self.refresh_task_list()
        self.status_label.configure(text=f"成功添加任务: {display_name}", text_color="green")

    # --- 新的修改任务流程 ---
    def start_modify_task(self, index):
        if self.focus_active_task_dialog():
            return
        if not (0 <= index < len(self.tasks)): return
        
        task = self.tasks[index]
        self.status_label.configure(text=f"正在修改: {task['name']}", text_color="blue")
        
        # 步骤 0: 修改时间与模式
        initial_config = {
            "time": task['time'],
            "mode": task.get('mode', 'song'),
            "end_time": task.get('end_time', '')
        }
        
        TimeModeDialog(self, 
                       callback=lambda cfg: self.start_task_wizard_draft(cfg, index),
                       initial_data=initial_config)

    def finalize_modify(self, index, config, f_list, weekdays_indices, display_name):
        self.tasks[index]['time'] = config['time']
        self.tasks[index]['mode'] = config['mode']
        self.tasks[index]['end_time'] = config['end_time']
        self.tasks[index]['files'] = [make_portable_music_path(path) for path in f_list]
        self.tasks[index]['weekdays'] = weekdays_indices
        self.tasks[index]['name'] = display_name
        # 保持原有的启用/禁用状态，如果没有则默认为 True
        self.tasks[index]['enabled'] = self.tasks[index].get('enabled', True)
        
        self.save_tasks() # 保存
        self.refresh_task_list()
        self.status_label.configure(text=f"任务修改成功: {display_name}", text_color="green")

    # --- 任务启用/禁用逻辑 ---
    def toggle_task_enabled(self, index, switch_var):
        if 0 <= index < len(self.tasks):
            new_state = bool(switch_var.get())
            self.tasks[index]["enabled"] = new_state
            self.save_tasks()
            state_str = "启用" if new_state else "禁用"
            self.status_label.configure(text=f"已{state_str}任务: {self.tasks[index]['name']}", text_color="#1F6AA5")

    def refresh_task_list(self):
        for widget in self.schedule_scroll.winfo_children():
            destroy_widget_tree(widget)
        self.tasks.sort(key=lambda x: x["time"])
        
        week_map = ["一", "二", "三", "四", "五", "六", "日"]

        for idx, task in enumerate(self.tasks):
            f = ctk.CTkFrame(self.schedule_scroll)
            f.pack(fill="x", padx=5, pady=2)
            
            days_idx = task.get("weekdays", [])
            if len(days_idx) == 7: days_str = "每天"
            elif len(days_idx) == 0: days_str = "关"
            else:
                d_names = [week_map[i] for i in days_idx]
                days_str = f"周{','.join(d_names)}"
            
            # 显示时间段 (如果是模式二)
            mode = task.get("mode", "song")
            time_display = task["time"]
            if mode == "duration":
                time_display += f" - {task.get('end_time','?')}"
                mode_str = "[时长]"
            else:
                mode_str = "[曲目]"

            info_text = f"{time_display} {mode_str}\n{task['name']} ({len(task.get('files',[]))}首) {days_str}"
            
            # 任务信息
            lbl = ctk.CTkLabel(f, text=info_text, anchor="w", justify="left")
            lbl.pack(side="left", padx=10, pady=5)
            
            # 右侧按钮区域
            
            # 1. 删除按钮
            del_btn = ctk.CTkButton(f, text="删除", width=50, fg_color="#CC0000", hover_color="#AA0000", text_color="white",
                                  command=lambda i=idx: self.delete_task(i))
            del_btn.pack(side="right", padx=5, pady=5)
            
            # 2. 修改按钮
            edit_btn = ctk.CTkButton(f, text="修改", width=50, fg_color="#1F6AA5", hover_color="#144d7a",
                                   command=lambda i=idx: self.start_modify_task(i))
            edit_btn.pack(side="right", padx=5, pady=5)

            # 3. 启用/禁用开关
            is_enabled = task.get("enabled", True)
            switch_var = ctk.IntVar(value=1 if is_enabled else 0)
            
            cmd_toggle = lambda i=idx, v=switch_var: self.toggle_task_enabled(i, v)
            
            enable_switch = ctk.CTkSwitch(f, text="开启", variable=switch_var, command=cmd_toggle, 
                                          width=60, onvalue=1, offvalue=0)
            enable_switch.pack(side="right", padx=(5, 10), pady=5)
        
        self.update_top_status()

    def delete_task(self, index):
        if 0 <= index < len(self.tasks):
            del self.tasks[index]
            self.save_tasks() # 保存
            self.refresh_task_list()
            self.update_top_status()

    def start_playlist(self, task):
        self.playlist_queue = list(task.get("files", []))
        self.current_track_index = 0
        self.is_playlist_active = True
        self.current_task_name = task["name"]
        self.current_task_mode = task.get("mode", "song")
        self.current_task_end_time = task.get("end_time", "")
        
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
                    f"下次: {day_text} {next_run.strftime('%H:%M:%S')} "
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
            current_weekday = now.weekday()

            if self.last_trigger_date != now.date():
                self.last_trigger_date = now.date()
                self.triggered_tasks.clear()

            self.time_label.configure(text=current_time_str)

            # 1. 检查是否有任务需要开始
            for task in list(self.tasks):
                if not task.get("enabled", True):
                    continue

                trigger_key = (id(task), task.get("time"))
                should_start = (
                    task.get("time") == current_time_str
                    and current_weekday in task.get("weekdays", [])
                    and trigger_key not in self.triggered_tasks
                )
                if should_start:
                    self.triggered_tasks.add(trigger_key)
                    self.start_playlist(task)

            # 2. 检查当前播放是否需要处理
            if self.is_playlist_active:
                if self.current_task_mode == "duration" and self.current_task_end_time:
                    if current_time_str >= self.current_task_end_time:
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
