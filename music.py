"""Application entry point for the local scheduled music player.

All business logic lives in :mod:`player_core` and the native shell lives in
:mod:`web_app`.  This module intentionally contains only resource-path
resolution, single-instance handling and startup assembly.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Any


def _application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APPLICATION_DIR = _application_directory()

MUTEX_NAME = "Global_MusicScheduler_Instance_Lock"
ERROR_ALREADY_EXISTS = 183
# Must stay equal to web_app.WINDOW_TITLE: wake_existing_window() finds the
# running instance by this exact caption. Changing one side alone silently
# breaks single-instance wake-up.
WINDOW_TITLE = "定时播放器"


def acquire_single_instance() -> tuple[Any, bool]:
    """Create the process mutex and report whether another instance exists."""

    if os.name != "nt":
        return None, False
    try:
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not mutex:
            return None, False
        return mutex, kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    except Exception:
        # A mutex is a convenience guard; a failed API call should not make
        # the player unusable on a restricted Windows environment.
        return None, False


def release_single_instance(mutex: Any) -> None:
    if mutex is None or os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.CloseHandle(mutex)
    except Exception:
        pass


def wake_existing_window() -> bool:
    """Bring an existing webview window to the foreground when possible."""

    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mutex, existing = acquire_single_instance()
    if existing:
        wake_existing_window()
        release_single_instance(mutex)
        return 0

    try:
        from web_app import run_web_app

        return run_web_app(APPLICATION_DIR, silent="--silent" in args)
    finally:
        release_single_instance(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
