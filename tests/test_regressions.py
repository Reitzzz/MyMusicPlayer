"""Regression coverage for the webview application's non-GUI core."""

from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
import unittest
import wave
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from player_core import (
    MusicPlayerController,
    PlaybackEngine,
    SchedulerService,
    TaskStore,
    calculate_task_end_at,
)
from web_app import WebApp, WebBridge


@contextmanager
def temporary_workspace():
    """Use an inherited-ACL repository folder instead of restricted AppData temp."""
    root = Path(__file__).resolve().parents[1] / f".test-runtime-{uuid4().hex}"
    root.mkdir()
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def valid_task(name: str = "task", clock: str = "12:00:00", weekdays=None) -> dict:
    return {
        "time": clock,
        "mode": "song",
        "end_time": "",
        "end_next_day": False,
        "files": ["mp3/ok.mp3"],
        "name": name,
        "weekdays": [0, 1, 2, 3, 4, 5, 6] if weekdays is None else weekdays,
        "enabled": True,
    }


class TaskStoreRegressionTests(unittest.TestCase):
    def test_corrupt_source_is_backed_up_and_read_only_when_json_invalid(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            source = root / "tasks.json"
            original = b"{not json"
            source.write_bytes(original)
            store = TaskStore(root)

            self.assertEqual([], store.load())
            self.assertEqual("failed", store.load_state)
            self.assertFalse(store.save([]))
            backups = list(root.glob("tasks.corrupt-*.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual(original, backups[0].read_bytes())
            self.assertEqual(original, source.read_bytes())

    def test_lossless_legacy_path_normalization_does_not_backup(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            legacy_path = str(root / "mp3" / "legacy.mp3")
            (root / "tasks.json").write_text(
                json.dumps({"bad": "top-level"}), encoding="utf-8"
            )
            # A list is required; rewrite the test source after checking the
            # store's strict top-level validation in the corrupt test above.
            (root / "tasks.json").write_text(
                json.dumps(
                    [
                        {
                            "time": "08:00:00",
                            "mode": "song",
                            "end_time": "",
                            "end_next_day": False,
                            "files": [legacy_path],
                            "name": " legacy ",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            store = TaskStore(root)
            tasks = store.load()

            self.assertEqual("ready", store.load_state)
            self.assertEqual(["mp3/legacy.mp3"], tasks[0]["files"])
            self.assertEqual("legacy", tasks[0]["name"])
            self.assertTrue(tasks[0]["enabled"])
            self.assertEqual([], list(root.glob("tasks.corrupt-*.json")))

    def test_lossy_task_field_creates_backup_but_keeps_valid_data(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "tasks.json").write_text(
                json.dumps([valid_task(weekdays=[0, 9])]), encoding="utf-8"
            )
            store = TaskStore(root)
            tasks = store.load()

            self.assertEqual("ready", store.load_state)
            self.assertEqual([0], tasks[0]["weekdays"])
            self.assertEqual(1, len(list(root.glob("tasks.corrupt-*.json"))))

    def test_save_is_atomic_and_keeps_portable_task_shape(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            store = TaskStore(root)
            store.load()
            task = valid_task()
            task["files"] = [str(root / "mp3" / "ok.mp3")]
            (root / "mp3").mkdir()
            self.assertTrue(store.save([task]))
            saved = json.loads((root / "tasks.json").read_text(encoding="utf-8"))
            self.assertEqual("mp3/ok.mp3", saved[0]["files"][0])
            self.assertFalse((root / "tasks.json.tmp").exists())


class SchedulerRegressionTests(unittest.TestCase):
    def make_service(self, tasks):
        started = []
        service = SchedulerService(
            lambda: tasks,
            lambda task, index, scheduled_at=None: started.append((task["name"], index)),
        )
        return service, started

    def test_first_tick_triggers_current_second(self):
        service, started = self.make_service([valid_task("current", "12:00:00", [0])])
        service.tick(datetime(2026, 8, 24, 12, 0, 0, 500000))
        self.assertEqual([("current", 0)], started)

    def test_grace_catches_delayed_tick_once_and_rollback_resets_baseline(self):
        service, started = self.make_service([valid_task("late", "12:00:00", [0])])
        service.tick(datetime(2026, 8, 24, 11, 59, 59, 600000))
        service.tick(datetime(2026, 8, 24, 12, 00, 1, 600000))
        service.tick(datetime(2026, 8, 24, 12, 00, 1, 600000))
        self.assertEqual([("late", 0)], started)
        service.tick(datetime(2026, 8, 24, 11, 59, 59))
        self.assertEqual([("late", 0)], started)
        self.assertEqual(datetime(2026, 8, 24, 11, 59, 59), service.last_tick_dt)

    def test_latest_due_task_wins_and_ties_use_last_task(self):
        tasks = [
            valid_task("old", "12:00:00", [0]),
            valid_task("latest", "12:01:00", [0]),
        ]
        service, started = self.make_service(tasks)
        service.tick(datetime(2026, 8, 24, 11, 59, 59, 500000))
        service.tick(datetime(2026, 8, 24, 12, 1, 10))
        self.assertEqual([("latest", 1)], started)

        tied_tasks = [valid_task("first", weekdays=[0]), valid_task("last", weekdays=[0])]
        tied, tied_started = self.make_service(tied_tasks)
        tied.tick(datetime(2026, 8, 24, 11, 59, 59, 500000))
        tied.tick(datetime(2026, 8, 24, 12, 0, 1))
        self.assertEqual([("last", 1)], tied_started)


class ControllerRegressionTests(unittest.TestCase):
    def test_bridge_mutations_return_authoritative_state(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            (root / "mp3" / "ok.mp3").write_bytes(b"not audio")
            controller = MusicPlayerController(root, initialize_audio=False)
            payload = {
                "task": {
                    "time": "09:30",
                    "mode": "song",
                    "files": ["mp3/ok.mp3"],
                    "weekdays": [0, 2],
                    "name": " Morning ",
                },
                "expected_revision": 0,
            }
            result = controller.save_task(payload)
            self.assertTrue(result["ok"])
            self.assertEqual("09:30:00", result["state"]["tasks"][0]["time"])
            self.assertEqual("Morning", result["state"]["tasks"][0]["name"])
            self.assertTrue(controller.set_task_enabled(0, False, expected_revision=1)["ok"])
            self.assertFalse(controller.get_state()["tasks"][0]["enabled"])
            self.assertTrue(controller.delete_task(0, expected_revision=2)["ok"])
            self.assertEqual([], controller.get_state()["tasks"])
            controller.shutdown()

    def test_invalid_duration_payload_is_rejected_without_write(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            controller = MusicPlayerController(root, initialize_audio=False)

            # 1. Invalid time string
            res1 = controller.save_task(
                {"time": "09:30", "mode": "duration", "end_time": "bad", "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x", "expected_revision": 0}
            )
            self.assertFalse(res1["ok"])

            # 2. Empty end_time
            res2 = controller.save_task(
                {"time": "09:00", "mode": "duration", "end_time": "", "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x", "expected_revision": 0}
            )
            self.assertFalse(res2["ok"])

            # 3. Omitted end_time
            res3 = controller.save_task(
                {"time": "10:00", "mode": "duration", "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x", "expected_revision": 0}
            )
            self.assertFalse(res3["ok"])

            # 4. Backward end_time without next-day
            res4 = controller.save_task(
                {"time": "10:00", "mode": "duration", "end_time": "09:00", "end_next_day": False, "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x", "expected_revision": 0}
            )
            self.assertFalse(res4["ok"])

            # 5. Equal end_time and start_time without next-day
            res5 = controller.save_task(
                {"time": "10:00", "mode": "duration", "end_time": "10:00", "end_next_day": False, "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x", "expected_revision": 0}
            )
            self.assertFalse(res5["ok"])

            self.assertEqual([], controller.get_state()["tasks"])
            controller.shutdown()

    def test_valid_cross_midnight_duration_task_is_saved(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            controller = MusicPlayerController(root, initialize_audio=False)
            result = controller.save_task(
                {
                    "time": "23:30",
                    "mode": "duration",
                    "end_time": "00:30",
                    "end_next_day": True,
                    "files": ["mp3/ok.mp3"],
                    "weekdays": [0],
                    "name": "midnight",
                    "expected_revision": 0,
                }
            )
            self.assertTrue(result["ok"])
            tasks = controller.get_state()["tasks"]
            self.assertEqual(1, len(tasks))
            self.assertEqual("00:30:00", tasks[0]["end_time"])
            self.assertTrue(tasks[0]["end_next_day"])
            controller.shutdown()

    def test_next_run_sends_raw_weekdays_for_the_page_to_format(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            controller = MusicPlayerController(root, initialize_audio=False)
            self.assertTrue(
                controller.save_task(
                    {
                        "time": "09:30",
                        "mode": "song",
                        "files": ["mp3/ok.mp3"],
                        "weekdays": [0, 1, 2, 3, 4, 5, 6],
                        "name": "daily",
                        "expected_revision": 0,
                    }
                )["ok"]
            )
            next_run = controller.get_state()["next_run"]
            self.assertEqual([0, 1, 2, 3, 4, 5, 6], next_run["weekdays"])
            # Weekday wording lives only in ui/app.js's formatWeekdays.
            self.assertNotIn("weekdays_label", next_run)
            controller.shutdown()


class _FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeWindow:
    def __init__(self):
        self.events = type(
            "Events",
            (),
            {"loaded": _FakeEvent(), "closing": _FakeEvent()},
        )()


class _FakeWebview:
    def __init__(self):
        self.window = _FakeWindow()
        self.options = None

    def create_window(self, _title, **options):
        self.options = options
        return self.window


class WebAppLifecycleRegressionTests(unittest.TestCase):
    @staticmethod
    def make_shell() -> WebApp:
        shell = object.__new__(WebApp)
        shell._lock = threading.RLock()
        shell._closing = False
        shell._tray_unavailable = False
        shell._tray_alert_sent = False
        shell.silent = False
        shell._native_action_queue = queue.Queue()
        shell._native_stop_event = threading.Event()
        shell._tray_icon = None
        shell._tray_thread = None
        shell._native_dispatcher_thread = None
        return shell

    def test_asynchronous_tray_failure_is_handled_on_ui_thread(self):
        # Case 1: icon.run() raises exception in silent mode
        shell = self.make_shell()
        shell.silent = True
        shell.controller = type(
            "Controller",
            (),
            {"status_message": "", "status_tone": "", "_emit": lambda *a, **k: None},
        )()

        class _BrokenIcon:
            def run(self):
                raise RuntimeError("tray crash")

        shell._tray_icon = _BrokenIcon()
        shell._run_tray()
        self.assertTrue(shell._tray_unavailable)
        # Verify Option B: asynchronous failure does not force show in silent mode
        self.assertTrue(shell._native_action_queue.empty())
        # Verify fallback: closing the window queues close action
        self.assertFalse(shell.on_closing())
        self.assertEqual("close", shell._native_action_queue.get_nowait())

        # Case 2: icon.run() returns unexpectedly without _closing
        shell2 = self.make_shell()
        shell2.silent = False
        shell2.controller = type(
            "Controller",
            (),
            {"status_message": "", "status_tone": "", "_emit": lambda *a, **k: None},
        )()

        class _ExitedIcon:
            def run(self):
                return

        shell2._tray_icon = _ExitedIcon()
        shell2._run_tray()
        self.assertTrue(shell2._tray_unavailable)
        self.assertFalse(shell2.on_closing())
        self.assertEqual("close", shell2._native_action_queue.get_nowait())

    def test_native_close_is_cancelled_and_serialized_to_tray_action(self):
        shell = self.make_shell()
        self.assertFalse(shell.on_closing())
        self.assertEqual("close", shell._native_action_queue.get_nowait())

        shell._tray_unavailable = True
        self.assertFalse(shell.on_closing())
        self.assertEqual("close", shell._native_action_queue.get_nowait())

        shell._closing = True
        self.assertTrue(shell.on_closing())

    def test_js_api_does_not_expose_the_native_shell_object_graph(self):
        bridge = WebBridge(object())
        self.assertEqual(["_app"], list(vars(bridge)))

    def test_tray_callbacks_do_not_depend_on_javascript_polling(self):
        shell = self.make_shell()
        shell._tray_show()
        shell._tray_exit()
        self.assertEqual("show", shell._native_action_queue.get_nowait())
        self.assertEqual("exit", shell._native_action_queue.get_nowait())

    def test_window_is_fixed_frameless_and_binds_close_handler(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "ui").mkdir()
            (root / "ui" / "index.html").write_text("<!doctype html>", encoding="utf-8")
            fake_webview = _FakeWebview()
            shell = object.__new__(WebApp)
            shell.application_dir = root
            shell.bridge = object()
            shell.silent = True
            shell.window = None
            shell._on_loaded = lambda: None
            shell.on_closing = lambda: False

            with patch("web_app.webview", fake_webview):
                window = shell.create_window()

            self.assertIs(window, fake_webview.window)
            self.assertFalse(fake_webview.options["resizable"])
            self.assertTrue(fake_webview.options["frameless"])
            self.assertFalse(fake_webview.options["easy_drag"])
            self.assertTrue(fake_webview.options["hidden"])
            self.assertEqual(1, len(window.events.loaded.handlers))
            self.assertEqual(1, len(window.events.closing.handlers))

    def test_ui_index_resolution_falls_back_to_bundle_for_onefile(self):
        with temporary_workspace() as app_dir, temporary_workspace() as bundle_dir:
            app_root = Path(app_dir)
            bundle_root = Path(bundle_dir)
            (bundle_root / "ui").mkdir()
            bundled_html = bundle_root / "ui" / "index.html"
            bundled_html.write_text("<!doctype html><!-- bundled -->", encoding="utf-8")

            shell = object.__new__(WebApp)
            shell.application_dir = app_root

            with patch("web_app.sys._MEIPASS", str(bundle_root), create=True):
                resolved = shell._resolve_ui_index()
                self.assertEqual(bundled_html.resolve(), resolved.resolve())


class RevisionControlRegressionTests(unittest.TestCase):
    def test_expected_revision_enforced_on_mutations(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            (root / "mp3" / "ok.mp3").write_bytes(b"not audio")
            controller = MusicPlayerController(root, initialize_audio=False)

            # 1. Missing expected_revision
            res_missing = controller.save_task({
                "time": "10:00:00",
                "mode": "song",
                "files": ["mp3/ok.mp3"],
                "weekdays": [0],
                "name": "test",
            })
            self.assertFalse(res_missing["ok"])
            self.assertIn("版本", res_missing["error"])

            # 2. Bool or non-int expected_revision
            res_bool = controller.save_task({
                "time": "10:00:00",
                "mode": "song",
                "files": ["mp3/ok.mp3"],
                "weekdays": [0],
                "name": "test",
                "expected_revision": True,
            })
            self.assertFalse(res_bool["ok"])
            self.assertIn("版本", res_bool["error"])

            # 3. Mismatched revision
            res_mismatch = controller.save_task({
                "time": "10:00:00",
                "mode": "song",
                "files": ["mp3/ok.mp3"],
                "weekdays": [0],
                "name": "test",
                "expected_revision": 999,
            })
            self.assertFalse(res_mismatch["ok"])
            self.assertEqual("任务列表已更新，请重新操作", res_mismatch["error"])

            # 4. Correct revision saves and increments revision
            self.assertEqual(0, controller.tasks_revision)
            res_ok = controller.save_task({
                "time": "10:00:00",
                "mode": "song",
                "files": ["mp3/ok.mp3"],
                "weekdays": [0],
                "name": "test",
                "expected_revision": 0,
            })
            self.assertTrue(res_ok["ok"])
            self.assertEqual(1, controller.tasks_revision)
            self.assertEqual(1, res_ok["state"]["tasks_revision"])

            # 5. Duplicate delete / stale delete is rejected
            res_del1 = controller.delete_task(0, expected_revision=1)
            self.assertTrue(res_del1["ok"])
            self.assertEqual(2, controller.tasks_revision)

            res_del_stale = controller.delete_task(0, expected_revision=1)
            self.assertFalse(res_del_stale["ok"])
            self.assertEqual("任务列表已更新，请重新操作", res_del_stale["error"])

            # 6. Set enabled with stale revision rejected
            res_en_stale = controller.set_task_enabled(0, False, expected_revision=1)
            self.assertFalse(res_en_stale["ok"])
            self.assertEqual("任务列表已更新，请重新操作", res_en_stale["error"])

            controller.shutdown()

    def test_duplicate_delete_preserves_remaining_tasks(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            (root / "mp3" / "a.mp3").write_bytes(b"audio")
            (root / "mp3" / "b.mp3").write_bytes(b"audio")
            (root / "mp3" / "c.mp3").write_bytes(b"audio")
            (root / "tasks.json").write_text(
                json.dumps([
                    valid_task("Task A", "09:00:00", [0]),
                    valid_task("Task B", "10:00:00", [0]),
                    valid_task("Task C", "11:00:00", [0]),
                ]),
                encoding="utf-8",
            )
            controller = MusicPlayerController(root, initialize_audio=False)
            self.assertEqual(0, controller.tasks_revision)
            self.assertEqual(["Task A", "Task B", "Task C"], [t["name"] for t in controller.tasks])

            # First delete of index 0 with revision 0 succeeds
            res1 = controller.delete_task(0, expected_revision=0)
            self.assertTrue(res1["ok"])
            self.assertEqual(1, controller.tasks_revision)
            self.assertEqual(["Task B", "Task C"], [t["name"] for t in controller.tasks])

            # Duplicate delete with old revision 0 is rejected, Task B is PRESERVED!
            res_dup = controller.delete_task(0, expected_revision=0)
            self.assertFalse(res_dup["ok"])
            self.assertEqual("任务列表已更新，请重新操作", res_dup["error"])
            self.assertEqual(["Task B", "Task C"], [t["name"] for t in controller.tasks])
            self.assertEqual(1, controller.tasks_revision)

            # Stale delete of index 1 with old revision 0 is rejected, Task C is PRESERVED!
            res_stale = controller.delete_task(1, expected_revision=0)
            self.assertFalse(res_stale["ok"])
            self.assertEqual("任务列表已更新，请重新操作", res_stale["error"])
            self.assertEqual(["Task B", "Task C"], [t["name"] for t in controller.tasks])
            self.assertEqual(1, controller.tasks_revision)

            # Mutation failure does not increment revision
            bad_save = controller.save_task({"time": "invalid", "expected_revision": 1})
            self.assertFalse(bad_save["ok"])
            self.assertEqual(1, controller.tasks_revision)

            controller.shutdown()


class ClockRollbackAndWatermarkRegressionTests(unittest.TestCase):
    def test_watermark_prevents_duplicate_trigger_across_clock_rollback(self):
        invocations = []
        task1 = valid_task("morning", "12:00:00", [0])
        task2 = valid_task("noon", "12:00:02", [0])
        service = SchedulerService(
            lambda: [task1, task2],
            lambda t, idx, scheduled_at: invocations.append((t["name"], idx, scheduled_at)),
        )

        # 1. Tick right before 12:00:00 sets baseline
        service.tick(datetime(2026, 8, 24, 11, 59, 59, 500000))
        self.assertEqual([], invocations)

        # 2. Tick at 12:00:01 triggers morning
        service.tick(datetime(2026, 8, 24, 12, 0, 1, 500000))
        self.assertEqual(1, len(invocations))
        self.assertEqual("morning", invocations[0][0])
        self.assertEqual(datetime(2026, 8, 24, 12, 0, 0), invocations[0][2])

        # 3. System clock rolls back to 11:59:59
        service.tick(datetime(2026, 8, 24, 11, 59, 59, 0))
        self.assertEqual(1, len(invocations))
        self.assertEqual(datetime(2026, 8, 24, 11, 59, 59), service.last_tick_dt)
        self.assertEqual(datetime(2026, 8, 24, 12, 0, 1, 500000), service.evaluated_max_dt)

        # 4. Clock reaches 12:00:01 again - MUST NOT re-trigger because candidate <= watermark
        service.tick(datetime(2026, 8, 24, 12, 0, 1, 500000))
        self.assertEqual(1, len(invocations))

        # 5. Clock advances past watermark to 12:00:02.5 - noon triggers normally!
        service.tick(datetime(2026, 8, 24, 12, 0, 2, 500000))
        self.assertEqual(2, len(invocations))
        self.assertEqual("noon", invocations[1][0])
        self.assertEqual(datetime(2026, 8, 24, 12, 0, 2), invocations[1][2])
        self.assertEqual(datetime(2026, 8, 24, 12, 0, 2, 500000), service.evaluated_max_dt)


class MidnightCatchupAndScheduledAtRegressionTests(unittest.TestCase):
    def test_scheduled_at_passed_and_preserves_cross_midnight_calculation(self):
        invocations = []
        task = {
            "name": "cross_midnight",
            "time": "23:59:00",
            "mode": "duration",
            "end_time": "00:15:00",
            "end_next_day": True,
            "files": ["mp3/ok.mp3"],
            "weekdays": [0],  # Monday
            "enabled": True,
        }
        service = SchedulerService(
            lambda: [task],
            lambda t, idx, scheduled_at: invocations.append((t, scheduled_at)),
        )

        # Baseline Monday 23:58:30
        service.tick(datetime(2026, 8, 24, 23, 58, 30))
        # Delayed tick at Tuesday 00:00:10 (within 90s grace)
        service.tick(datetime(2026, 8, 25, 0, 0, 10))

        self.assertEqual(1, len(invocations))
        t, scheduled_at = invocations[0]
        self.assertEqual(datetime(2026, 8, 24, 23, 59, 0), scheduled_at)

        # calculate_task_end_at with scheduled_at gives Tuesday 00:15:00
        end_at = calculate_task_end_at(t, scheduled_at)
        self.assertEqual(datetime(2026, 8, 25, 0, 15, 0), end_at)

        # At Tuesday 00:00:10, now < end_at (task is still active)
        now = datetime(2026, 8, 25, 0, 0, 10)
        self.assertLess(now, end_at)

    def test_monday_night_duration_task_catchup_and_natural_stop(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            (root / "mp3" / "sound.mp3").write_bytes(b"audio")

            invocations = []
            task = {
                "name": "midnight_task",
                "time": "23:59:30",
                "mode": "duration",
                "end_time": "00:30:00",
                "end_next_day": True,
                "files": ["mp3/sound.mp3"],
                "weekdays": [0],  # Monday
                "enabled": True,
            }
            service = SchedulerService(
                lambda: [task],
                lambda t, idx, scheduled_at: invocations.append((t, scheduled_at)),
            )

            # Baseline at Monday 23:59:00
            service.tick(datetime(2026, 8, 24, 23, 59, 0))
            # Delayed tick at Tuesday 00:00:10 (within 90s grace)
            service.tick(datetime(2026, 8, 25, 0, 0, 10))

            self.assertEqual(1, len(invocations))
            t, scheduled_at = invocations[0]
            self.assertEqual(datetime(2026, 8, 24, 23, 59, 30), scheduled_at)

            # Test PlaybackEngine with this scheduled task
            engine = PlaybackEngine(root, initialize_audio=False)
            engine._play_path = lambda path: (True, "")
            res = engine.start_playlist(
                t,
                started_at=scheduled_at,
                now=datetime(2026, 8, 25, 0, 0, 10),
            )
            self.assertTrue(res["ok"])
            self.assertTrue(engine.is_playlist_active)
            self.assertEqual(datetime(2026, 8, 25, 0, 30, 0), engine.current_task_end_at)

            # Tick before end time (00:29:59) -> stays active
            engine._is_busy_locked = lambda: True
            tick_res = engine.tick(datetime(2026, 8, 25, 0, 29, 59))
            self.assertIsNone(tick_res)
            self.assertTrue(engine.is_playlist_active)

            # Tick at end time (00:30:00) -> ends!
            end_res = engine.tick(datetime(2026, 8, 25, 0, 30, 0))
            self.assertEqual("ended", end_res["event"])
            self.assertFalse(engine.is_playlist_active)
            self.assertIsNone(engine.current_task_end_at)


class ExpiredDurationTaskRegressionTests(unittest.TestCase):
    def test_expired_duration_task_does_not_interrupt_playback(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            engine = PlaybackEngine(root, initialize_audio=False)
            # Simulate an already running track
            engine.is_playlist_active = True
            engine.current_task_name = "active_song"
            engine.current_task_mode = "song"

            # Create a duration task that has already expired
            task = {
                "name": "past_duration",
                "mode": "duration",
                "time": "08:00:00",
                "end_time": "08:30:00",
                "end_next_day": False,
                "files": ["mp3/ok.mp3"],
            }
            # started_at was 08:00:00 today, current time is 09:00:00
            result = engine.start_playlist(
                task,
                started_at=datetime(2026, 8, 24, 8, 0, 0),
                now=datetime(2026, 8, 24, 9, 0, 0),
            )
            self.assertFalse(result["ok"])
            self.assertEqual("expired", result["event"])
            self.assertIn("已超过设定结束时间", result["message"])

            # Verify active playback is untouched
            self.assertTrue(engine.is_playlist_active)
            self.assertEqual("active_song", engine.current_task_name)


class CorruptModeRegressionTests(unittest.TestCase):
    def test_corrupt_mode_sanitization_in_taskstore(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            tasks_file = root / "tasks.json"
            # Write tasks with mode as a list, dict, and int (unhashable/non-string)
            tasks_file.write_text(
                json.dumps([
                    {"time": "10:00:00", "mode": ["unhashable", "list"], "files": ["mp3/ok.mp3"], "name": "bad1"},
                    {"time": "11:00:00", "mode": {"invalid": "dict"}, "files": ["mp3/ok.mp3"], "name": "bad2"},
                    {"time": "12:00:00", "mode": 12345, "files": ["mp3/ok.mp3"], "name": "bad3"},
                ]),
                encoding="utf-8",
            )
            store = TaskStore(root)
            loaded = store.load()
            self.assertEqual("ready", store.load_state)
            self.assertEqual(3, len(loaded))
            for t in loaded:
                self.assertEqual("song", t["mode"])
            # Backup should have been created
            backups = list(root.glob("tasks.corrupt-*.json"))
            self.assertEqual(1, len(backups))

    def test_controller_save_task_rejects_unhashable_mode_cleanly(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            controller = MusicPlayerController(root, initialize_audio=False)
            res = controller.save_task({
                "time": "10:00:00",
                "mode": ["list", "mode"],
                "files": ["mp3/ok.mp3"],
                "name": "bad",
                "expected_revision": 0,
            })
            self.assertFalse(res["ok"])
            self.assertEqual("播放模式无效", res["error"])
            controller.shutdown()

    def test_corrupt_mode_backup_failure_enters_read_only_protection(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            tasks_file = root / "tasks.json"
            tasks_file.write_text(
                json.dumps([
                    {"time": "10:00:00", "mode": ["corrupt", "mode"], "files": ["mp3/ok.mp3"], "name": "bad"},
                ]),
                encoding="utf-8",
            )
            with patch.object(TaskStore, "_backup_tasks_file", side_effect=OSError("disk readonly")):
                controller = MusicPlayerController(root, initialize_audio=False)
                self.assertEqual("failed", controller.store.load_state)
                # Tasks can still be read
                self.assertEqual(1, len(controller.tasks))
                # But writes are rejected with read-only error
                res_save = controller.save_task({
                    "time": "12:00:00",
                    "mode": "song",
                    "files": ["mp3/ok.mp3"],
                    "weekdays": [0],
                    "name": "new",
                    "expected_revision": 0,
                })
                self.assertFalse(res_save["ok"])
                self.assertIn("只读保护", res_save["error"])

                res_del = controller.delete_task(0, expected_revision=0)
                self.assertFalse(res_del["ok"])
                self.assertIn("只读保护", res_del["error"])

                res_en = controller.set_task_enabled(0, False, expected_revision=0)
                self.assertFalse(res_en["ok"])
                self.assertIn("只读保护", res_en["error"])
                controller.shutdown()


class ManualPlaybackAndFileLockingRegressionTests(unittest.TestCase):
    def test_play_manual_cleans_up_on_failure(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            engine = PlaybackEngine(root, initialize_audio=False)
            engine.is_playlist_active = True
            engine.current_task_name = "old_task"

            ok, msg = engine.play_manual("mp3/does_not_exist.mp3")
            self.assertFalse(ok)
            self.assertFalse(engine.is_playlist_active)
            self.assertEqual("", engine.current_task_name)

    def test_audio_file_can_be_deleted_after_playback(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            audio_dir = root / "mp3"
            audio_dir.mkdir()
            wav_file = audio_dir / "test_sound.wav"
            with wave.open(str(wav_file), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(b"\x00\x00" * 4410)

            os.environ["SDL_AUDIODRIVER"] = "dummy"
            engine = PlaybackEngine(root, initialize_audio=True)
            if not engine._mixer_ready:
                self.skipTest("pygame dummy mixer not available")

            ok, _ = engine.play_manual("mp3/test_sound.wav")
            self.assertTrue(ok)
            self.assertTrue(engine.is_playlist_active)
            self.assertEqual(str(wav_file.resolve()), engine.current_track_path)

            # Natural completion: music stops playing, tick evaluates completed
            engine._pygame.mixer.music.stop()
            tick_res = engine.tick()
            self.assertIsNotNone(tick_res)
            self.assertEqual("completed", tick_res["event"])
            self.assertFalse(engine.is_playlist_active)
            self.assertIsNone(engine.current_track_path)

            # Deleting the file on Windows MUST succeed (proves file handle was released on natural end)
            wav_file.unlink()
            self.assertFalse(wav_file.exists())
            engine.shutdown()

    def test_manual_playback_failures_clean_up_completely(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            (root / "mp3" / "valid.wav").write_bytes(b"dummy")
            engine = PlaybackEngine(root, initialize_audio=False)

            # 1. Missing file
            engine.is_playlist_active = True
            engine.current_task_name = "prior"
            engine.playlist_queue = ["mp3/prior.mp3"]
            engine.current_task_end_at = datetime.now()
            ok1, msg1 = engine.play_manual("mp3/missing.mp3")
            self.assertFalse(ok1)
            self.assertFalse(engine.is_playlist_active)
            self.assertEqual([], engine.playlist_queue)
            self.assertEqual("", engine.current_task_name)
            self.assertIsNone(engine.current_task_end_at)
            self.assertIsNone(engine.current_track_path)

            # 2. Unsupported format (.m4a)
            engine.is_playlist_active = True
            engine.current_task_name = "prior"
            ok2, msg2 = engine.play_manual("mp3/file.m4a")
            self.assertFalse(ok2)
            self.assertIn("不支持的音频格式", msg2)
            self.assertFalse(engine.is_playlist_active)
            self.assertEqual([], engine.playlist_queue)
            self.assertEqual("", engine.current_task_name)

            # 3. Decoding / load error
            engine.is_playlist_active = True
            engine.current_task_name = "prior"
            with patch.object(engine, "_play_path", return_value=(False, "decoder error")):
                ok3, msg3 = engine.play_manual("mp3/valid.wav")
                self.assertFalse(ok3)
                self.assertIn("decoder error", msg3)
                self.assertFalse(engine.is_playlist_active)
                self.assertEqual([], engine.playlist_queue)
                self.assertEqual("", engine.current_task_name)

    def test_engine_rejects_unsupported_audio_format(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            engine = PlaybackEngine(root, initialize_audio=False)
            ok, msg = engine.play_manual("mp3/audio.m4a")
            self.assertFalse(ok)
            self.assertIn("不支持的音频格式", msg)

    def test_controller_play_track_unsupported_format_stops_prior_playback(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            controller = MusicPlayerController(root, initialize_audio=False)
            try:
                controller.playback.is_playlist_active = True
                controller.playback.current_task_name = "prior_task"
                controller.playback.playlist_queue = ["mp3/prior.mp3"]
                controller.playback.current_track_path = "mp3/prior.mp3"
                controller.playback.current_task_end_at = datetime.now()

                events = []
                controller.event_sink = events.append

                res = controller.play_track("mp3/audio.m4a")

                self.assertFalse(res["ok"])
                self.assertIn("不支持的音频格式: .m4a", res["error"])
                self.assertIn("不支持的音频格式: .m4a", controller.status_message)
                self.assertTrue(any(e.get("type") == "error" and "不支持的音频格式: .m4a" in e.get("message", "") for e in events))
                self.assertFalse(controller.playback.is_playlist_active)
                self.assertEqual([], controller.playback.playlist_queue)
                self.assertEqual("", controller.playback.current_task_name)
                self.assertIsNone(controller.playback.current_task_end_at)
                self.assertIsNone(controller.playback.current_track_path)
                self.assertEqual("danger", controller.status_tone)
                self.assertFalse(res["state"]["playback"]["active"])
            finally:
                controller.shutdown()


class AudioFormatGrandfatheringRegressionTests(unittest.TestCase):
    def test_new_task_rejects_unsupported_audio_format(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            controller = MusicPlayerController(root, initialize_audio=False)
            res = controller.save_task({
                "time": "10:00:00",
                "mode": "song",
                "files": ["mp3/song.m4a"],
                "weekdays": [0],
                "name": "m4a_task",
                "expected_revision": 0,
            })
            self.assertFalse(res["ok"])
            self.assertIn("不支持的音频格式: .m4a", res["error"])
            controller.shutdown()

    def test_grandfathered_existing_task_can_be_saved(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            # Legacy task file with .m4a
            (root / "tasks.json").write_text(
                json.dumps([{
                    "time": "10:00:00",
                    "mode": "song",
                    "files": ["mp3/legacy.m4a"],
                    "weekdays": [0],
                    "name": "legacy_task",
                    "enabled": True,
                }]),
                encoding="utf-8",
            )
            controller = MusicPlayerController(root, initialize_audio=False)
            self.assertEqual(1, len(controller.tasks))
            # Updating the name of the grandfathered task
            res = controller.save_task({
                "index": 0,
                "task": {
                    "time": "10:30:00",
                    "name": "updated_name",
                    "files": ["mp3/legacy.m4a"],
                },
                "expected_revision": 0,
            })
            self.assertTrue(res["ok"])
            self.assertEqual("updated_name", res["task"]["name"])
            self.assertEqual(["mp3/legacy.m4a"], res["task"]["files"])
            controller.shutdown()


class WebAppLifecycleAndHangPreventionRegressionTests(unittest.TestCase):
    def test_shutdown_unstarted_window_does_not_hang(self):
        shell = WebAppLifecycleRegressionTests.make_shell()
        shell.controller = type("Controller", (), {"shutdown": lambda *a, **k: None, "get_state": lambda *a, **k: {}})()
        shell._native_stop_event = threading.Event()

        class _MockWindow:
            def __init__(self):
                self.events = type("Events", (), {"shown": threading.Event()})()  # NOT set
                self.destroyed = False

            def destroy(self):
                self.destroyed = True

        mock_window = _MockWindow()
        shell.window = mock_window
        shell.shutdown()
        self.assertTrue(shell._closing)
        # Verify window.destroy was NOT called because shown was not set
        self.assertFalse(mock_window.destroyed)

    def test_dispatcher_handles_close_action_based_on_tray(self):
        # Case A: tray is unavailable -> close shuts down
        shell = WebAppLifecycleRegressionTests.make_shell()
        shell._tray_unavailable = True
        shell._tray_icon = None
        shell._native_stop_event = threading.Event()
        shutdown_called = []
        shell.shutdown = lambda: shutdown_called.append(True)
        shell._hide_window = lambda: self.fail("_hide_window should not be called")
        shell._native_action_queue.put("close")

        t = threading.Thread(target=shell._dispatch_native_actions, daemon=True)
        t.start()
        time.sleep(0.05)
        shell._native_stop_event.set()
        t.join(timeout=1.0)
        self.assertEqual([True], shutdown_called)

        # Case B: tray is available -> close hides window
        shell2 = WebAppLifecycleRegressionTests.make_shell()
        shell2._tray_unavailable = False
        shell2._tray_icon = object()  # non-None tray icon
        shell2._native_stop_event = threading.Event()
        hidden_called = []
        shell2._hide_window = lambda: hidden_called.append(True)
        shell2.shutdown = lambda: self.fail("shutdown should not be called")
        shell2._native_action_queue.put("close")

        t2 = threading.Thread(target=shell2._dispatch_native_actions, daemon=True)
        t2.start()
        time.sleep(0.05)
        shell2._native_stop_event.set()
        t2.join(timeout=1.0)
        self.assertEqual([True], hidden_called)

    def test_close_queued_before_tray_failure_shuts_down_on_dispatch(self):
        shell = WebAppLifecycleRegressionTests.make_shell()
        shell._tray_unavailable = False
        shell._tray_icon = object()
        shell._native_stop_event = threading.Event()
        shutdown_called = []
        shell.shutdown = lambda: shutdown_called.append(True)
        shell._hide_window = lambda: self.fail("_hide_window should not be called when tray fails")

        # 1. Action "close" is queued while tray is still active
        shell.enqueue_native_action("close")

        # 2. Tray fails while "close" is pending in queue
        shell._tray_unavailable = True
        shell._tray_icon = None

        # 3. Dispatcher runs and processes "close"
        t = threading.Thread(target=shell._dispatch_native_actions, daemon=True)
        t.start()
        time.sleep(0.05)
        shell._native_stop_event.set()
        t.join(timeout=1.0)
        self.assertEqual([True], shutdown_called)


class WebAppLifecycleFailureInjectionRegressionTests(unittest.TestCase):
    def test_create_window_failure_rolls_back(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            app = WebApp(root, silent=True)
            with patch.object(app, "create_window", side_effect=RuntimeError("window fail")):
                with self.assertRaises(RuntimeError):
                    app.run()
            self.assertFalse(app.controller.running)

    def test_dispatcher_start_failure_rolls_back(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            app = WebApp(root, silent=True)
            with patch.object(app, "create_window", return_value=None):
                with patch.object(app, "_start_native_dispatcher", side_effect=RuntimeError("disp fail")):
                    with self.assertRaises(RuntimeError):
                        app.run()
            self.assertFalse(app.controller.running)

    def test_setup_tray_failure_rolls_back(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            app = WebApp(root, silent=True)
            with patch.object(app, "create_window", return_value=None):
                with patch.object(app, "setup_tray", side_effect=RuntimeError("tray fail")):
                    with self.assertRaises(RuntimeError):
                        app.run()
            self.assertFalse(app.controller.running)

    def test_controller_start_failure_rolls_back(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            app = WebApp(root, silent=True)
            with patch.object(app, "create_window", return_value=None):
                with patch.object(app.controller, "start", side_effect=RuntimeError("ctrl fail")):
                    with self.assertRaises(RuntimeError):
                        app.run()
            self.assertFalse(app.controller.running)

    def test_repeated_shutdown_is_safe_and_idempotent(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            app = WebApp(root, silent=True)
            res1 = app.shutdown()
            res2 = app.shutdown()
            res3 = app.shutdown()
            self.assertTrue(res1["ok"])
            self.assertTrue(res2["ok"])
            self.assertTrue(res3["ok"])

    def test_controller_init_failure_releases_playback(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            with patch.object(MusicPlayerController, "_read_startup_status", side_effect=RuntimeError("reg fail")):
                with self.assertRaises(RuntimeError):
                    MusicPlayerController(root, initialize_audio=False)


if __name__ == "__main__":
    unittest.main()
