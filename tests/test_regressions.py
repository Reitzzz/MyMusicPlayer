import copy
import json
import os
import queue
import sys
import threading
import unittest
import uuid
from datetime import datetime as RealDateTime
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest import mock

import music


TEST_TEMP_DIR = Path(__file__).parent / ".tmp"


class FakeLabel:
    def __init__(self):
        self.options = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)


class FakeButton(FakeLabel):
    def __init__(self, parent=None, *args, **kwargs):
        super().__init__()
        self.parent = parent
        self.options.update(kwargs)
        if parent is not None and hasattr(parent, "children"):
            parent.children.append(self)

    def pack(self, *args, **kwargs):
        return self

    def grid(self, *args, **kwargs):
        return self

    def configure(self, **kwargs):
        if self.parent is not None and not getattr(self.parent, "alive", True):
            raise RuntimeError("widget has been destroyed")
        super().configure(**kwargs)


class IsolatedTasksFile:
    def __init__(self):
        token = uuid.uuid4().hex
        self.path = TEST_TEMP_DIR / f"tasks-{token}.json"
        self.patchers = [
            mock.patch.object(music, "TASKS_FILE", str(self.path)),
            mock.patch.object(music, "TASKS_PATH", self.path, create=True),
        ]

    def __enter__(self):
        for patcher in self.patchers:
            patcher.start()
        return self.path

    def __exit__(self, exc_type, exc_value, traceback):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.path.unlink(missing_ok=True)
        self.path.with_name(f"{self.path.name}.tmp").unlink(missing_ok=True)
        for backup in TEST_TEMP_DIR.glob(f"{self.path.stem}.corrupt-*{self.path.suffix}"):
            backup.unlink(missing_ok=True)


def bind_app_methods(app, *names):
    for name in names:
        method = getattr(music.MusicSchedulerApp, name, None)
        if method is not None:
            setattr(app, name, MethodType(method, app))
    return app


def valid_task(name="task", time_value="12:00:00", weekdays=None):
    return {
        "time": time_value,
        "mode": "song",
        "end_time": "",
        "end_next_day": False,
        "files": ["mp3/example.mp3"],
        "name": name,
        "weekdays": [0] if weekdays is None else weekdays,
        "enabled": True,
    }


def storage_app():
    app = SimpleNamespace(
        tasks=[],
        _tasks_load_state="pending",
        _tasks_backup_path=None,
        create_task_btn=FakeButton(),
        status_label=FakeLabel(),
        schedule_scroll=SimpleNamespace(winfo_children=lambda: []),
        alerts=[],
    )
    app.refresh_task_list = lambda: None
    app.show_error_alert = lambda *args, **kwargs: app.alerts.append((args, kwargs))
    app._is_valid_clock = music.MusicSchedulerApp._is_valid_clock
    return bind_app_methods(
        app,
        "save_tasks",
        "load_tasks",
        "_save_tasks_or_restore",
        "_set_tasks_load_state",
        "_tasks_are_writable",
        "_warn_tasks_read_only",
        "_backup_tasks_file",
        "_sanitize_task",
    )


