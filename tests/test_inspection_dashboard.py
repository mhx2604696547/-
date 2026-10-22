"""Regression tests for the daily inspection workflow and dashboard data."""
from __future__ import annotations

import logging
import queue
import sqlite3
import tempfile
import unittest
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core.alert import AlertEngine
from app.core.monitor import MonitorEngine
from app.utils.config import ConfigManager
from app.utils.database import MonitorDatabase


class _HoldingExecutor:
    """Records submitted work without completing it until the test says so."""

    def __init__(self) -> None:
        self.submissions: list[tuple[object, dict[str, object], object]] = []
        self.future: Future[None] = Future()

    def submit(self, function: object, target: dict[str, object], stop_event: object) -> Future[None]:
        self.submissions.append((function, target, stop_event))
        return self.future


class DailyInspectionStorageTests(unittest.TestCase):
    """A daily website run remains usable for retry and dashboard queries."""

    def test_persists_a_run_and_reopens_only_failed_items_for_retry(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            database = MonitorDatabase(Path(directory) / "monitor.db")
            targets = [
                {"name": "门户", "address": "https://portal.example.test", "check_type": "http", "interval": 60},
                {"name": "防火墙", "address": "https://fw.example.test", "check_type": "http", "interval": 60},
            ]
            run_id = database.create_inspection_run(targets, started_at="2026-09-07T09:00:00")
            database.record_inspection_result(
                run_id, targets[0],
                {"status": "up", "latency": 12.5, "error_msg": "", "detail": "HTTP 200", "check_time": "2026-09-07T09:00:03"},
            )
            database.record_inspection_result(
                run_id, targets[1],
                {"status": "down", "latency": None, "error_msg": "连接超时", "detail": "", "check_time": "2026-09-07T09:00:04"},
            )

            summary = database.get_inspection_run(run_id)
            self.assertEqual(summary["status"], "finished")
            self.assertEqual((summary["total"], summary["completed"], summary["up"], summary["down"]), (2, 2, 1, 1))
            failures = database.get_inspection_failures(run_id)
            self.assertEqual([item["target_name"] for item in failures], ["防火墙"])

            self.assertEqual(database.prepare_inspection_retry(run_id, [item["target_key"] for item in failures]), 1)
            retrying = database.get_inspection_run(run_id)
            self.assertEqual((retrying["status"], retrying["completed"], retrying["pending"], retrying["down"]), ("running", 1, 1, 0))

    def test_dashboard_marks_missing_or_stale_results_unknown(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            database = MonitorDatabase(Path(directory) / "monitor.db")
            targets = [
                {"name": "正常门户", "address": "https://ok.example.test", "check_type": "http", "interval": 60},
                {"name": "异常设备", "address": "https://bad.example.test", "check_type": "http", "interval": 60},
                {"name": "过期记录", "address": "192.168.1.1", "check_type": "ping", "interval": 60},
                {"name": "未检测", "address": "192.168.1.2", "check_type": "ping", "interval": 60},
            ]
            database.save_result(targets[0], {"status": "up", "latency": 10, "error_msg": "", "check_time": "2026-09-07T10:00:00"})
            database.save_result(targets[1], {"status": "down", "latency": None, "error_msg": "连接失败", "check_time": "2026-09-07T10:00:00"})
            database.save_result(targets[2], {"status": "up", "latency": 5, "error_msg": "", "check_time": "2026-09-07T09:50:00"})

            snapshot = database.get_dashboard_snapshot(targets, now=datetime.fromisoformat("2026-09-07T10:01:00"))

            self.assertEqual(snapshot["status_counts"], {"total": 4, "up": 1, "down": 1, "unknown": 2})
            self.assertEqual([item["name"] for item in snapshot["exceptions"]], ["异常设备"])
            self.assertEqual(snapshot["today_inspection"]["status"], "not_started")

    def test_dashboard_uses_only_the_latest_saved_result_for_each_target(self) -> None:
        """Old history must not override the latest state in the live dashboard."""
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            database = MonitorDatabase(Path(directory) / "monitor.db")
            target = {"name": "核心门户", "address": "https://portal.example.test", "check_type": "http", "interval": 60}
            database.save_result(
                target,
                {"status": "up", "latency": 12.0, "error_msg": "", "check_time": "2026-09-07T10:00:00"},
            )
            database.save_result(
                target,
                {"status": "down", "latency": None, "error_msg": "HTTP 503", "check_time": "2026-09-07T10:01:00"},
            )

            snapshot = database.get_dashboard_snapshot([target], now=datetime.fromisoformat("2026-09-07T10:01:30"))

            self.assertEqual(snapshot["status_counts"], {"total": 1, "up": 0, "down": 1, "unknown": 0})
            self.assertEqual(snapshot["targets"][0]["error_msg"], "HTTP 503")

    def test_dashboard_exposes_real_hourly_history_for_the_monitor_wall(self) -> None:
        """The wall receives sparse saved buckets and can show an hourly fluctuation honestly."""
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            database = MonitorDatabase(Path(directory) / "monitor.db")
            targets = [
                {"name": "核心交换机", "address": "192.168.1.1", "check_type": "ping", "interval": 60},
                {"name": "防火墙", "address": "https://fw.example.test", "check_type": "http", "interval": 60},
            ]
            database.save_result(
                targets[0],
                {"status": "up", "latency": 1.0, "error_msg": "", "check_time": "2026-09-07T08:10:00"},
            )
            database.save_result(
                targets[0],
                {"status": "down", "latency": None, "error_msg": "超时", "check_time": "2026-09-07T08:45:00"},
            )
            database.save_result(
                targets[1],
                {"status": "up", "latency": 20.0, "error_msg": "", "check_time": "2026-09-07T09:05:00"},
            )

            snapshot = database.get_dashboard_snapshot(targets, now=datetime.fromisoformat("2026-09-07T10:30:00"))

            history = snapshot["hourly_history"]
            self.assertEqual(history["bucket_minutes"], 60)
            self.assertEqual(len(history["hours"]), 24)
            exchange_bucket = next(
                bucket for bucket in history["buckets"]
                if bucket["key"] == database.target_key(targets[0]) and bucket["hour_start"] == "2026-09-07T08:00:00"
            )
            firewall_bucket = next(
                bucket for bucket in history["buckets"]
                if bucket["key"] == database.target_key(targets[1]) and bucket["hour_start"] == "2026-09-07T09:00:00"
            )
            self.assertEqual(
                {key: exchange_bucket[key] for key in ("up", "down", "other", "total")},
                {"up": 1, "down": 1, "other": 0, "total": 2},
            )
            self.assertEqual(
                {key: firewall_bucket[key] for key in ("up", "down", "other", "total")},
                {"up": 1, "down": 0, "other": 0, "total": 1},
            )


class MonitorReliabilityTests(unittest.TestCase):
    """Monitor-side failures must not become false network failures."""

    def test_immediate_check_does_not_queue_a_target_twice_while_it_is_running(self) -> None:
        target = {"name": "门户", "address": "https://portal.example.test", "check_type": "http"}
        engine = MonitorEngine(
            [target], database=object(), alert_engine=SimpleNamespace(settings={}),
            event_queue=queue.Queue(), logger=logging.getLogger("test"),
        )
        executor = _HoldingExecutor()
        engine._executor = executor

        self.assertEqual(engine.check_now(check_type="http"), 1)
        self.assertEqual(engine.check_now(check_type="http"), 0)
        self.assertEqual(len(executor.submissions), 1)

        executor.future.set_result(None)
        self.assertEqual(engine.check_now(check_type="http"), 1)

    def test_storage_failure_keeps_a_successful_check_up_and_still_processes_alerts(self) -> None:
        target = {"name": "门户", "address": "https://portal.example.test", "check_type": "http"}
        processed: list[dict[str, object]] = []
        event_queue: queue.Queue[dict[str, object]] = queue.Queue()

        def raise_locked(*_args: object, **_kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        database = SimpleNamespace(save_result=raise_locked)
        alert_engine = SimpleNamespace(settings={}, process=lambda _target, result: processed.append(result))
        logger = logging.getLogger("test.storage_failure")
        logger.propagate = False
        logger.addHandler(logging.NullHandler())
        engine = MonitorEngine([target], database, alert_engine, event_queue, logger)
        successful_result = {"status": "up", "latency": 15.0, "error_msg": "", "detail": "HTTP 200"}

        with patch("app.core.monitor.http_check.check", return_value=successful_result):
            engine._check_target(target, None)

        event = event_queue.get_nowait()
        self.assertEqual(event["result"]["status"], "up")
        self.assertIn("database is locked", event["storage_error"])
        self.assertEqual([result["status"] for result in processed], ["up"])

    def test_web_check_button_creates_one_finished_daily_run_and_emits_progress(self) -> None:
        class InlineExecutor:
            def submit(self, function: object, target: dict[str, object], stop_event: object) -> Future[None]:
                future: Future[None] = Future()
                try:
                    function(target, stop_event)  # type: ignore[operator]
                except BaseException as error:
                    future.set_exception(error)
                else:
                    future.set_result(None)
                return future

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            database = MonitorDatabase(Path(directory) / "monitor.db")
            targets = [
                {"name": "门户", "address": "https://portal.example.test", "check_type": "http"},
                {"name": "防火墙", "address": "https://fw.example.test", "check_type": "http"},
                {"name": "网关", "address": "192.168.1.1", "check_type": "ping"},
            ]
            event_queue: queue.Queue[dict[str, object]] = queue.Queue()
            engine = MonitorEngine(
                targets, database, SimpleNamespace(settings={}, process=lambda _target, _result: None),
                event_queue, logging.getLogger("test"),
            )
            engine._executor = InlineExecutor()
            outcomes = [
                {"status": "up", "latency": 10.0, "error_msg": "", "detail": "HTTP 200"},
                {"status": "down", "latency": None, "error_msg": "超时", "detail": ""},
            ]

            with patch("app.core.monitor.http_check.check", side_effect=outcomes):
                self.assertEqual(engine.check_websites_now(), 2)

            summary = database.get_inspection_run(engine.last_inspection_run_id or "")
            self.assertIsNotNone(summary)
            self.assertEqual((summary["status"], summary["total"], summary["up"], summary["down"]), ("finished", 2, 1, 1))
            event_types: list[str] = []
            while not event_queue.empty():
                event_types.append(str(event_queue.get_nowait()["event"]))
            self.assertIn("inspection_started", event_types)
            self.assertIn("inspection_progress", event_types)
            self.assertIn("inspection_finished", event_types)


class DurableOperationsStateTests(unittest.TestCase):
    """Reliability state should survive ordinary application restarts."""

    def test_alert_failure_count_is_restored_after_restart(self) -> None:
        class Notifier:
            def __init__(self) -> None:
                self.messages: list[str] = []

            def send_markdown(self, content: str) -> bool:
                self.messages.append(content)
                return True

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            database = MonitorDatabase(Path(directory) / "monitor.db")
            target = {"name": "核心门户", "address": "https://portal.example.test", "check_type": "http", "alert_enabled": True}
            failed = {"status": "down", "error_msg": "超时", "check_time": "2026-09-07T10:00:00"}
            settings = {"failure_threshold": 2, "cooldown_minutes": 5, "send_recovery_notification": True}

            first_notifier = Notifier()
            first = AlertEngine(settings, first_notifier, logging.getLogger("test"), state_store=database)
            self.assertIsNone(first.process(target, failed))
            self.assertEqual(first_notifier.messages, [])

            second_notifier = Notifier()
            second = AlertEngine(settings, second_notifier, logging.getLogger("test"), state_store=database)
            self.assertEqual(second.process(target, failed), "alert")
            self.assertEqual(len(second_notifier.messages), 1)

    def test_config_save_keeps_the_previous_file_as_a_backup(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            config = ConfigManager(Path(directory))
            first_targets = [{"name": "旧目标", "address": "192.168.1.1", "check_type": "ping"}]
            second_targets = [{"name": "新目标", "address": "192.168.1.2", "check_type": "ping"}]
            config.save_targets(first_targets)
            config.save_targets(second_targets)

            self.assertEqual(config.load_targets(), second_targets)
            backup = config.targets_path.with_suffix(".json.bak")
            self.assertTrue(backup.exists())
            self.assertIn("旧目标", backup.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
