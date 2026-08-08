"""Task-wizard dialogs. Communication with the app stays callback-based."""

import functools
import os
from datetime import datetime
from tkinter import font as tkfont

import customtkinter as ctk

from song_widgets import SongRowCanvas
from windowing import DpiStableToplevel, TaskFlowWindow


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

        ctk.CTkLabel(
            content_frame,
            text="为这个任务起个名字",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#1F6AA5",
        ).pack(pady=(25, 15))

        self.name_entry = ctk.CTkEntry(
            content_frame,
            width=250,
            height=35,
            font=ctk.CTkFont(size=14),
        )
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
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.btn_back.grid(row=0, column=0, sticky="w")

        self.btn_confirm = ctk.CTkButton(
            btn_frame,
            text="完成",
            command=self.on_confirm,
            width=120,
            height=42,
            corner_radius=21,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
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


class WeekdaySelectionDialog(TaskFlowWindow):
    def __init__(
        self,
        parent,
        time_str,
        song_count,
        callback,
        back_callback,
        initial_selection=None,
    ):
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

        ctk.CTkLabel(
            content_frame,
            text=f"任务时间: {time_str}",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#1F6AA5",
        ).pack(pady=(25, 5))
        ctk.CTkLabel(
            content_frame,
            text=f"包含歌曲数量: {song_count} 首",
            text_color="gray",
            font=ctk.CTkFont(size=12),
        ).pack(pady=(0, 14))

        ctk.CTkFrame(content_frame, height=2, fg_color="#E0E0E0").pack(
            fill="x", padx=30, pady=5
        )
        ctk.CTkLabel(
            content_frame,
            text="请勾选需要播放的星期:",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(pady=12)

        self.checkboxes = []
        days = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        self.check_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        self.check_frame.pack(pady=5)

        for index, day in enumerate(days):
            is_checked = True
            if initial_selection is not None:
                is_checked = index in initial_selection

            variable = ctk.BooleanVar(value=is_checked)
            checkbox = ctk.CTkCheckBox(
                self.check_frame,
                text=day,
                variable=variable,
                font=ctk.CTkFont(size=14),
                checkbox_width=24,
                checkbox_height=24,
                border_width=2,
            )
            checkbox.grid(row=index, column=0, pady=8, padx=40, sticky="w")
            self.checkboxes.append(variable)

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
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.btn_back.grid(row=0, column=0, sticky="w")

        self.btn_cancel = ctk.CTkButton(
            btn_frame,
            text="取消",
            command=self.destroy,
            height=44,
            corner_radius=22,
            width=78,
            fg_color="transparent",
            border_width=2,
            border_color="gray",
            text_color="gray",
            hover_color="#EEEEEE",
        )
        self.btn_cancel.grid(row=0, column=2, padx=(12, 10), sticky="e")

        self.btn_confirm = ctk.CTkButton(
            btn_frame,
            text="下一步",
            command=self.on_confirm,
            width=110,
            height=44,
            corner_radius=22,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#1F6AA5",
            hover_color="#144d7a",
        )
        self.btn_confirm.grid(row=0, column=3, sticky="e")

    def on_confirm(self):
        selected_indices = [index for index, variable in enumerate(self.checkboxes) if variable.get()]
        callback = self.callback
        self.destroy()
        callback(selected_indices)

    def on_back(self):
        selected_indices = [index for index, variable in enumerate(self.checkboxes) if variable.get()]
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

__all__ = [
    "TaskRenameDialog",
    "WeekdaySelectionDialog",
    "MultiSongSelectDialog",
    "TimeModeDialog",
]