class TaskStorageRegressionTests(unittest.TestCase):
    def test_pending_state_blocks_save_without_touching_original(self):
        with IsolatedTasksFile() as path:
            original = json.dumps([valid_task("valuable")], ensure_ascii=False)
            path.write_text(original, encoding="utf-8")
            app = storage_app()
            app.tasks = [valid_task("replacement")]

            self.assertFalse(app.save_tasks())
            self.assertEqual(original, path.read_text(encoding="utf-8"))

    def test_utf8_bom_loads_without_backup(self):
        with IsolatedTasksFile() as path:
            payload = json.dumps([valid_task("bom")], ensure_ascii=False)
            path.write_text("\ufeff" + payload, encoding="utf-8")
            app = storage_app()

            app.load_tasks()

            self.assertEqual("ready", app._tasks_load_state)
            self.assertEqual(["bom"], [task["name"] for task in app.tasks])
            self.assertEqual([], list(TEST_TEMP_DIR.glob(f"{path.stem}.corrupt-*{path.suffix}")))
            app.tasks.append(valid_task("new"))
            self.assertTrue(app.save_tasks())
            saved_names = [
                task["name"]
                for task in json.loads(path.read_text(encoding="utf-8"))
            ]
            self.assertEqual(["bom", "new"], saved_names)

    def test_missing_file_enters_ready_state(self):
        with IsolatedTasksFile():
            app = storage_app()

            app.load_tasks()

            self.assertEqual("ready", app._tasks_load_state)
            self.assertEqual([], app.tasks)
            self.assertEqual("normal", app.create_task_btn.options.get("state"))

    def test_truncated_json_is_backed_up_and_locked_read_only(self):
        with IsolatedTasksFile() as path:
            original = '[{"name":"valuable"'
            path.write_text(original, encoding="utf-8")
            app = storage_app()

            app.load_tasks()
            backups = list(TEST_TEMP_DIR.glob(f"{path.stem}.corrupt-*{path.suffix}"))

            self.assertEqual("failed", app._tasks_load_state)
            self.assertEqual([], app.tasks)
            self.assertEqual(1, len(backups))
            self.assertEqual(original.encode("utf-8"), backups[0].read_bytes())
            app.tasks = [valid_task("replacement")]
            self.assertFalse(app.save_tasks())
            self.assertEqual(original, path.read_text(encoding="utf-8"))

    def test_non_list_top_level_is_a_whole_file_failure(self):
        with IsolatedTasksFile() as path:
            original = json.dumps({"tasks": [valid_task()]}, ensure_ascii=False)
            path.write_text(original, encoding="utf-8")
            app = storage_app()

            app.load_tasks()

            self.assertEqual("failed", app._tasks_load_state)
            self.assertEqual([], app.tasks)
            backups = list(TEST_TEMP_DIR.glob(f"{path.stem}.corrupt-*{path.suffix}"))
            self.assertEqual(1, len(backups))
            self.assertEqual(original.encode("utf-8"), backups[0].read_bytes())

    def test_partial_invalid_data_is_backed_up_and_valid_tasks_remain_writable(self):
        with IsolatedTasksFile() as path:
            payload = [
                {**valid_task("valid"), "future_field": {"keep": True}},
                {"name": "missing time", "files": [], "weekdays": [0]},
                {
                    "time": "13:00:00",
                    "files": ["mp3/ok.mp3", 123],
                    "weekdays": [0, 7, 0, "1"],
                    "mode": "unknown",
                    "enabled": "false",
                    "end_next_day": "false",
                },
            ]
            original = json.dumps(payload, ensure_ascii=False)
            path.write_text(original, encoding="utf-8")
            app = storage_app()

            app.load_tasks()

            self.assertEqual("ready", app._tasks_load_state)
            self.assertEqual(2, len(app.tasks))
            self.assertEqual("valid", app.tasks[0]["name"])
            self.assertEqual({"keep": True}, app.tasks[0]["future_field"])
            repaired = app.tasks[1]
            self.assertEqual("未命名任务", repaired["name"])
            self.assertEqual([0], repaired["weekdays"])
            self.assertEqual(["mp3/ok.mp3"], repaired["files"])
            self.assertEqual("song", repaired["mode"])
            self.assertFalse(repaired["enabled"])
            self.assertFalse(repaired["end_next_day"])
            backups = list(TEST_TEMP_DIR.glob(f"{path.stem}.corrupt-*{path.suffix}"))
            self.assertEqual(1, len(backups))
            self.assertEqual(original.encode("utf-8"), backups[0].read_bytes())
            self.assertTrue(app.save_tasks())

    def test_invalid_duration_end_time_is_skipped(self):
        with IsolatedTasksFile() as path:
            payload = [{**valid_task(), "mode": "duration", "end_time": "not-a-time"}]
            path.write_text(json.dumps(payload), encoding="utf-8")
            app = storage_app()

            app.load_tasks()

            self.assertEqual("ready", app._tasks_load_state)
            self.assertEqual([], app.tasks)
            self.assertEqual(1, len(list(TEST_TEMP_DIR.glob(f"{path.stem}.corrupt-*{path.suffix}"))))

    def test_lossless_normalization_does_not_backup_or_warn(self):
        # 旧格式任务：程序目录内绝对路径 + 缺 enabled 字段。便携化和补默认值
        # 属于无损归一化，不应被判为损坏数据、不应生成备份或弹窗。
        with IsolatedTasksFile() as path:
            abs_file = str((music.APPLICATION_DIR / "mp3" / "legacy.mp3"))
            legacy = {
                "time": "08:00:00",
                "mode": "song",
                "end_time": "",
                "end_next_day": False,
                "files": [abs_file],
                "name": "legacy",
            }
            path.write_text(json.dumps([legacy], ensure_ascii=False), encoding="utf-8")
            app = storage_app()

            app.load_tasks()
            first_backups = list(TEST_TEMP_DIR.glob(f"{path.stem}.corrupt-*{path.suffix}"))

            self.assertEqual("ready", app._tasks_load_state)
            self.assertEqual(1, len(app.tasks))
            self.assertEqual(["mp3/legacy.mp3"], app.tasks[0]["files"])
            self.assertTrue(app.tasks[0]["enabled"])
            self.assertEqual([], first_backups)
            self.assertEqual([], app.alerts)

            # 第二次启动（用户从未保存）同样不得再生成备份。
            app.tasks = []
            app.load_tasks()
            self.assertEqual([], list(TEST_TEMP_DIR.glob(f"{path.stem}.corrupt-*{path.suffix}")))
            self.assertEqual([], app.alerts)

    def test_lossy_field_still_backs_up(self):
        # 显式存在但非法的值（weekdays 含越界元素）属于有损修正，仍需备份告警。
        with IsolatedTasksFile() as path:
            payload = [{**valid_task("lossy"), "weekdays": [0, 9]}]
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            app = storage_app()

            app.load_tasks()

            self.assertEqual("ready", app._tasks_load_state)
            self.assertEqual([0], app.tasks[0]["weekdays"])
            self.assertEqual(1, len(list(TEST_TEMP_DIR.glob(f"{path.stem}.corrupt-*{path.suffix}"))))
            self.assertEqual(1, len(app.alerts))

    def test_backup_failure_keeps_partially_valid_data_read_only(self):
        with IsolatedTasksFile() as path:
            payload = [valid_task("valid"), {"name": "invalid"}]
            original = json.dumps(payload, ensure_ascii=False)
            path.write_text(original, encoding="utf-8")
            app = storage_app()
            app._backup_tasks_file = mock.Mock(side_effect=OSError("backup denied"))

            app.load_tasks()

            self.assertEqual("failed", app._tasks_load_state)
            self.assertEqual(["valid"], [task["name"] for task in app.tasks])
            self.assertFalse(app.save_tasks())
            self.assertEqual(original, path.read_text(encoding="utf-8"))


