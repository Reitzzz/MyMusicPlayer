"""Platform-independent playback, task storage and scheduling logic.

The web front-end talks to this module through :mod:`web_app`.  Nothing in
this module imports a GUI toolkit or updates a UI object.  State changes are
reported through ``event_sink`` so callers can serialize them on their own
UI/event thread.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

try:  # pygame is an application dependency, but core tests may mock it.
    import pygame  # type: ignore
except Exception:  # pragma: no cover - exercised only in incomplete installs
    pygame = None  # type: ignore

try:
    import winreg  # type: ignore
except Exception:  # pragma: no cover - non-Windows development environments
    winreg = None  # type: ignore


TASKS_FILE = "tasks.json"
CONFIG_FILE = "config.json"
MUSIC_DIRECTORIES = ("mp3", "changyong")
ALLOWED_EXTENSIONS = (".mp3", ".wav", ".flac", ".ogg")
SCHEDULE_GRACE_SECONDS = 90
STARTUP_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APP_NAME = "MusicSchedulerByStudent"


def resolve_music_path(path_value: str | os.PathLike[str], application_dir: Path) -> str:
    """Resolve a stored relative/legacy absolute audio path.

    Paths relative to the application directory are the canonical format.
    Older releases stored absolute paths; if a path contains ``mp3`` or
    ``changyong`` we remap that suffix to the current portable application
    directory, allowing the whole folder to be moved safely.
    """

    if not isinstance(path_value, (str, os.PathLike)):
        raise TypeError("音频路径必须是字符串")
    app_dir = Path(application_dir).resolve()
    path = Path(path_value)
    if not path.is_absolute():
        return str((app_dir / path).resolve())

    lower_directories = {item.casefold() for item in MUSIC_DIRECTORIES}
    for index, part in enumerate(path.parts):
        if part.casefold() in lower_directories:
            candidate = app_dir.joinpath(*path.parts[index:]).resolve()
            if candidate.exists() or not path.exists():
                return str(candidate)

    if path.exists():
        return str(path.resolve())
    return str(path)


def make_portable_music_path(
    path_value: str | os.PathLike[str], application_dir: Path
) -> str:
    """Return the portable path representation used in ``tasks.json``."""

    resolved_path = Path(resolve_music_path(path_value, application_dir))
    app_dir = Path(application_dir).resolve()
    try:
        return resolved_path.relative_to(app_dir).as_posix()
    except ValueError:
        # Preserve supported legacy external paths rather than silently
        # changing their meaning.  The UI never exposes an external picker.
        return str(resolved_path)


def calculate_task_end_at(task: Mapping[str, Any], started_at: datetime) -> datetime | None:
    """Calculate the concrete stop datetime for a duration-mode task."""

    if task.get("mode", "song") != "duration":
        return None
    raw_end = task.get("end_time", "")
    normalized = TaskStore.normalize_clock(raw_end)
    if not normalized:
        return None
    try:
        end_clock = datetime.strptime(normalized, "%H:%M:%S").time()
    except (TypeError, ValueError):
        return None

    end_at = datetime.combine(started_at.date(), end_clock)
    if bool(task.get("end_next_day", False)):
        end_at += timedelta(days=1)
    return end_at


def _deepcopy_tasks(tasks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [copy.deepcopy(dict(task)) for task in tasks]


class TaskStore:
    """Loss-aware, atomic persistence for the existing task JSON format."""

    def __init__(self, application_dir: Path, tasks_path: Path | None = None) -> None:
        self.application_dir = Path(application_dir).resolve()
        self.tasks_path = Path(tasks_path or (self.application_dir / TASKS_FILE)).resolve()
        self.tasks: list[dict[str, Any]] = []
        self.load_state = "pending"  # pending, ready, failed
        self.backup_path: Path | None = None
        self.last_error: str | None = None
        self.last_notice = ""

    @staticmethod
    def is_valid_clock(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        try:
            parsed = datetime.strptime(value, "%H:%M:%S")
        except ValueError:
            return False
        return parsed.strftime("%H:%M:%S") == value

    @classmethod
    def normalize_clock(cls, value: Any) -> str | None:
        """Normalize UI ``HH:MM`` and stored ``HH:MM:SS`` clock values."""

        if not isinstance(value, str):
            return None
        value = value.strip()
        if cls.is_valid_clock(value):
            return value
        try:
            parsed = datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return None
        return parsed.strftime("%H:%M:%S")

    def resolve_music_path(self, path_value: str | os.PathLike[str]) -> str:
        return resolve_music_path(path_value, self.application_dir)

    def make_portable_music_path(self, path_value: str | os.PathLike[str]) -> str:
        return make_portable_music_path(path_value, self.application_dir)

    def _backup_tasks_file(self) -> Path:
        """Copy the source to a unique corrupt backup without overwriting it."""

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = self.tasks_path.with_name(
            f"{self.tasks_path.stem}.corrupt-{timestamp}{self.tasks_path.suffix}"
        )
        counter = 1
        while backup_path.exists():
            backup_path = self.tasks_path.with_name(
                f"{self.tasks_path.stem}.corrupt-{timestamp}-{counter}{self.tasks_path.suffix}"
            )
            counter += 1
        try:
            shutil.copy2(self.tasks_path, backup_path)
        except Exception:
            try:
                backup_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        self.backup_path = backup_path
        return backup_path

    def _sanitize_task(self, raw_task: Any) -> tuple[dict[str, Any] | None, int]:
        """Return ``(normalized_task, lossy_field_count)``.

        Missing fields and portable path conversion are lossless compatibility
        normalization. Explicitly invalid values and discarded entries count
        as lossy and cause a source backup before writes are enabled.
        """

        if not isinstance(raw_task, dict):
            return None, 1

        task = dict(raw_task)
        lossy = 0

        time_value = raw_task.get("time")
        if not self.is_valid_clock(time_value):
            return None, 1
        task["time"] = time_value

        raw_weekdays = raw_task.get("weekdays", [])
        weekdays: list[int] = []
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
        files: list[str] = []
        if isinstance(raw_files, list):
            for path_value in raw_files:
                if not isinstance(path_value, str) or not path_value.strip():
                    continue
                try:
                    files.append(self.make_portable_music_path(path_value))
                except Exception:
                    continue
            if len(files) != len(raw_files):
                lossy += 1
        elif "files" in raw_task:
            lossy += 1
        task["files"] = files

        raw_mode = raw_task.get("mode", "song")
        if isinstance(raw_mode, str) and raw_mode in {"song", "duration"}:
            mode = raw_mode
        else:
            mode = "song"
        if "mode" in raw_task and raw_mode != mode:
            lossy += 1
        task["mode"] = mode

        raw_end_time = raw_task.get("end_time", "")
        if mode == "duration":
            if not self.is_valid_clock(raw_end_time):
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

    def load(self) -> list[dict[str, Any]]:
        """Load, normalize and (when necessary) back up the source file."""

        self.backup_path = None
        self.last_error = None
        self.last_notice = ""
        self.load_state = "pending"

        if not self.tasks_path.exists():
            self.tasks = []
            self.load_state = "ready"
            self.last_notice = "无历史任务记录"
            return []

        try:
            with self.tasks_path.open("r", encoding="utf-8-sig") as handle:
                loaded_data = json.load(handle)
            if not isinstance(loaded_data, list):
                raise ValueError("tasks.json 顶层必须是任务列表")
        except Exception as exc:
            self.last_error = str(exc)
            try:
                backup_path = self._backup_tasks_file()
            except Exception as backup_exc:
                self.last_error = f"{exc}; 自动备份失败: {backup_exc}"
                self.backup_path = None
            self.tasks = []
            self.load_state = "failed"
            self.last_notice = "任务文件无法读取，本次运行已进入只读保护。"
            return []

        sanitized_tasks: list[dict[str, Any]] = []
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
        backup_error: Exception | None = None
        if needs_backup:
            try:
                self._backup_tasks_file()
            except Exception as exc:
                backup_error = exc

        self.tasks = sanitized_tasks
        if needs_backup and self.backup_path is None:
            self.load_state = "failed"
            self.last_error = str(backup_error)
            self.last_notice = "任务数据存在问题且备份失败，已进入只读保护。"
        else:
            self.load_state = "ready"
            if needs_backup:
                self.last_notice = (
                    f"已加载 {len(self.tasks)} 个任务，跳过 {skipped_count} 个，"
                    f"修正 {lossy_count} 个字段"
                )
            else:
                self.last_notice = f"已加载 {len(self.tasks)} 个任务"
        return _deepcopy_tasks(self.tasks)

    def save(self, tasks: Iterable[Mapping[str, Any]] | None = None) -> bool:
        """Atomically save tasks, refusing writes in read-only protection."""

        if self.load_state != "ready":
            self.last_error = "任务数据当前不可写"
            return False

        tasks_to_save: list[dict[str, Any]] = []
        try:
            for task in tasks if tasks is not None else self.tasks:
                saved_task = dict(task)
                saved_task["files"] = [
                    self.make_portable_music_path(path)
                    for path in saved_task.get("files", [])
                ]
                tasks_to_save.append(saved_task)

            self.tasks_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.tasks_path.with_name(f"{self.tasks_path.name}.tmp")
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(tasks_to_save, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.tasks_path)
            self.tasks = _deepcopy_tasks(tasks_to_save)
            self.last_error = None
            return True
        except Exception as exc:
            try:
                temp_path.unlink(missing_ok=True)  # type: ignore[has-type]
            except Exception:
                pass
            self.last_error = str(exc)
            return False


class PlaybackEngine:
    """Thread-safe pygame playback and playlist state machine."""

    def __init__(
        self,
        application_dir: Path,
        *,
        pygame_module: Any = None,
        initialize_audio: bool = True,
    ) -> None:
        self.application_dir = Path(application_dir).resolve()
        self._pygame = pygame_module if pygame_module is not None else pygame
        self._lock = threading.RLock()
        self._mixer_ready = False
        self.audio_error: str | None = None
        self.playlist_queue: list[str] = []
        self.current_track_index = 0
        self.is_playlist_active = False
        self.current_task_name = ""
        self.current_task_mode = "song"
        self.current_task_end_at: datetime | None = None
        self.current_track_path: str | None = None
        self._current_file: Any = None
        if initialize_audio:
            self.initialize()

    def initialize(self) -> bool:
        with self._lock:
            if self._mixer_ready:
                return True
            if self._pygame is None:
                self.audio_error = "pygame 未安装"
                return False
            mixer_initialized = False
            try:
                self._pygame.mixer.init()
                mixer_initialized = True
                self._pygame.mixer.music.set_volume(1.0)
                self._mixer_ready = True
                self.audio_error = None
                return True
            except Exception as exc:
                self.audio_error = str(exc)
                self._mixer_ready = False
                if mixer_initialized:
                    try:
                        self._pygame.mixer.quit()
                    except Exception:
                        pass
                return False

    def _resolve(self, path_value: str | os.PathLike[str]) -> Path:
        return Path(resolve_music_path(path_value, self.application_dir))

    def _close_file(self) -> None:
        file_obj = self._current_file
        self._current_file = None
        if file_obj is not None:
            try:
                file_obj.close()
            except Exception:
                pass

    def _unload(self) -> None:
        if not self._mixer_ready:
            self._close_file()
            return
        try:
            self._pygame.mixer.music.unload()
        except Exception:
            pass
        self._close_file()

    def _play_path(self, path_value: str | os.PathLike[str]) -> tuple[bool, str]:
        try:
            path = self._resolve(path_value)
        except Exception as exc:
            return False, f"无效路径: {exc}"

        if path.suffix.casefold() not in ALLOWED_EXTENSIONS:
            return False, f"不支持的音频格式: {path.suffix}"

        if not path.exists() or not path.is_file():
            return False, "文件不存在"
        if not self._mixer_ready and not self.initialize():
            return False, self.audio_error or "音频设备不可用"
        try:
            if self._pygame.mixer.music.get_busy():
                self._pygame.mixer.music.stop()
        except Exception:
            pass
        self._unload()
        file_obj = None
        try:
            file_obj = path.open("rb")
            self._current_file = file_obj
            self._pygame.mixer.music.load(file_obj)
            self._pygame.mixer.music.play()
            self.current_track_path = str(path)
            return True, ""
        except Exception as exc:
            if self._mixer_ready:
                try:
                    self._pygame.mixer.music.unload()
                except Exception:
                    pass
            if file_obj is not None:
                try:
                    file_obj.close()
                except Exception:
                    pass
            self._current_file = None
            self.current_track_path = None
            return False, str(exc)

    def play_manual(self, path_value: str) -> tuple[bool, str]:
        with self._lock:
            self._stop_locked()
            task = {
                "name": "手动播放",
                "mode": "song",
                "files": [path_value],
            }
            result = self.start_playlist(task)
            if result.get("ok"):
                return True, result.get("message", "")
            self._stop_locked()
            return False, result.get("message", "播放失败")

    def start_playlist(
        self,
        task: Mapping[str, Any],
        started_at: datetime | None = None,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            current_time = now or datetime.now()
            effective_start = started_at or current_time
            mode = str(task.get("mode", "song"))
            end_at = calculate_task_end_at(task, effective_start)
            if mode == "duration" and end_at is not None and current_time >= end_at:
                return {
                    "ok": False,
                    "event": "expired",
                    "message": f"任务未播放：已超过设定结束时间（{task.get('name', '未命名任务')}）",
                }
            self.playlist_queue = [str(path) for path in task.get("files", [])]
            self.current_track_index = 0
            self.is_playlist_active = True
            self.current_task_name = str(task.get("name", "未命名任务"))
            self.current_task_mode = mode
            self.current_task_end_at = end_at
            result = self._play_next_locked()
            return result

    def _is_busy_locked(self) -> bool:
        if not self._mixer_ready:
            return False
        try:
            return bool(self._pygame.mixer.music.get_busy())
        except Exception:
            return False

    def _play_next_locked(self) -> dict[str, Any]:
        total_files = len(self.playlist_queue)
        if total_files == 0:
            self._stop_locked()
            return {"ok": False, "event": "empty", "message": "任务停止：播放列表为空"}

        attempted = 0
        last_error = ""
        while attempted < total_files:
            if self.current_track_index >= total_files:
                if self.current_task_mode == "duration":
                    self.current_track_index = 0
                else:
                    self._stop_locked()
                    return {"ok": True, "event": "completed", "message": "任务播放完毕"}

            path_value = self.playlist_queue[self.current_track_index]
            self.current_track_index += 1
            attempted += 1
            success, message = self._play_path(path_value)
            if success:
                return {
                    "ok": True,
                    "event": "playing",
                    "message": f"正在播放: {Path(path_value).name}",
                    "track": path_value,
                }
            last_error = message

        self._stop_locked()
        suffix = f"（{last_error}）" if last_error else ""
        return {
            "ok": False,
            "event": "failed",
            "message": f"任务停止：所有歌曲均无法播放{suffix}",
        }

    def tick(self, now: datetime | None = None) -> dict[str, Any] | None:
        with self._lock:
            if not self.is_playlist_active:
                return None
            current_time = now or datetime.now()
            if self.current_task_mode == "duration" and self.current_task_end_at:
                if current_time >= self.current_task_end_at:
                    self._stop_locked()
                    return {"ok": True, "event": "ended", "message": "已达到设定结束时间"}
            if not self._is_busy_locked():
                return self._play_next_locked()
            return None

    def _stop_locked(self) -> None:
        if self._mixer_ready:
            try:
                self._pygame.mixer.music.stop()
            except Exception:
                pass
        self._unload()
        self.is_playlist_active = False
        self.playlist_queue = []
        self.current_track_index = 0
        self.current_task_name = ""
        self.current_task_mode = "song"
        self.current_task_end_at = None
        self.current_track_path = None

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def shutdown(self) -> None:
        with self._lock:
            self._stop_locked()
            if self._mixer_ready and self._pygame is not None:
                try:
                    self._pygame.mixer.quit()
                except Exception:
                    pass
            self._mixer_ready = False

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": bool(self.is_playlist_active),
                "task_name": self.current_task_name,
                "mode": self.current_task_mode,
                "end_at": self.current_task_end_at.isoformat() if self.current_task_end_at else None,
                "current_track": self.current_track_path,
                "queue_length": len(self.playlist_queue),
                "queue_index": self.current_track_index,
            }


class SchedulerService:
    """90-second-grace scheduler with rollback-safe monotonic baselines."""

    def __init__(
        self,
        tasks_provider: Callable[[], list[Mapping[str, Any]]],
        on_due: Callable[[Mapping[str, Any], int, datetime], None],
        on_tick: Callable[[datetime], None] | None = None,
        *,
        interval: float = 0.5,
        grace_seconds: int = SCHEDULE_GRACE_SECONDS,
    ) -> None:
        self.tasks_provider = tasks_provider
        self.on_due = on_due
        self.on_tick = on_tick
        self.interval = interval
        self.grace_seconds = grace_seconds
        self.last_tick_dt: datetime | None = None
        self.evaluated_max_dt: datetime | None = None
        self.running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _candidate_datetime(task: Mapping[str, Any], date_value: Any) -> datetime | None:
        time_value = task.get("time")
        if not TaskStore.is_valid_clock(time_value):
            return None
        weekdays = task.get("weekdays", [])
        if not isinstance(weekdays, list) or date_value.weekday() not in weekdays:
            return None
        try:
            task_time = datetime.strptime(str(time_value), "%H:%M:%S").time()
        except (TypeError, ValueError):
            return None
        return datetime.combine(date_value, task_time)

    def tick(self, now: datetime | None = None) -> Mapping[str, Any] | None:
        """Evaluate one clock tick; callable directly in deterministic tests."""

        with self._lock:
            if not self.running and self._thread is not None:
                return None
            current = now or datetime.now()
            previous = self.last_tick_dt
            if previous is not None and current < previous:
                # A rollback must reset the baseline without lowering evaluated_max_dt.
                self.last_tick_dt = current
                if self.on_tick is not None:
                    self.on_tick(current)
                return None

            if previous is None:
                interval_start = current.replace(microsecond=0) - timedelta(microseconds=1)
            else:
                interval_start = previous

            high_watermark = self.evaluated_max_dt if self.evaluated_max_dt is not None else interval_start
            effective_start = max(interval_start, high_watermark)
            grace_start = current - timedelta(seconds=self.grace_seconds)
            scan_start = max(effective_start, grace_start)
            tasks = list(self.tasks_provider())
            due_candidates: list[tuple[datetime, int, Mapping[str, Any]]] = []
            candidate_date = scan_start.date()
            while candidate_date <= current.date():
                for task_index, task in enumerate(tasks):
                    if not isinstance(task, Mapping) or not task.get("enabled", True):
                        continue
                    candidate = self._candidate_datetime(task, candidate_date)
                    if candidate is None:
                        continue
                    if effective_start < candidate <= current and candidate >= grace_start:
                        due_candidates.append((candidate, task_index, task))
                candidate_date += timedelta(days=1)

            # Advance the baseline and evaluated max before invoking potentially failing playback.
            self.last_tick_dt = current
            if self.evaluated_max_dt is None or current > self.evaluated_max_dt:
                self.evaluated_max_dt = current

            selected: Mapping[str, Any] | None = None
            if due_candidates:
                candidate_dt, selected_index, selected = max(due_candidates, key=lambda item: (item[0], item[1]))
                self.on_due(selected, selected_index, candidate_dt)
            if self.on_tick is not None:
                self.on_tick(current)
            return selected

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick(datetime.now())
            except Exception:
                # The controller owns user-facing error reporting; a scheduler
                # failure must never kill the application process.
                if self.on_tick is not None:
                    try:
                        self.on_tick(datetime.now())
                    except Exception:
                        pass
            self._stop_event.wait(self.interval)

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self.running = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="MusicScheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self.running = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=2.0)


class MusicPlayerController:
    """Application service used by the pywebview bridge."""

    def __init__(
        self,
        application_dir: Path,
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        window_actions: Mapping[str, Callable[[], Any]] | None = None,
        silent: bool = False,
        initialize_audio: bool = True,
    ) -> None:
        self.application_dir = Path(application_dir).resolve()
        self.event_sink = event_sink
        self.window_actions = dict(window_actions or {})
        self.silent = bool(silent)
        self._lock = threading.RLock()
        self.running = True
        self._closing = False
        self.tasks_revision = 0
        self.music_files: list[dict[str, str]] = []
        self.status_message = "就绪"
        self.status_tone = "neutral"
        self._startup_enabled = False
        self.first_run_help = False
        self.playback: PlaybackEngine | None = None
        self.scheduler: SchedulerService | None = None
        self.store: TaskStore | None = None
        try:
            self.config = self._load_config()
            self.store = TaskStore(self.application_dir)
            self.tasks = self.store.load()
            self.tasks.sort(key=lambda task: task.get("time", "") if isinstance(task, dict) else "")
            self.store.tasks = _deepcopy_tasks(self.tasks)
            self.playback = PlaybackEngine(
                self.application_dir,
                initialize_audio=initialize_audio,
            )
            self.scheduler = SchedulerService(
                self._get_tasks,
                self._on_due_task,
                self._on_scheduler_tick,
            )
            self.refresh_music(emit=False)
            self._startup_enabled = self._read_startup_status()
            self._set_initial_status()
        except Exception:
            self.shutdown()
            raise

    def _load_config(self) -> dict[str, Any]:
        config_path = self.application_dir / CONFIG_FILE
        if not config_path.exists():
            self.first_run_help = True
            try:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = config_path.with_name(f"{config_path.name}.tmp")
                with temp_path.open("w", encoding="utf-8") as handle:
                    json.dump({"first_run": False}, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, config_path)
            except Exception:
                pass
            return {"first_run": False}
        try:
            with config_path.open("r", encoding="utf-8-sig") as handle:
                loaded = json.load(handle)
            return loaded if isinstance(loaded, dict) else {"first_run": False}
        except Exception:
            return {"first_run": False}

    def _set_initial_status(self) -> None:
        if self.store.load_state == "failed":
            self.status_message = "任务文件无法读取，本次运行仅可查看"
            self.status_tone = "danger"
        elif self.store.last_notice:
            self.status_message = self.store.last_notice
            self.status_tone = "warning" if self.store.backup_path else "neutral"

    def _get_tasks(self) -> list[Mapping[str, Any]]:
        with self._lock:
            return _deepcopy_tasks(self.tasks)

    def _emit(self, event_type: str = "state", **payload: Any) -> None:
        if self.event_sink is None:
            return
        event = {"type": event_type, **payload}
        if event_type == "state":
            event["state"] = self.get_state()
        try:
            self.event_sink(event)
        except Exception:
            pass

    def _set_status(self, message: str, tone: str = "neutral", *, toast: bool = False) -> None:
        self.status_message = str(message)
        self.status_tone = tone
        self._emit("state")
        if toast:
            self._emit("toast", message=self.status_message, tone=tone)

    @property
    def tasks_load_state(self) -> str:
        return self.store.load_state

    def _read_startup_status(self) -> bool:
        if winreg is None:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY_PATH, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, STARTUP_APP_NAME)
            return str(value).strip().casefold() == self.get_startup_command().strip().casefold()
        except (FileNotFoundError, OSError):
            return False

    def get_startup_command(self) -> str:
        if getattr(__import__("sys"), "frozen", False):
            return f'"{__import__("sys").executable}" --silent'
        import sys

        return f'"{sys.executable}" "{(self.application_dir / "music.py").resolve()}" --silent'

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            now = datetime.now()
            next_run = self._get_next_run_info(now)
            return {
                "clock": now.strftime("%H:%M:%S"),
                "tasks": _deepcopy_tasks(self.tasks),
                "tasks_revision": self.tasks_revision,
                "music_files": copy.deepcopy(self.music_files),
                "startup_enabled": bool(self._startup_enabled),
                "playback": self.playback.state() if self.playback is not None else {},
                "next_run": next_run,
                "status": {"message": self.status_message, "tone": self.status_tone},
                "store": {
                    "load_state": self.store.load_state if self.store else "pending",
                    "read_only": (self.store.load_state != "ready") if self.store else True,
                    "backup_path": self.store.backup_path.name if (self.store and self.store.backup_path) else None,
                    "error": self.store.last_error if self.store else None,
                },
                "first_run_help": bool(self.first_run_help),
                "running": bool(self.running),
            }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                self.scheduler.start()
            return self.get_state()

    def refresh_music(self, *, emit: bool = True) -> dict[str, Any]:
        with self._lock:
            found: list[dict[str, str]] = []
            for folder in MUSIC_DIRECTORIES:
                directory = self.application_dir / folder
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                except OSError:
                    continue
                try:
                    entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
                except OSError:
                    continue
                for path in entries:
                    if path.is_file() and path.suffix.casefold() in ALLOWED_EXTENSIONS:
                        portable = self.store.make_portable_music_path(path)
                        found.append({
                            "path": portable,
                            "name": path.name,
                            "folder": folder,
                        })
            self.music_files = found
            self.status_message = f"刷新成功，找到 {len(found)} 个音频文件"
            self.status_tone = "success"
            if emit:
                self._emit("state")
                self._emit("toast", message=f"音乐列表已刷新（{len(found)} 首）", tone="success")
            return self.get_state()

    def _read_only_result(self) -> dict[str, Any]:
        if self.store.load_state == "pending":
            message = "任务数据仍在加载，请稍后再试"
        else:
            message = "任务文件读取失败，本次运行已进入只读保护"
        self._set_status(message, "danger")
        return {"ok": False, "error": message, "state": self.get_state()}

    @staticmethod
    def _strict_index(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def _normalize_task_payload(
        self, raw: Mapping[str, Any], existing: Mapping[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not isinstance(raw, Mapping):
            return None, "任务数据必须是对象"
        start_time = TaskStore.normalize_clock(raw.get("time"))
        if start_time is None:
            return None, "开始时间格式无效，应为 HH:MM"
        mode = raw.get("mode", existing.get("mode", "song") if existing else "song")
        if not isinstance(mode, str) or mode not in {"song", "duration"}:
            return None, "播放模式无效"
        end_time_value = raw.get("end_time", existing.get("end_time", "") if existing else "")
        end_time = TaskStore.normalize_clock(end_time_value) if end_time_value else ""
        end_next_day = raw.get(
            "end_next_day", existing.get("end_next_day", False) if existing else False
        )
        if not isinstance(end_next_day, bool):
            return None, "次日结束设置无效"
        if mode == "song":
            end_time = ""
            end_next_day = False
        else:  # duration
            if not end_time:
                return None, "固定时长模式必须设置有效结束时间"
            if not end_next_day and end_time <= start_time:
                return None, "结束时间不晚于开始时间；如需跨午夜，请勾选“次日结束”"

        files_value = raw.get("files", existing.get("files", []) if existing else [])
        if not isinstance(files_value, list):
            return None, "歌曲列表必须是数组"
        existing_files: set[str] = set()
        if existing and isinstance(existing.get("files"), list):
            for item in existing["files"]:
                existing_files.add(str(item))
                try:
                    existing_files.add(self.store.make_portable_music_path(item))
                except Exception:
                    pass
        files: list[str] = []
        for path_value in files_value:
            if not isinstance(path_value, str) or not path_value.strip():
                return None, "歌曲路径无效"
            try:
                portable = self.store.make_portable_music_path(path_value)
                suffix = Path(portable).suffix.casefold()
                if portable not in existing_files and str(path_value) not in existing_files:
                    if suffix not in ALLOWED_EXTENSIONS:
                        return None, f"不支持的音频格式: {suffix}"
                files.append(portable)
            except Exception as exc:
                return None, f"歌曲路径无效: {exc}"
        if not files:
            return None, "请至少选择一首歌曲"

        weekdays_value = raw.get("weekdays", existing.get("weekdays", []) if existing else [])
        if not isinstance(weekdays_value, list):
            return None, "播放星期必须是数组"
        weekdays: list[int] = []
        for day in weekdays_value:
            if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6:
                return None, "播放星期无效"
            if day not in weekdays:
                weekdays.append(day)

        name_value = raw.get("name", existing.get("name", "") if existing else "")
        if not isinstance(name_value, str):
            return None, "任务名称无效"
        name = name_value.strip() or "未命名任务"
        if len(name) > 80:
            return None, "任务名称不能超过 80 个字符"

        enabled = raw.get("enabled", existing.get("enabled", True) if existing else True)
        if not isinstance(enabled, bool):
            return None, "启用状态无效"

        task = dict(existing or {})
        task.update(
            {
                "time": start_time,
                "mode": mode,
                "end_time": end_time or "",
                "end_next_day": end_next_day,
                "files": files,
                "name": name,
                "weekdays": weekdays,
                "enabled": enabled,
            }
        )
        return task, None

    def save_task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.store.load_state != "ready":
                return self._read_only_result()
            if not isinstance(payload, Mapping):
                return {"ok": False, "error": "任务数据必须是对象", "state": self.get_state()}
            expected_revision = payload.get("expected_revision")
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                return {"ok": False, "error": "任务版本无效", "state": self.get_state()}
            if expected_revision != self.tasks_revision:
                return {"ok": False, "error": "任务列表已更新，请重新操作", "state": self.get_state()}

            raw_task = payload.get("task") if isinstance(payload.get("task"), Mapping) else payload
            index_value = payload.get("index")
            index = self._strict_index(index_value) if index_value is not None else None
            existing: Mapping[str, Any] | None = None
            if index is not None:
                if index < 0 or index >= len(self.tasks):
                    return {"ok": False, "error": "任务不存在", "state": self.get_state()}
                existing = self.tasks[index]
            elif index_value is not None:
                return {"ok": False, "error": "任务索引无效", "state": self.get_state()}
            normalized, error = self._normalize_task_payload(raw_task, existing)
            if normalized is None:
                self._set_status(error or "任务数据无效", "danger")
                return {"ok": False, "error": error or "任务数据无效", "state": self.get_state()}
            previous = _deepcopy_tasks(self.tasks)
            if index is None:
                self.tasks.append(normalized)
            else:
                self.tasks[index] = normalized
            self.tasks.sort(key=lambda task: task.get("time", ""))
            if not self.store.save(self.tasks):
                self.tasks = previous
                self.store.tasks = _deepcopy_tasks(previous)
                self._set_status("任务设置未能保存，请检查程序目录的写入权限或磁盘空间。", "danger")
                return {"ok": False, "error": self.store.last_error or "保存失败", "state": self.get_state()}
            self.tasks_revision += 1
            self._set_status(
                f"任务{'修改' if index is not None else '创建'}成功：{normalized['name']}",
                "success",
                toast=True,
            )
            return {"ok": True, "task": copy.deepcopy(normalized), "state": self.get_state()}

    def delete_task(self, index_value: Any, expected_revision: Any = None) -> dict[str, Any]:
        with self._lock:
            if self.store.load_state != "ready":
                return self._read_only_result()
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                return {"ok": False, "error": "任务版本无效", "state": self.get_state()}
            if expected_revision != self.tasks_revision:
                return {"ok": False, "error": "任务列表已更新，请重新操作", "state": self.get_state()}
            index = self._strict_index(index_value)
            if index is None or index < 0 or index >= len(self.tasks):
                return {"ok": False, "error": "任务索引无效", "state": self.get_state()}
            previous = _deepcopy_tasks(self.tasks)
            deleted = self.tasks.pop(index)
            if not self.store.save(self.tasks):
                self.tasks = previous
                self.store.tasks = _deepcopy_tasks(previous)
                self._set_status("任务设置未能保存，请检查程序目录的写入权限或磁盘空间。", "danger")
                return {"ok": False, "error": self.store.last_error or "保存失败", "state": self.get_state()}
            self.tasks_revision += 1
            name = str(deleted.get("name", "未命名任务"))
            self._set_status(f"已删除任务：{name}", "success", toast=True)
            return {"ok": True, "deleted": copy.deepcopy(deleted), "state": self.get_state()}

    def set_task_enabled(self, index_value: Any, enabled: Any, expected_revision: Any = None) -> dict[str, Any]:
        with self._lock:
            if self.store.load_state != "ready":
                return self._read_only_result()
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                return {"ok": False, "error": "任务版本无效", "state": self.get_state()}
            if expected_revision != self.tasks_revision:
                return {"ok": False, "error": "任务列表已更新，请重新操作", "state": self.get_state()}
            index = self._strict_index(index_value)
            if index is None or index < 0 or index >= len(self.tasks):
                return {"ok": False, "error": "任务索引无效", "state": self.get_state()}
            if not isinstance(enabled, bool):
                return {"ok": False, "error": "启用状态无效", "state": self.get_state()}
            previous = _deepcopy_tasks(self.tasks)
            self.tasks[index]["enabled"] = enabled
            if not self.store.save(self.tasks):
                self.tasks = previous
                self.store.tasks = _deepcopy_tasks(previous)
                self._set_status("任务设置未能保存，请检查程序目录的写入权限或磁盘空间。", "danger")
                return {"ok": False, "error": self.store.last_error or "保存失败", "state": self.get_state()}
            self.tasks_revision += 1
            name = str(self.tasks[index].get("name", "未命名任务"))
            self._set_status(f"已{'启用' if enabled else '暂停'}任务：{name}", "success", toast=True)
            return {"ok": True, "state": self.get_state()}

    def _get_next_run_info(self, now: datetime) -> dict[str, Any] | None:
        candidates: list[tuple[datetime, Mapping[str, Any]]] = []
        for task in self.tasks:
            if not task.get("enabled", True):
                continue
            next_run = self.get_next_run(task, now)
            if next_run is not None:
                candidates.append((next_run, task))
        if not candidates:
            return None
        next_run, task = min(candidates, key=lambda item: item[0])
        day_offset = (next_run.date() - now.date()).days
        if day_offset == 0:
            day_text = "今天"
        elif day_offset == 1:
            day_text = "明天"
        else:
            day_text = f"周{['一', '二', '三', '四', '五', '六', '日'][next_run.weekday()]}"
        return {
            "date_label": day_text,
            "time": next_run.strftime("%H:%M"),
            "name": str(task.get("name", "未命名任务")),
            "mode": task.get("mode", "song"),
            "files_count": len(task.get("files", [])) if isinstance(task.get("files"), list) else 0,
            # The page formats weekday labels itself; sending the raw list keeps
            # that wording in one place.
            "weekdays": list(task.get("weekdays", [])) if isinstance(task.get("weekdays"), list) else [],
        }

    @staticmethod
    def get_next_run(task: Mapping[str, Any], now: datetime) -> datetime | None:
        weekdays = {
            day for day in task.get("weekdays", [])
            if isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6
        }
        if not weekdays:
            return None
        try:
            task_time = datetime.strptime(str(task["time"]), "%H:%M:%S").time()
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

    def play_track(self, path_value: Any) -> dict[str, Any]:
        with self._lock:
            if not isinstance(path_value, str) or not path_value.strip():
                return {"ok": False, "error": "歌曲路径无效", "state": self.get_state()}
            if self.playback is None:
                return {"ok": False, "error": "音频播放器未初始化", "state": self.get_state()}
            success, message = self.playback.play_manual(path_value)
            if success:
                self._set_status(f"正在播放：{Path(path_value).name}", "playing", toast=True)
                return {"ok": True, "state": self.get_state()}
            self._set_status(f"播放失败：{message}", "danger")
            self._emit("error", title="播放失败", message=message)
            return {"ok": False, "error": message, "state": self.get_state()}

    def stop_playback(self) -> dict[str, Any]:
        with self._lock:
            self.playback.stop()
            self._set_status("播放已停止", "neutral", toast=True)
            return {"ok": True, "state": self.get_state()}

    def set_startup(self, enabled: Any) -> dict[str, Any]:
        with self._lock:
            if not isinstance(enabled, bool):
                return {"ok": False, "error": "开机自启状态无效", "state": self.get_state()}
            if winreg is None:
                message = "当前系统不支持 Windows 开机自启"
                self._set_status(message, "danger")
                return {"ok": False, "error": message, "state": self.get_state()}
            try:
                if enabled:
                    with winreg.CreateKeyEx(
                        winreg.HKEY_CURRENT_USER, STARTUP_KEY_PATH, 0, winreg.KEY_SET_VALUE
                    ) as key:
                        winreg.SetValueEx(key, STARTUP_APP_NAME, 0, winreg.REG_SZ, self.get_startup_command())
                else:
                    try:
                        with winreg.OpenKey(
                            winreg.HKEY_CURRENT_USER, STARTUP_KEY_PATH, 0, winreg.KEY_SET_VALUE
                        ) as key:
                            winreg.DeleteValue(key, STARTUP_APP_NAME)
                    except FileNotFoundError:
                        pass
                self._startup_enabled = enabled
                message = f"已{'开启' if enabled else '关闭'}开机自启"
                self._set_status(message, "success", toast=True)
                return {"ok": True, "state": self.get_state()}
            except Exception as exc:
                self._startup_enabled = False if enabled else self._startup_enabled
                message = "设置开机自启失败"
                self._set_status(message, "danger")
                self._emit("error", title="设置失败", message=str(exc))
                return {"ok": False, "error": str(exc), "state": self.get_state()}

    def acknowledge_first_help(self) -> dict[str, Any]:
        with self._lock:
            self.first_run_help = False
            return {"ok": True, "state": self.get_state()}

    def window_command(self, action: str) -> dict[str, Any]:
        with self._lock:
            callback = self.window_actions.get(action)
            if callback is not None:
                try:
                    callback()
                except Exception as exc:
                    self._emit("error", title="窗口操作失败", message=str(exc))
                    return {"ok": False, "error": str(exc), "state": self.get_state()}
            return {"ok": True, "state": self.get_state()}

    def _on_due_task(self, task: Mapping[str, Any], _task_index: int, scheduled_at: datetime) -> None:
        with self._lock:
            if not self.running or self.playback is None:
                return
            result = self.playback.start_playlist(task, started_at=scheduled_at)
            if result.get("event") == "expired":
                self._set_status(result.get("message", "任务已过期"), "warning", toast=True)
            else:
                self._set_status(
                    result.get("message", "任务已触发"),
                    "playing" if result.get("ok") else "danger",
                    toast=True,
                )

    def _on_scheduler_tick(self, now: datetime) -> None:
        with self._lock:
            if not self.running or self.playback is None:
                return
            result = self.playback.tick(now)
            if result is not None:
                tone = "playing" if result.get("ok") else "danger"
                if result.get("event") == "ended":
                    tone = "warning"
                self._set_status(str(result.get("message", "")), tone, toast=True)
            # The clock is part of state; poll-driven web UIs fetch it each tick.
            self._emit("clock", clock=now.strftime("%H:%M:%S"))

    def shutdown(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._closing = True
            self.running = False
        if self.scheduler is not None:
            try:
                self.scheduler.stop()
            except Exception:
                pass
        if self.playback is not None:
            try:
                self.playback.shutdown()
            except Exception:
                pass


__all__ = [
    "ALLOWED_EXTENSIONS",
    "CONFIG_FILE",
    "MUSIC_DIRECTORIES",
    "PlaybackEngine",
    "MusicPlayerController",
    "SchedulerService",
    "SCHEDULE_GRACE_SECONDS",
    "TaskStore",
    "calculate_task_end_at",
    "make_portable_music_path",
    "resolve_music_path",
]
