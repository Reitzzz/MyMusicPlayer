"""pywebview application shell and serialized JavaScript bridge.

The HTML page owns presentation.  This module owns the native window, tray
icon and bridge calls, while :mod:`player_core` owns business state.  Tray
threads only enqueue events; they never touch the webview directly.
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Mapping

try:
    import webview  # type: ignore
except Exception:  # pragma: no cover - optional dependency in source checkouts
    webview = None  # type: ignore

try:
    import pystray  # type: ignore
except Exception:  # pragma: no cover - optional dependency in non-Windows tests
    pystray = None  # type: ignore

try:
    from PIL import Image, ImageDraw  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore

from player_core import MusicPlayerController


WINDOW_TITLE = "定时播放器"
WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 750
WINDOW_BACKGROUND = "#0f1020"


class WebViewUnavailable(RuntimeError):
    """Raised when pywebview or its WebView2 backend cannot be started."""


def show_webview_error(detail: Any = None) -> None:
    """Show an actionable native Windows error for missing WebView2."""

    message = (
        "无法启动定时播放器的网页界面。\n\n"
        "请安装或修复 Microsoft Edge WebView2 Runtime，然后重新启动程序。"
    )
    if detail:
        compact = " ".join(str(detail).split())
        if len(compact) > 260:
            compact = compact[:257] + "..."
        message += f"\n\n详细信息：{compact}"
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10)
            return
        except Exception:
            pass
    # Keep diagnostics visible when running source checks on another host.
    print(message)


class WebBridge:
    """Minimal, validating API exposed to ``window.pywebview.api``."""

    def __init__(self, app: "WebApp") -> None:
        # pywebview recursively exposes public attributes on ``js_api``.
        # Keep the shell reference private or it will traverse native .NET
        # window objects and fail API injection with a recursion error.
        self._app = app

    def get_state(self) -> dict[str, Any]:
        return self._app.controller.get_state()

    def refresh_music(self) -> dict[str, Any]:
        return self._app.controller.refresh_music()

    def save_task(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._app.controller.save_task(payload or {})

    def delete_task(self, index: Any) -> dict[str, Any]:
        return self._app.controller.delete_task(index)

    def set_task_enabled(self, index: Any, enabled: Any) -> dict[str, Any]:
        return self._app.controller.set_task_enabled(index, enabled)

    def play_track(self, path: Any) -> dict[str, Any]:
        return self._app.controller.play_track(path)

    def stop_playback(self) -> dict[str, Any]:
        return self._app.controller.stop_playback()

    def set_startup(self, enabled: Any) -> dict[str, Any]:
        return self._app.controller.set_startup(enabled)

    def acknowledge_first_help(self) -> dict[str, Any]:
        return self._app.controller.acknowledge_first_help()

    def minimize(self) -> dict[str, Any]:
        return self._app.controller.window_command("minimize")

    def hide(self) -> dict[str, Any]:
        return self._app.controller.window_command("hide")

    def show(self) -> dict[str, Any]:
        return self._app.controller.window_command("show")

    def exit(self) -> dict[str, Any]:
        return self._app.controller.window_command("exit")

    def poll_events(self) -> list[dict[str, Any]]:
        return self._app.poll_events()


class WebApp:
    """Native shell for the fixed-size, frameless local web application."""

    def __init__(self, application_dir: Path, *, silent: bool = False) -> None:
        self.application_dir = Path(application_dir).resolve()
        self.silent = bool(silent)
        self.window: Any = None
        self._closing = False
        self._lock = threading.RLock()
        self._event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._native_action_queue: queue.Queue[str] = queue.Queue()
        self._native_stop_event = threading.Event()
        self._native_dispatcher_thread: threading.Thread | None = None
        self._tray_icon: Any = None
        self._tray_thread: threading.Thread | None = None
        self._tray_unavailable = False
        self._tray_alert_sent = False
        self.controller = MusicPlayerController(
            self.application_dir,
            event_sink=self.enqueue_event,
            window_actions={
                "minimize": lambda: self.enqueue_native_action("minimize"),
                "hide": lambda: self.enqueue_native_action("hide"),
                "show": lambda: self.enqueue_native_action("show"),
                "exit": lambda: self.enqueue_native_action("exit"),
            },
            silent=self.silent,
        )
        self.bridge = WebBridge(self)

    def enqueue_event(self, event: dict[str, Any]) -> None:
        """Serialize background/controller events for the page poller."""

        if isinstance(event, dict):
            self._event_queue.put(event)

    def poll_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for _ in range(80):
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def enqueue_native_action(self, action: str) -> None:
        """Queue a window action without touching webview from worker threads."""

        if action in {"minimize", "hide", "show", "exit"}:
            self._native_action_queue.put(action)

    def _dispatch_native_actions(self) -> None:
        while not self._native_stop_event.is_set():
            try:
                action = self._native_action_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if action == "minimize":
                self._minimize_window()
            elif action == "hide":
                self._hide_window()
            elif action == "show":
                self._show_window()
            elif action == "exit":
                self.shutdown()
                return

    def _start_native_dispatcher(self) -> None:
        if self._native_dispatcher_thread is not None:
            return
        self._native_stop_event.clear()
        self._native_dispatcher_thread = threading.Thread(
            target=self._dispatch_native_actions,
            name="WebViewWindowDispatcher",
            daemon=True,
        )
        self._native_dispatcher_thread.start()

    def _minimize_window(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.minimize()
        except Exception:
            # Frameless webview backends may not expose minimize; hiding is a
            # safe equivalent for this utility and preserves tray semantics.
            try:
                window.hide()
            except Exception:
                pass

    def _hide_window(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.hide()
        except Exception:
            pass

    def _show_window(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.show()
            window.restore()
        except Exception:
            try:
                window.show()
            except Exception:
                pass

    def _resolve_ui_index(self) -> Path:
        """Resolve index.html from application directory or bundled resources."""
        candidates = [self.application_dir / "ui" / "index.html"]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "ui" / "index.html")
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def create_window(self) -> Any:
        if webview is None:
            raise WebViewUnavailable("pywebview 未安装")
        index_path = self._resolve_ui_index()
        if not index_path.exists():
            raise WebViewUnavailable(f"缺少界面资源: {index_path.name}")
        self.window = webview.create_window(
            WINDOW_TITLE,
            url=index_path.as_uri(),
            js_api=self.bridge,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=(WINDOW_WIDTH, WINDOW_HEIGHT),
            resizable=False,
            frameless=True,
            easy_drag=False,
            hidden=self.silent,
            text_select=True,
            background_color=WINDOW_BACKGROUND,
        )
        try:
            self.window.events.loaded += self._on_loaded
            self.window.events.closing += self.on_closing
        except Exception:
            pass
        return self.window

    def _on_loaded(self) -> None:
        # JS fetches authoritative state itself.  The event is useful for
        # browser harnesses and keeps the bridge free of UI writes.
        self.enqueue_event({"type": "loaded"})
        if not self.silent:
            self._show_window()

    @staticmethod
    def _tray_image() -> Any:
        if Image is None or ImageDraw is None:
            return None
        image = Image.new("RGBA", (64, 64), "#a99bff")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((12, 12, 52, 52), radius=12, fill="#181a30")
        draw.rectangle((27, 20, 33, 43), fill="#f5f4ff")
        draw.ellipse((20, 37, 33, 50), fill="#f5f4ff")
        draw.ellipse((27, 30, 40, 43), fill="#f5f4ff")
        return image

    def _tray_show(self, _icon: Any = None, _item: Any = None) -> None:
        self.enqueue_native_action("show")

    def _tray_exit(self, _icon: Any = None, _item: Any = None) -> None:
        self.enqueue_native_action("exit")

    def setup_tray(self) -> None:
        if pystray is None:
            self._handle_tray_failure("pystray 未安装")
            return
        try:
            image = self._tray_image()
            menu = pystray.Menu(
                pystray.MenuItem("显示窗口", self._tray_show, default=True),
                pystray.MenuItem("退出程序", self._tray_exit),
            )
            self._tray_icon = pystray.Icon("MusicScheduler", image, WINDOW_TITLE, menu)
            self._tray_thread = threading.Thread(target=self._run_tray, name="MusicTray", daemon=True)
            self._tray_thread.start()
        except Exception as exc:
            self._tray_icon = None
            self._tray_thread = None
            self._handle_tray_failure(str(exc))

    def _run_tray(self) -> None:
        icon = self._tray_icon
        if icon is None:
            return
        try:
            icon.run()
        except Exception as exc:
            self._handle_tray_failure(str(exc), allow_silent_show=False)
        else:
            if not self._closing:
                self._handle_tray_failure("托盘线程意外停止", allow_silent_show=False)

    def _handle_tray_failure(self, detail: Any = None, *, allow_silent_show: bool = True) -> None:
        # This method only changes controller state and queues page events; it
        # is safe to call from setup or the pystray worker.
        self._tray_unavailable = True
        self.controller.status_message = "系统托盘不可用，关闭窗口将直接退出"
        self.controller.status_tone = "warning"
        self.controller._emit("state")
        if self.silent and allow_silent_show:
            self.enqueue_native_action("show")
        if not self._tray_alert_sent:
            self._tray_alert_sent = True
            self.controller._emit(
                "error",
                title="系统托盘不可用",
                message="系统托盘不可用，程序将以窗口模式运行；关闭窗口会直接退出。",
                detail=detail,
            )

    def on_closing(self) -> bool:
        """Handle native close requests while retaining tray semantics."""

        if self._closing:
            return True
        self.enqueue_native_action("exit" if self._tray_unavailable else "hide")
        # False cancels the native close. The dispatcher either hides the
        # window or performs a real, explicitly flagged shutdown.
        return False

    def shutdown(self) -> dict[str, Any]:
        with self._lock:
            if self._closing:
                return {"ok": True, "state": self.controller.get_state()}
            self._closing = True
        self._native_stop_event.set()
        self.controller.shutdown()
        icon = self._tray_icon
        thread = self._tray_thread
        self._tray_icon = None
        self._tray_thread = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=2.0)
        dispatcher = self._native_dispatcher_thread
        self._native_dispatcher_thread = None
        if (
            dispatcher is not None
            and dispatcher is not threading.current_thread()
            and dispatcher.is_alive()
        ):
            dispatcher.join(timeout=2.0)
        window = self.window
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        return {"ok": True, "state": self.controller.get_state()}

    def run(self) -> int:
        if webview is None:
            raise WebViewUnavailable("pywebview 未安装；请安装 pywebview 与 WebView2 Runtime")
        self.create_window()
        self._start_native_dispatcher()
        self.setup_tray()
        self.controller.start()
        try:
            # Explicit EdgeChromium selects WebView2 on Windows and yields a
            # clear exception when the runtime is missing.
            webview.start(gui="edgechromium", debug=False)
        finally:
            self.shutdown()
        return 0


def run_web_app(application_dir: Path, *, silent: bool = False) -> int:
    """Run the app and convert backend failures into a native error dialog."""

    app = WebApp(application_dir, silent=silent)
    try:
        return app.run()
    except WebViewUnavailable as exc:
        show_webview_error(exc)
        return 1
    except Exception as exc:
        show_webview_error(exc)
        return 1


__all__ = [
    "WINDOW_HEIGHT",
    "WINDOW_TITLE",
    "WINDOW_WIDTH",
    "WebApp",
    "WebBridge",
    "WebViewUnavailable",
    "run_web_app",
    "show_webview_error",
]