class Clock(RealDateTime):
    current = RealDateTime(2026, 8, 24, 0, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.current


def scheduler_app(tasks):
    app = SimpleNamespace(
        tasks=copy.deepcopy(tasks),
        running=True,
        last_tick_dt=None,
        time_label=FakeLabel(),
        status_label=FakeLabel(),
        is_playlist_active=False,
        current_task_mode="song",
        current_task_end_at=None,
        started=[],
        scheduled_callbacks=[],
    )
    app.start_playlist = lambda task: app.started.append(task["name"])
    app.update_top_status = lambda: None
    app.stop_music = lambda: None
    app.play_next_in_queue = lambda: None
    app.after = lambda delay, callback: app.scheduled_callbacks.append((delay, callback))
    app.check_schedule_tick = MethodType(music.MusicSchedulerApp.check_schedule_tick, app)
    return app


def run_tick(app, when):
    Clock.current = when
    with mock.patch.object(music, "datetime", Clock):
        app.check_schedule_tick()


class SchedulerRegressionTests(unittest.TestCase):
    def test_first_tick_still_triggers_current_second(self):
        app = scheduler_app([valid_task("current", weekdays=[0])])

        run_tick(app, RealDateTime(2026, 8, 24, 12, 0, 0, 500000))

        self.assertEqual(["current"], app.started)

    def test_blocking_across_target_second_triggers_once(self):
        for delayed_now in (
            RealDateTime(2026, 8, 24, 12, 0, 1, 100000),
            RealDateTime(2026, 8, 24, 12, 0, 1, 600000),
            RealDateTime(2026, 8, 24, 12, 0, 2, 500000),
        ):
            with self.subTest(delayed_now=delayed_now):
                app = scheduler_app([valid_task("blocked", weekdays=[0])])
                run_tick(app, RealDateTime(2026, 8, 24, 11, 59, 59, 600000))
                run_tick(app, delayed_now)
                run_tick(app, delayed_now)
                self.assertEqual(["blocked"], app.started)

    def test_grace_boundary_is_inclusive_but_older_task_is_skipped(self):
        app = scheduler_app([valid_task("edge", weekdays=[0])])
        run_tick(app, RealDateTime(2026, 8, 24, 11, 59, 59))
        run_tick(app, RealDateTime(2026, 8, 24, 12, 1, 30))
        self.assertEqual(["edge"], app.started)

        stale = scheduler_app([valid_task("stale", weekdays=[0])])
        run_tick(stale, RealDateTime(2026, 8, 24, 11, 59, 59))
        run_tick(stale, RealDateTime(2026, 8, 24, 12, 1, 30, 1))
        self.assertEqual([], stale.started)

    def test_cross_midnight_uses_candidate_date_weekday(self):
        app = scheduler_app([valid_task("monday", "23:59:59", weekdays=[0])])
        run_tick(app, RealDateTime(2026, 8, 24, 23, 59, 58, 900000))
        run_tick(app, RealDateTime(2026, 8, 25, 0, 0, 0, 500000))
        self.assertEqual(["monday"], app.started)

    def test_clock_rollback_resets_baseline_without_triggering(self):
        app = scheduler_app([valid_task("rolled", "12:00:00", weekdays=[0])])
        run_tick(app, RealDateTime(2026, 8, 24, 12, 0, 10))
        app.started.clear()
        run_tick(app, RealDateTime(2026, 8, 24, 11, 59, 59))
        self.assertEqual([], app.started)
        self.assertEqual(RealDateTime(2026, 8, 24, 11, 59, 59), app.last_tick_dt)

    def test_only_latest_due_task_starts_and_ties_choose_last(self):
        app = scheduler_app([
            valid_task("older", "12:00:00", weekdays=[0]),
            valid_task("latest", "12:01:00", weekdays=[0]),
        ])
        run_tick(app, RealDateTime(2026, 8, 24, 11, 59, 59, 500000))
        run_tick(app, RealDateTime(2026, 8, 24, 12, 1, 10))
        self.assertEqual(["latest"], app.started)

        tied = scheduler_app([
            valid_task("first", "12:00:00", weekdays=[0]),
            valid_task("last", "12:00:00", weekdays=[0]),
        ])
        run_tick(tied, RealDateTime(2026, 8, 24, 11, 59, 59, 500000))
        run_tick(tied, RealDateTime(2026, 8, 24, 12, 0, 1))
        self.assertEqual(["last"], tied.started)


class FakeHelpWindow:
    instances = []

    def __init__(self, owner):
        self.owner = owner
        self.alive = True
        self.callbacks = []
        self.protocols = {}
        self.bindings = {}
        self.children = []
        self.focused = False
        FakeHelpWindow.instances.append(self)

    def title(self, *args): pass
    def geometry(self, *args): pass
    def attributes(self, *args): pass
    def lift(self): self.focused = True
    def focus_force(self): self.focused = True
    def winfo_exists(self): return self.alive

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def bind(self, event_name, callback, add=None):
        self.bindings[event_name] = callback

    def after(self, delay, callback):
        self.callbacks.append(callback)

    def destroy(self):
        if not self.alive:
            return
        self.alive = False
        callback = self.bindings.get("<Destroy>")
        if callback is not None:
            callback(SimpleNamespace(widget=self))


class FakeContainer(FakeButton):
    pass


class HelpWindowRegressionTests(unittest.TestCase):
    def setUp(self):
        FakeHelpWindow.instances = []
        self.patches = [
            mock.patch.object(music, "DpiStableToplevel", FakeHelpWindow),
            mock.patch.object(music.ctk, "CTkLabel", FakeButton),
            mock.patch.object(music.ctk, "CTkScrollableFrame", FakeContainer),
            mock.patch.object(music.ctk, "CTkButton", FakeButton),
            mock.patch.object(music.ctk, "CTkFont", lambda *args, **kwargs: None),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def test_forced_help_is_single_instance_and_countdown_survives_reentry(self):
        app = SimpleNamespace(_help_win=None, _help_win_forced=False)

        music.MusicSchedulerApp.show_help_window(app, forced_countdown=True)
        forced = FakeHelpWindow.instances[-1]
        music.MusicSchedulerApp.show_help_window(app, forced_countdown=False)

        self.assertEqual(1, len(FakeHelpWindow.instances))
        self.assertIs(forced, app._help_win)
        self.assertTrue(forced.focused)
        for _ in range(10):
            forced.callbacks.pop(0)()
        button = next(
            widget for widget in getattr(forced, "children", [])
            if widget.options.get("text", "").startswith(("请阅读", "我知道了"))
        ) if getattr(forced, "children", []) else None
        if button is not None:
            self.assertEqual("normal", button.options.get("state"))
        forced.protocols["WM_DELETE_WINDOW"]()
        self.assertFalse(forced.alive)
        self.assertIsNone(app._help_win)

    def test_forced_request_replaces_existing_normal_window(self):
        app = SimpleNamespace(_help_win=None, _help_win_forced=False)
        music.MusicSchedulerApp.show_help_window(app, forced_countdown=False)
        normal = FakeHelpWindow.instances[-1]

        music.MusicSchedulerApp.show_help_window(app, forced_countdown=True)

        self.assertFalse(normal.alive)
        self.assertEqual(2, len(FakeHelpWindow.instances))
        self.assertIs(FakeHelpWindow.instances[-1], app._help_win)
        self.assertTrue(app._help_win_forced)

    def test_destroyed_forced_window_makes_pending_callback_a_noop(self):
        app = SimpleNamespace(_help_win=None, _help_win_forced=False)
        music.MusicSchedulerApp.show_help_window(app, forced_countdown=True)
        forced = FakeHelpWindow.instances[-1]
        callback = forced.callbacks.pop(0)
        forced.destroy()

        callback()

        self.assertIsNone(app._help_win)


class ImmediateThread:
    def __init__(self, target, daemon=False):
        self.target = target
        self.daemon = daemon
        self.started = False
        self.joined = False

    def start(self):
        self.started = True
        self.target()

    def is_alive(self):
        return False

    def join(self, timeout=None):
        self.joined = True


def tray_app(started_silent=True):
    app = SimpleNamespace(
        tray_icon=None,
        tray_thread=None,
        _tray_unavailable=False,
        _tray_alert_shown=False,
        running=True,
        _closing=False,
        ui_event_queue=queue.Queue(),
        status_label=FakeLabel(),
        shown=0,
        lifted=0,
        withdrawn=0,
        destroyed=0,
        scheduled=[],
    )
    app.deiconify = lambda: setattr(app, "shown", app.shown + 1)
    app.lift = lambda: setattr(app, "lifted", app.lifted + 1)
    app.withdraw = lambda: setattr(app, "withdrawn", app.withdrawn + 1)
    app.destroy = lambda: setattr(app, "destroyed", app.destroyed + 1)
    app.after = lambda delay, callback: app.scheduled.append((delay, callback))
    app.create_tray_image = lambda: object()
    app.show_window_from_tray = lambda *args: None
    app.quit_app_from_tray = lambda *args: None
    app.alerts = []
    app.show_error_alert = lambda *args, **kwargs: app.alerts.append((args, kwargs))
    return bind_app_methods(
        app,
        "setup_tray_icon",
        "_run_tray_icon",
        "_handle_tray_failure",
        "_announce_tray_unavailable",
        "process_ui_events",
        "on_close",
        "on_real_close",
    )


def flush_scheduled(app):
    """执行并清空所有延后回调，模拟 Tk 的 after 队列。"""
    pending = list(app.scheduled)
    app.scheduled.clear()
    for _delay, callback in pending:
        callback()


class StartupTrayRegressionTests(unittest.TestCase):
    def test_source_startup_command_uses_module_path_not_argv(self):
        original_cwd = Path.cwd()
        original_argv0 = sys.argv[0]
        try:
            os.chdir(Path(music.__file__).parent)
            sys.argv[0] = os.path.join("..", "wrong", "music.py")
            command = music.MusicSchedulerApp.get_startup_command(SimpleNamespace())
        finally:
            sys.argv[0] = original_argv0
            os.chdir(original_cwd)

        self.assertIn(f'"{Path(music.__file__).resolve()}"', command)

    def test_synchronous_tray_failure_degrades_and_shows_silent_window(self):
        app = tray_app(started_silent=True)
        with mock.patch.object(music.pystray, "Icon", side_effect=RuntimeError("tray unavailable")):
            app.setup_tray_icon()

        self.assertIsNone(app.tray_icon)
        self.assertIsNone(app.tray_thread)
        self.assertEqual(1, app.shown)
        # 提示被延后，避免被启动阶段的 load_tasks 状态覆盖。
        self.assertEqual("", app.status_label.options.get("text", ""))
        flush_scheduled(app)
        self.assertIn("托盘", app.status_label.options.get("text", ""))
        self.assertEqual(1, len(app.alerts))

    def test_tray_notice_survives_later_status_updates_and_alerts_once(self):
        app = tray_app(started_silent=False)
        with mock.patch.object(music.pystray, "Icon", side_effect=RuntimeError("tray unavailable")):
            app.setup_tray_icon()
        # 模拟 load_tasks 在延后提示之前覆盖状态栏。
        app.status_label.configure(text="无历史任务记录", text_color="gray")
        flush_scheduled(app)

        self.assertIn("托盘", app.status_label.options.get("text", ""))
        self.assertEqual(1, len(app.alerts))

        app._announce_tray_unavailable()
        self.assertEqual(1, len(app.alerts))

    def test_asynchronous_tray_failure_is_handled_on_ui_thread(self):
        class BrokenIcon:
            def run(self):
                raise RuntimeError("run failed")

            def stop(self):
                pass

        app = tray_app(started_silent=True)
        with (
            mock.patch.object(music.pystray, "Icon", return_value=BrokenIcon()),
            mock.patch.object(music.threading, "Thread", ImmediateThread),
        ):
            app.setup_tray_icon()
        self.assertEqual(0, app.shown)

        app.process_ui_events()

        self.assertIsNone(app.tray_icon)
        self.assertEqual(1, app.shown)
        flush_scheduled(app)
        self.assertIn("托盘", app.status_label.options.get("text", ""))

    def test_unexpected_normal_tray_return_is_also_a_failure(self):
        class ReturningIcon:
            def run(self):
                return

            def stop(self):
                pass

        app = tray_app(started_silent=False)
        with (
            mock.patch.object(music.pystray, "Icon", return_value=ReturningIcon()),
            mock.patch.object(music.threading, "Thread", ImmediateThread),
        ):
            app.setup_tray_icon()

        app.process_ui_events()

        self.assertIsNone(app.tray_icon)
        self.assertEqual(1, app.shown)

    def test_close_without_tray_performs_real_cleanup(self):
        app = tray_app(started_silent=False)
        with mock.patch.object(music.pygame.mixer, "quit"):
            app.on_close()

        self.assertEqual(0, app.withdrawn)
        self.assertEqual(1, app.destroyed)

    def test_real_close_stops_tray_and_joins_live_thread(self):
        class Icon:
            stopped = False

            def stop(self):
                self.stopped = True

        class LiveThread:
            joined = False

            def is_alive(self):
                return True

            def join(self, timeout=None):
                self.joined = timeout == 2.0

        app = tray_app(started_silent=False)
        icon = Icon()
        tray_thread = LiveThread()
        app.tray_icon = icon
        app.tray_thread = tray_thread
        with mock.patch.object(music.pygame.mixer, "quit"):
            app.on_real_close()

        self.assertTrue(icon.stopped)
        self.assertTrue(tray_thread.joined)
        self.assertEqual(1, app.destroyed)


class FakeFrame(FakeButton):
    pass


class RefreshDefenseRegressionTests(unittest.TestCase):
    def test_refresh_does_not_raise_for_residual_bad_task(self):
        scroll = SimpleNamespace(children=[], winfo_children=lambda: [])
        app = SimpleNamespace(
            tasks=[{"name": "bad", "weekdays": [0, 99], "files": []}],
            schedule_scroll=scroll,
            _tasks_load_state="failed",
            update_top_status=lambda: None,
        )
        with (
            mock.patch.object(music.ctk, "CTkFrame", FakeFrame),
            mock.patch.object(music.ctk, "CTkLabel", FakeButton),
            mock.patch.object(music.ctk, "CTkButton", FakeButton),
            mock.patch.object(music.ctk, "CTkSwitch", FakeButton),
            mock.patch.object(music.ctk, "IntVar", lambda value=0: SimpleNamespace(get=lambda: value)),
            mock.patch.object(music, "destroy_widget_tree", lambda widget: None),
        ):
            music.MusicSchedulerApp.refresh_task_list(app)


if __name__ == "__main__":
    unittest.main()
