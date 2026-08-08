"""Task-wizard dialogs. Communication with the app stays callback-based."""

import functools
import os
from datetime import datetime
from tkinter import font as tkfont

import customtkinter as ctk

from song_widgets import SongRowCanvas
from windowing import TaskFlowWindow


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
            text=f"任务时间: {format_clock_for_display(time_str)}",
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
        self._dragged_playlist_index = None
        self._drag_target_index = None
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
        ctk.CTkLabel(right_frame, text="播放顺序 (从上到下，可拖动 ≡ 调整)", text_color="#1F6AA5", font=ctk.CTkFont(weight="bold")).pack(pady=5)

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

    def _start_song_drag(self, row, _event):
        try:
            self._dragged_playlist_index = self.playlist_song_rows.index(row)
            self._drag_target_index = self._dragged_playlist_index
        except ValueError:
            self._dragged_playlist_index = None
            self._drag_target_index = None

    def _show_song_drag_target(self, target_index):
        last_index = len(self.playlist_song_rows) - 1
        for index, playlist_row in enumerate(self.playlist_song_rows):
            if target_index == index:
                position = "top"
            elif target_index == len(self.playlist_song_rows) and index == last_index:
                position = "bottom"
            else:
                position = None
            playlist_row.set_drop_indicator(position)

    def _playlist_insert_index_at(self, event):
        left = self.scroll_playlist.winfo_rootx()
        top = self.scroll_playlist.winfo_rooty()
        right = left + self.scroll_playlist.winfo_width()
        bottom = top + self.scroll_playlist.winfo_height()
        if not (left <= event.x_root <= right and top <= event.y_root <= bottom):
            return None

        for index, row in enumerate(self.playlist_song_rows):
            midpoint = row.winfo_rooty() + row.winfo_height() / 2
            if event.y_root < midpoint:
                return index
        return len(self.playlist_song_rows)

    def _update_song_drag(self, _row, event):
        if self._dragged_playlist_index is None:
            return
        self._drag_target_index = self._playlist_insert_index_at(event)
        self._show_song_drag_target(self._drag_target_index)

    def _finish_song_drag(self, _row, event):
        source_index = self._dragged_playlist_index
        target_index = self._playlist_insert_index_at(event)
        self._dragged_playlist_index = None
        self._drag_target_index = None
        self._show_song_drag_target(None)
        if source_index is None or target_index is None:
            return

        if target_index > source_index:
            target_index -= 1
        if target_index == source_index:
            return

        song = self.selected_files.pop(source_index)
        self.selected_files.insert(target_index, song)
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
                    drag_handle={"text": "≡", "text_color": "#858585"},
                    on_drag_start=self._start_song_drag,
                    on_drag_motion=self._update_song_drag,
                    on_drag_end=self._finish_song_drag,
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


def normalize_time_value(value):
    """Return an ``(hour, minute)`` pair for supported legacy time formats."""
    if value is None:
        return None

    clean_value = str(value).strip().replace("：", ":").replace(" ", "")
    if not clean_value:
        return None

    second = 0
    if ":" in clean_value:
        parts = clean_value.split(":")
        if len(parts) not in (2, 3) or any(not part.isdigit() for part in parts):
            return None
        hour, minute = int(parts[0]), int(parts[1])
        if len(parts) == 3:
            second = int(parts[2])
    else:
        if not clean_value.isdigit():
            return None
        if len(clean_value) <= 2:
            hour, minute = int(clean_value), 0
        elif len(clean_value) == 4:
            hour, minute = int(clean_value[:2]), int(clean_value[2:])
        elif len(clean_value) == 6:
            hour = int(clean_value[:2])
            minute = int(clean_value[2:4])
            second = int(clean_value[4:])
        else:
            return None

    if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
        return hour, minute
    return None


def format_clock_for_display(value):
    """Hide zero seconds without concealing precision used by legacy tasks."""
    try:
        parsed = datetime.strptime(str(value), "%H:%M:%S")
    except (TypeError, ValueError):
        parsed_parts = normalize_time_value(value)
        if parsed_parts is None:
            return str(value or "--:--")
        return f"{parsed_parts[0]:02d}:{parsed_parts[1]:02d}"
    if parsed.second:
        return parsed.strftime("%H:%M:%S")
    return parsed.strftime("%H:%M")


