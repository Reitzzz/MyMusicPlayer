"""CustomTkinter DPI handling and shared task-window behavior."""

import time

import customtkinter as ctk

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


def destroy_widget_tree(widget):
    """逐层销毁动态控件，确保 CustomTkinter 注销每个 DPI 回调。"""
    for child in widget.winfo_children():
        destroy_widget_tree(child)
    widget.destroy()


__all__ = [
    "StableDpiScalingController",
    "DpiStableCTk",
    "DpiStableToplevel",
    "TaskFlowWindow",
    "destroy_widget_tree",
]
