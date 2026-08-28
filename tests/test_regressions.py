"""Regression coverage for the webview application's non-GUI core."""

from __future__ import annotations

import json
import queue
import shutil
import unittest
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from player_core import MusicPlayerController, SchedulerService, TaskStore
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
        service = SchedulerService(lambda: tasks, lambda task, index: started.append((task["name"], index)))
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
                }
            }
            result = controller.save_task(payload)
            self.assertTrue(result["ok"])
            self.assertEqual("09:30:00", result["state"]["tasks"][0]["time"])
            self.assertEqual("Morning", result["state"]["tasks"][0]["name"])
            self.assertTrue(controller.set_task_enabled(0, False)["ok"])
            self.assertFalse(controller.get_state()["tasks"][0]["enabled"])
            self.assertTrue(controller.delete_task(0)["ok"])
            self.assertEqual([], controller.get_state()["tasks"])
            controller.shutdown()

    def test_invalid_duration_payload_is_rejected_without_write(self):
        with temporary_workspace() as temp:
            root = Path(temp)
            (root / "mp3").mkdir()
            controller = MusicPlayerController(root, initialize_audio=False)

            # 1. Invalid time string
            res1 = controller.save_task(
                {"time": "09:30", "mode": "duration", "end_time": "bad", "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x"}
            )
            self.assertFalse(res1["ok"])

            # 2. Empty end_time
            res2 = controller.save_task(
                {"time": "09:00", "mode": "duration", "end_time": "", "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x"}
            )
            self.assertFalse(res2["ok"])

            # 3. Omitted end_time
            res3 = controller.save_task(
                {"time": "10:00", "mode": "duration", "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x"}
            )
            self.assertFalse(res3["ok"])

            # 4. Backward end_time without next-day
            res4 = controller.save_task(
                {"time": "10:00", "mode": "duration", "end_time": "09:00", "end_next_day": False, "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x"}
            )
            self.assertFalse(res4["ok"])

            # 5. Equal end_time and start_time without next-day
            res5 = controller.save_task(
                {"time": "10:00", "mode": "duration", "end_time": "10:00", "end_next_day": False, "files": ["mp3/ok.mp3"], "weekdays": [0], "name": "x"}
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
                }
            )
            self.assertTrue(result["ok"])
            tasks = controller.get_state()["tasks"]
            self.assertEqual(1, len(tasks))
            self.assertEqual("00:30:00", tasks[0]["end_time"])
            self.assertTrue(tasks[0]["end_next_day"])
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
        shell._closing = False
        shell._tray_unavailable = False
        shell._tray_alert_sent = False
        shell.silent = False
        shell._native_action_queue = queue.Queue()
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
        # Verify fallback: closing the window exits instead of hiding
        self.assertFalse(shell.on_closing())
        self.assertEqual("exit", shell._native_action_queue.get_nowait())

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
        self.assertEqual("exit", shell2._native_action_queue.get_nowait())

    def test_native_close_is_cancelled_and_serialized_to_tray_action(self):
        shell = self.make_shell()
        self.assertFalse(shell.on_closing())
        self.assertEqual("hide", shell._native_action_queue.get_nowait())

        shell._tray_unavailable = True
        self.assertFalse(shell.on_closing())
        self.assertEqual("exit", shell._native_action_queue.get_nowait())

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


if __name__ == "__main__":
    unittest.main()