class TimeSpinInput(ctk.CTkFrame):
    """Compact 24-hour segmented time input for keyboard and pointer users."""

    _NORMAL_BORDER = ("#979DA2", "#565B5E")
    _ERROR_BORDER = "#D13438"

    def __init__(self, master, value="", command=None, **kwargs):
        super().__init__(
            master,
            fg_color="transparent",
            border_width=1,
            border_color=self._NORMAL_BORDER,
            corner_radius=8,
            **kwargs,
        )
        self.command = command
        self._updating = False
        self._active_segment = "hour"
        self.hour_var = ctk.StringVar(value="")
        self.minute_var = ctk.StringVar(value="")

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(2, weight=1)

        entry_options = {
            "width": 58,
            "height": 42,
            "justify": "center",
            "font": ctk.CTkFont(size=20, weight="bold"),
            "border_width": 0,
            "fg_color": "transparent",
            "placeholder_text": "--",
        }
        self.hour_entry = ctk.CTkEntry(self, textvariable=self.hour_var, **entry_options)
        self.hour_entry.grid(row=0, column=0, padx=(8, 2), pady=4)
        ctk.CTkLabel(self, text=":", width=14, font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=1, pady=4
        )
        self.minute_entry = ctk.CTkEntry(self, textvariable=self.minute_var, **entry_options)
        self.minute_entry.grid(row=0, column=2, padx=(2, 4), pady=4)

        step_frame = ctk.CTkFrame(self, width=28, height=50, fg_color="transparent")
        step_frame.grid(row=0, column=3, padx=(0, 5), pady=3, sticky="ns")
        step_frame.grid_propagate(False)
        step_frame.grid_rowconfigure((0, 1), weight=1)
        self.up_button = ctk.CTkButton(
            step_frame,
            text="▲",
            width=26,
            height=20,
            corner_radius=5,
            font=ctk.CTkFont(size=10),
            command=lambda: self.adjust_active(1),
        )
        self.up_button.grid(row=0, column=0, pady=(0, 1), sticky="nsew")
        self.down_button = ctk.CTkButton(
            step_frame,
            text="▼",
            width=26,
            height=20,
            corner_radius=5,
            font=ctk.CTkFont(size=10),
            command=lambda: self.adjust_active(-1),
        )
        self.down_button.grid(row=1, column=0, pady=(1, 0), sticky="nsew")

        self._trace_tokens = (
            (self.hour_var, self.hour_var.trace_add("write", lambda *_: self._on_value_changed("hour"))),
            (self.minute_var, self.minute_var.trace_add("write", lambda *_: self._on_value_changed("minute"))),
        )
        self._bind_entry(self.hour_entry, "hour")
        self._bind_entry(self.minute_entry, "minute")
        self.set_time(value)

    def _bind_entry(self, entry, segment):
        entry.bind("<FocusIn>", lambda _event, name=segment: self._set_active(name))
        entry.bind("<FocusOut>", lambda _event, name=segment: self._pad_segment(name))
        entry.bind("<KeyRelease>", lambda event, name=segment: self._on_key_release(event, name))
        entry.bind("<Up>", lambda _event, name=segment: self._adjust_from_event(name, 1))
        entry.bind("<Down>", lambda _event, name=segment: self._adjust_from_event(name, -1))
        entry.bind("<Left>", lambda event, name=segment: self._move_between_segments(event, name, -1))
        entry.bind("<Right>", lambda event, name=segment: self._move_between_segments(event, name, 1))
        entry.bind("<MouseWheel>", lambda event, name=segment: self._on_mousewheel(event, name))
        entry.bind("<<Paste>>", lambda _event, name=segment: self._on_paste(name))

    def _set_active(self, segment):
        self._active_segment = segment

    def _set_var(self, variable, value):
        self._updating = True
        try:
            variable.set(value)
        finally:
            self._updating = False

    def _on_value_changed(self, segment):
        if self._updating:
            return
        variable = self.hour_var if segment == "hour" else self.minute_var
        raw_value = variable.get()
        clean_value = "".join(character for character in raw_value if character.isdigit())[:2]
        if clean_value != raw_value:
            self._set_var(variable, clean_value)
        self._notify_change()

    def _on_key_release(self, event, segment):
        if segment == "hour" and event.keysym.isdigit() and len(self.hour_var.get()) == 2:
            self.focus_minute()

    def _move_between_segments(self, event, segment, direction):
        try:
            cursor_index = event.widget.index("insert")
            text_length = len(event.widget.get())
        except Exception:
            return None
        if segment == "minute" and direction < 0 and cursor_index == 0:
            self.focus_hour()
            return "break"
        if segment == "hour" and direction > 0 and cursor_index >= text_length:
            self.focus_minute()
            return "break"
        return None

    def _pad_segment(self, segment):
        variable = self.hour_var if segment == "hour" else self.minute_var
        value = variable.get()
        if value and len(value) == 1:
            self._set_var(variable, value.zfill(2))
            self._notify_change()

    def _adjust_from_event(self, segment, delta):
        self._active_segment = segment
        self.adjust_active(delta)
        return "break"

    def _on_mousewheel(self, event, segment):
        delta = 1 if event.delta > 0 else -1
        return self._adjust_from_event(segment, delta)

    def _on_paste(self, segment):
        try:
            pasted = self.clipboard_get().strip()
        except Exception:
            return "break"

        compact = pasted.replace(" ", "")
        if ":" in compact or len(compact) in (4, 6):
            self.set_time(compact)
            return "break"

        if compact.isdigit() and len(compact) <= 2:
            limit = 23 if segment == "hour" else 59
            number = int(compact)
            if number <= limit:
                variable = self.hour_var if segment == "hour" else self.minute_var
                self._set_var(variable, f"{number:02d}")
                self._notify_change()
        return "break"

    def adjust_active(self, delta):
        segment = self._active_segment
        variable = self.hour_var if segment == "hour" else self.minute_var
        modulus = 24 if segment == "hour" else 60
        raw_value = variable.get()
        current = int(raw_value) if raw_value.isdigit() and int(raw_value) < modulus else 0
        self._set_var(variable, f"{(current + delta) % modulus:02d}")
        self._notify_change()
        if segment == "hour":
            self.focus_hour(select=False)
        else:
            self.focus_minute(select=False)

    def focus_hour(self, select=True):
        self._active_segment = "hour"
        self.hour_entry.focus_set()
        if select:
            self.hour_entry.select_range(0, "end")

    def focus_minute(self, select=True):
        self._active_segment = "minute"
        self.minute_entry.focus_set()
        if select:
            self.minute_entry.select_range(0, "end")

    def set_time(self, value):
        parsed = normalize_time_value(value)
        self._updating = True
        try:
            if parsed is None:
                self.hour_var.set("")
                self.minute_var.set("")
            else:
                self.hour_var.set(f"{parsed[0]:02d}")
                self.minute_var.set(f"{parsed[1]:02d}")
        finally:
            self._updating = False
        self._notify_change()

    def get_time(self):
        hour_value = self.hour_var.get()
        minute_value = self.minute_var.get()
        if not hour_value or not minute_value:
            return ""
        try:
            hour = int(hour_value)
            minute = int(minute_value)
        except ValueError:
            return ""
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}:00"
        return ""

    def has_value(self):
        return bool(self.hour_var.get() or self.minute_var.get())

    def set_error(self, has_error):
        self.configure(border_color=self._ERROR_BORDER if has_error else self._NORMAL_BORDER)

    def _notify_change(self):
        if self.command is not None:
            self.after_idle(self.command)

    def destroy(self):
        for variable, token in self._trace_tokens:
            try:
                variable.trace_remove("write", token)
            except Exception:
                pass
        super().destroy()


