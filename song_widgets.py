"""Lightweight song-list widgets used by the task wizard."""

import time
import tkinter as tk

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
        drag_handle=None,
        on_drag_start=None,
        on_drag_motion=None,
        on_drag_end=None,
        layout_scheduler=None,
    ):
        self.full_text = full_text
        self.text_font = text_font
        self.control_font = control_font
        self.background = background
        self.index_text = index_text
        self.actions = [dict(action) for action in (actions or []) if action]
        self.drag_handle = dict(drag_handle) if drag_handle else None
        self._on_drag_start_callback = on_drag_start
        self._on_drag_motion_callback = on_drag_motion
        self._on_drag_end_callback = on_drag_end
        self._dragging = False
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
        self._drag_handle_hitbox = None
        self._drop_indicator_position = None
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
        self._drag_handle_item = None
        if self.drag_handle is not None:
            self._drag_handle_item = self.create_text(
                0,
                0,
                text=self.drag_handle.get("text", "≡"),
                fill=self.drag_handle.get("text_color", "#858585"),
                font=self.control_font,
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
        self._drop_indicator_item = self.create_rectangle(
            0, 0, 0, 0, fill="#3B8ED0", outline="", state="hidden"
        )

        self.bind("<Configure>", self._on_configure)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_button_press)
        self.bind("<B1-Motion>", self._on_drag_motion)
        self.bind("<ButtonRelease-1>", self._on_button_release)

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
            self._text_left = self._px(54 if self.drag_handle is not None else 36)
            control_width = self._px(96)
        self._text_right = max(self._text_left, width - control_width - self._px(3))

        self.coords(self._left_mask_item, 0, 0, self._text_left, height)
        self.coords(self._right_mask_item, self._text_right, 0, width, height)
        if self._index_item is not None:
            self.coords(self._index_item, self._px(5), center_y)
        if self._drag_handle_item is not None:
            handle_left = self._px(27)
            handle_right = self._px(47)
            self._drag_handle_hitbox = (handle_left, 0, handle_right, height)
            self.coords(self._drag_handle_item, (handle_left + handle_right) / 2, center_y)
        else:
            self._drag_handle_hitbox = None

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
        self._refresh_drop_indicator()

    def set_drop_indicator(self, position):
        """Display the insertion line above or below this row during a drag."""
        if position not in ("top", "bottom"):
            position = None
        if position == self._drop_indicator_position:
            return
        self._drop_indicator_position = position
        self._refresh_drop_indicator()

    def _refresh_drop_indicator(self):
        if self._drop_indicator_position is None:
            self.itemconfigure(self._drop_indicator_item, state="hidden")
            return
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        line_height = max(2, self._px(2))
        line_y = 0 if self._drop_indicator_position == "top" else height - line_height
        self.coords(
            self._drop_indicator_item,
            self._px(2), line_y, width - self._px(2), line_y + line_height,
        )
        self.itemconfigure(self._drop_indicator_item, state="normal")
        self.tag_raise(self._drop_indicator_item)

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

    def _on_drag_handle(self, x, y):
        if self._drag_handle_hitbox is None:
            return False
        x1, y1, x2, y2 = self._drag_handle_hitbox
        return x1 <= x < x2 and y1 <= y < y2

    def _on_motion(self, event):
        action_key = self._action_at(event.x, event.y)
        if action_key != self._hovered_action:
            self._hovered_action = action_key
            for action in self.actions:
                fill = action["hover_fill"] if action["key"] == action_key else action["fill"]
                self.itemconfigure(action["shape_item"], fill=fill)

        cursor_name = "hand2" if action_key is not None or self._on_drag_handle(event.x, event.y) else ""
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

    def _on_button_press(self, event):
        if self._on_drag_handle(event.x, event.y):
            self._dragging = True
            self.grab_set()
            if self._on_drag_start_callback is not None:
                self._on_drag_start_callback(self, event)
            return

        action_key = self._action_at(event.x, event.y)
        if action_key is None:
            return
        for action in self.actions:
            if action["key"] == action_key:
                action["command"]()
                return

    def _on_drag_motion(self, event):
        if self._dragging and self._on_drag_motion_callback is not None:
            self._on_drag_motion_callback(self, event)

    def _on_button_release(self, event):
        if not self._dragging:
            return
        self._dragging = False
        try:
            self.grab_release()
        except tk.TclError:
            pass
        if self._on_drag_end_callback is not None:
            self._on_drag_end_callback(self, event)

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


__all__ = ["SongRowCanvas"]
