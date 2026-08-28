"""Application entry point for the local scheduled music player.

All business logic lives in :mod:`player_core` and the native shell lives in
:mod:`web_app`.  This module intentionally contains only resource-path
resolution, single-instance handling and startup assembly.
"""

from __future__ import annotations

import ctypes
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from player_core import (
    CONFIG_FILE,
    MUSIC_DIRECTORIES,
    SCHEDULE_GRACE_SECONDS,
    TASKS_FILE,
    calculate_task_end_at as _calculate_task_end_at,
    make_portable_music_path as _make_portable_music_path,
    resolve_music_path as _resolve_music_path,
)


def _application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APPLICATION_DIR = _application_directory()
TASKS_PATH = APPLICATION_DIR / TASKS_FILE
CONFIG_PATH = APPLICATION_DIR / CONFIG_FILE


def resolve_music_path(path_value: str | os.PathLike[str]) -> str:
    """Resolve a task path relative to the executable/resource directory."""

    return _resolve_music_path(path_value, APPLICATION_DIR)


def make_portable_music_path(path_value: str | os.PathLike[str]) -> str:
    """Convert a task path to the portable ``tasks.json`` representation."""

    return _make_portable_music_path(path_value, APPLICATION_DIR)


def calculate_task_end_at(task: Mapping[str, Any], started_at: datetime) -> datetime | None:
    return _calculate_task_end_at(task, started_at)


MUTEX_NAME = "Global_MusicScheduler_Instance_Lock"
ERROR_ALREADY_EXISTS = 183
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


__all__ = [
    "APPLICATION_DIR",
    "CONFIG_PATH",
    "MUSIC_DIRECTORIES",
    "SCHEDULE_GRACE_SECONDS",
    "TASKS_PATH",
    "WINDOW_TITLE",
    "acquire_single_instance",
    "calculate_task_end_at",
    "main",
    "make_portable_music_path",
    "release_single_instance",
    "resolve_music_path",
    "wake_existing_window",
]


if __name__ == "__main__":
    raise SystemExit(main())