class TimeModeDialog(TaskFlowWindow):
    def __init__(self, parent, callback, initial_data=None):
        super().__init__(parent)
        self.callback = callback
        self.title("设置播放时间")
        self.geometry("440x520")
        self.minsize(440, 520)
        self.resizable(False, False)

        initial_data = initial_data or {}
        init_start = initial_data.get("time", "")
        init_mode = initial_data.get("mode", "song")
        if init_mode not in ("song", "duration"):
            init_mode = "song"
        init_end = initial_data.get("end_time", "")
        init_next_day = bool(initial_data.get("end_next_day", False))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, minsize=76)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew", padx=34, pady=(20, 0))
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(6, minsize=128)

        ctk.CTkLabel(
            content,
            text="设置播放时间",
            font=ctk.CTkFont(size=21, weight="bold"),
            text_color="#1F6AA5",
        ).grid(row=0, column=0, pady=(0, 14))

        self.start_time_label = ctk.CTkLabel(
            content,
            text="开始时间",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.start_time_label.grid(row=1, column=0, sticky="w")
        self.start_input = TimeSpinInput(content, value=init_start, command=self.validate_form)
        self.start_input.grid(row=2, column=0, pady=(7, 17))

        ctk.CTkLabel(content, text="播放行为", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=3, column=0, sticky="w", pady=(0, 4)
        )
        self.mode_var = ctk.StringVar(value=init_mode)
        self.radio_song = ctk.CTkRadioButton(
            content,
            text="播放所选歌曲，播完后停止",
            variable=self.mode_var,
            value="song",
            command=self.toggle_mode,
        )
        self.radio_song.grid(row=4, column=0, sticky="w", padx=14, pady=(4, 6))
        self.radio_duration = ctk.CTkRadioButton(
            content,
            text="循环播放，到指定时间停止",
            variable=self.mode_var,
            value="duration",
            command=self.toggle_mode,
        )
        self.radio_duration.grid(row=5, column=0, sticky="w", padx=14)

        self.end_time_frame = ctk.CTkFrame(content, fg_color="transparent")
        self.end_time_frame.grid(row=6, column=0, sticky="nsew", pady=(8, 0))
        self.end_time_frame.grid_columnconfigure(0, weight=1)
        self.end_time_label = ctk.CTkLabel(
            self.end_time_frame,
            text="结束时间",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.end_time_label.grid(row=0, column=0, sticky="w")

        end_controls = ctk.CTkFrame(self.end_time_frame, fg_color="transparent")
        end_controls.grid(row=1, column=0, pady=(7, 0))
        self.end_input = TimeSpinInput(end_controls, value=init_end, command=self.validate_form)
        self.end_input.grid(row=0, column=0)
        self.next_day_var = ctk.BooleanVar(value=init_next_day)
        self.next_day_checkbox = ctk.CTkCheckBox(
            end_controls,
            text="次日结束",
            variable=self.next_day_var,
            command=self.validate_form,
            width=100,
        )
        self.next_day_checkbox.grid(row=1, column=0, sticky="w", pady=(7, 0))

        self.feedback_label = ctk.CTkLabel(
            content,
            text="",
            height=40,
            wraplength=360,
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=12),
        )
        self.feedback_label.grid(row=7, column=0, sticky="ew", pady=(4, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="nsew", padx=34, pady=(7, 18))
        footer.grid_columnconfigure(0, weight=1)
        self.cancel_button = ctk.CTkButton(
            footer,
            text="取消",
            command=self.destroy,
            width=96,
            height=42,
            corner_radius=21,
            fg_color="transparent",
            border_width=2,
            text_color="gray",
        )
        self.cancel_button.grid(row=0, column=0, sticky="w")
        self.next_button = ctk.CTkButton(
            footer,
            text="下一步",
            command=self.on_confirm,
            width=126,
            height=42,
            corner_radius=21,
            font=ctk.CTkFont(size=15, weight="bold"),
            state="disabled",
        )
        self.next_button.grid(row=0, column=1, sticky="e")

        self.bind("<Return>", self._on_return)
        self.toggle_mode()
        self.after(50, self.start_input.focus_hour)

    def toggle_mode(self):
        if self.mode_var.get() == "duration":
            self.end_time_frame.grid()
        else:
            self.end_time_frame.grid_remove()
        self.validate_form()

    def validate_form(self):
        if not self.winfo_exists():
            return False
        start_time = self.start_input.get_time()
        mode = self.mode_var.get()
        end_time = self.end_input.get_time() if mode == "duration" else ""
        next_day = bool(self.next_day_var.get()) if mode == "duration" else False

        start_error = self.start_input.has_value() and not bool(start_time)
        end_error = mode == "duration" and self.end_input.has_value() and not bool(end_time)
        message = "请选择开始时间"
        message_kind = "hint"
        valid = False

        if not start_time and self.start_input.has_value():
            message = "请完整输入有效的开始时间（00:00 至 23:59）"
            message_kind = "error"
        elif start_time:
            start_display = format_clock_for_display(start_time)
            if mode == "song":
                message = f"将在 {start_display} 开始，所选歌曲播完后停止"
                message_kind = "valid"
                valid = True
            elif not end_time:
                if self.end_input.has_value():
                    message = "请完整输入有效的结束时间（00:00 至 23:59）"
                    message_kind = "error"
                else:
                    message = "请选择结束时间"
            elif not next_day and end_time <= start_time:
                message = "结束时间不晚于开始时间；如需跨午夜，请勾选“次日结束”"
                message_kind = "error"
                end_error = True
            else:
                end_display = format_clock_for_display(end_time)
                end_target = f"次日 {end_display}" if next_day else end_display
                message = f"将在 {start_display} 开始，循环播放至{end_target}"
                message_kind = "valid"
                valid = True

        self.start_input.set_error(start_error)
        self.end_input.set_error(end_error)
        self.feedback_label.configure(
            text=message,
            text_color={
                "hint": "gray",
                "error": "#D13438",
                "valid": "#1F6AA5",
            }[message_kind],
        )
        self.next_button.configure(state="normal" if valid else "disabled")
        return valid

    def _on_return(self, _event=None):
        if self.validate_form():
            self.on_confirm()
        return "break"

    def on_confirm(self):
        if not self.validate_form():
            return

        mode = self.mode_var.get()
        callback = self.callback
        config_data = {
            "time": self.start_input.get_time(),
            "mode": mode,
            "end_time": self.end_input.get_time() if mode == "duration" else "",
            "end_next_day": bool(self.next_day_var.get()) if mode == "duration" else False,
        }
        self.destroy()
        callback(config_data)

__all__ = [
    "TaskRenameDialog",
    "WeekdaySelectionDialog",
    "MultiSongSelectDialog",
    "TimeSpinInput",
    "TimeModeDialog",
    "normalize_time_value",
    "format_clock_for_display",
]
