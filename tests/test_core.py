"""Public-behavior tests for configuration, storage, and alert thresholds."""
from __future__ import annotations

import logging
import platform
import queue
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from app.core.alert import AlertEngine
from app.core.monitor import MonitorEngine
from app.integrations.wechat import WeChatNotifier
from app.utils.config import ConfigManager, DEFAULT_TARGETS
from app.utils.database import MonitorDatabase


class RecordingNotifier:
    """Minimal notification boundary for observable alert behavior."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_markdown(self, content: str) -> bool:
        self.messages.append(content)
        return True


class ToggleNotifier(RecordingNotifier):
    def __init__(self) -> None:
        super().__init__()
        self.succeeds = False

    def send_markdown(self, content: str) -> bool:
        self.messages.append(content)
        return self.succeeds


class BlockingNotifier(RecordingNotifier):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def send_markdown(self, content: str) -> bool:
        self.messages.append(content)
        self.started.set()
        self.release.wait(timeout=2)
        return True


class NetworkMonitorBehaviorTests(unittest.TestCase):
    def test_first_run_creates_default_configuration(self) -> None:
        # Keep test files in the project tree: this also works in restricted Windows sandboxes.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            config = ConfigManager(Path(directory))
            targets = config.load_targets()
            settings = config.load_settings()
            self.assertEqual(targets, DEFAULT_TARGETS)
            self.assertTrue(config.targets_path.exists())
            self.assertIn("failure_threshold", settings)

    def test_database_persists_a_check_result(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            database = MonitorDatabase(Path(directory) / "monitor.db")
            target = {"name": "网页", "address": "https://example.test", "check_type": "http"}
            database.save_result(target, {"status": "up", "latency": 10.5, "error_msg": "", "check_time": "2026-08-17T12:00:00"})
            rows = database.export_rows()
            self.assertEqual(rows[0][0:5], ("网页", "https://example.test", "http", "up", 10.5))

    def test_alerts_after_threshold_then_notifies_recovery(self) -> None:
        notifier = RecordingNotifier()
        settings = {"failure_threshold": 2, "cooldown_minutes": 5, "send_recovery_notification": True}
        engine = AlertEngine(settings, notifier, logging.getLogger("test"))
        target = {"name": "网关", "address": "192.168.1.1", "check_type": "ping", "alert_enabled": True}
        failed = {"status": "down", "error_msg": "超时", "check_time": "2026-08-17T12:00:00"}
        recovered = {"status": "up", "error_msg": "", "check_time": "2026-08-17T12:01:00"}
        self.assertIsNone(engine.process(target, failed))
        self.assertEqual(engine.process(target, failed), "alert")
        self.assertEqual(len(notifier.messages), 1)
        self.assertEqual(engine.process(target, recovered), "recovery")
        self.assertEqual(len(notifier.messages), 2)

    def test_failed_notification_does_not_start_cooldown(self) -> None:
        notifier = ToggleNotifier()
        engine = AlertEngine({"failure_threshold": 1, "cooldown_minutes": 5}, notifier, logging.getLogger("test"))
        target = {"name": "网关", "address": "192.168.1.1", "check_type": "ping", "alert_enabled": True}
        failed = {"status": "down", "error_msg": "超时", "check_time": "2026-08-17T12:00:00"}
        self.assertIsNone(engine.process(target, failed))
        notifier.succeeds = True
        self.assertEqual(engine.process(target, failed), "alert")
        self.assertEqual(len(notifier.messages), 2)

    def test_new_failure_during_recovery_notification_is_not_erased(self) -> None:
        stable_notifier = RecordingNotifier()
        engine = AlertEngine({"failure_threshold": 1, "cooldown_minutes": 5, "send_recovery_notification": True}, stable_notifier, logging.getLogger("test"))
        target = {"name": "网关", "address": "192.168.1.1", "check_type": "ping", "alert_enabled": True}
        failed = {"status": "down", "error_msg": "超时", "check_time": "2026-08-17T12:00:00"}
        recovered = {"status": "up", "error_msg": "", "check_time": "2026-08-17T12:01:00"}
        self.assertEqual(engine.process(target, failed), "alert")
        blocking_notifier = BlockingNotifier()
        engine.notifier = blocking_notifier
        outcomes: list[str | None] = []
        recovery_thread = threading.Thread(target=lambda: outcomes.append(engine.process(target, recovered)))
        recovery_thread.start()
        self.assertTrue(blocking_notifier.started.wait(timeout=1))
        self.assertIsNone(engine.process(target, failed))
        blocking_notifier.release.set()
        recovery_thread.join(timeout=1)
        self.assertEqual(outcomes, ["alert"])
        self.assertEqual(len(blocking_notifier.messages), 2)  # recovery, then corrective failure
        engine.notifier = stable_notifier
        self.assertEqual(engine.process(target, recovered), "recovery")


class RecordingLogHandler(logging.Handler):
    """Captures formatted records so tests can assert on log content."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


class WeChatNotifierSecurityTests(unittest.TestCase):
    def test_request_failure_does_not_leak_webhook_url_or_key_into_logs(self) -> None:
        webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=super-secret-key"
        logger = logging.getLogger("test_wechat_redaction")
        logger.setLevel(logging.DEBUG)
        handler = RecordingLogHandler()
        logger.addHandler(handler)
        try:
            notifier = WeChatNotifier(webhook, logger)
            # Real ConnectionError/HTTPError text from `requests` typically embeds
            # the full request URL (and therefore the secret key) verbatim.
            failure = requests.exceptions.ConnectionError(
                f"Max retries exceeded with url: /cgi-bin/webhook/send?key=super-secret-key "
                f"(Caused by NewConnectionError) full url was {webhook}"
            )
            with patch("app.integrations.wechat.requests.post", side_effect=failure):
                result = notifier.send_markdown("test message")
            self.assertFalse(result)
            logged_text = "\n".join(handler.records)
            self.assertNotIn("super-secret-key", logged_text)
            self.assertNotIn(webhook, logged_text)
        finally:
            logger.removeHandler(handler)


class RecordingCleanupDatabase:
    """Fake database that only records cleanup_history calls."""

    def __init__(self) -> None:
        self.cleanup_calls: list[int] = []

    def cleanup_history(self, retention_days: int = 30) -> int:
        self.cleanup_calls.append(retention_days)
        return 0


class FakeAlertEngine:
    def __init__(self, settings: dict) -> None:
        self.settings = settings


class MonitorEngineCleanupTests(unittest.TestCase):
    def test_run_cleanup_uses_configured_retention_days(self) -> None:
        database = RecordingCleanupDatabase()
        alert_engine = FakeAlertEngine({"history_retention_days": 14})
        engine = MonitorEngine([], database, alert_engine, queue.Queue(), logging.getLogger("test"))
        engine._run_cleanup()
        self.assertEqual(database.cleanup_calls, [14])

    def test_start_schedules_periodic_cleanup_a_day_out_not_immediately(self) -> None:
        database = RecordingCleanupDatabase()
        alert_engine = FakeAlertEngine({"history_retention_days": 30})
        engine = MonitorEngine([], database, alert_engine, queue.Queue(), logging.getLogger("test"))
        before = time.monotonic()
        self.assertTrue(engine.start())
        try:
            # main.py already performs a cleanup pass at startup; the engine
            # must not immediately schedule a second one on top of it.
            self.assertGreater(engine._next_cleanup, before + 3600)
            time.sleep(0.5)
            self.assertEqual(database.cleanup_calls, [])
        finally:
            engine.shutdown()


class PingCheckRobustnessTests(unittest.TestCase):
    def test_ping_decodes_output_with_errors_replace(self) -> None:
        """Regression test: a codepage/encoding mismatch must not crash the
        check (previously observed to silently lose subprocess output when
        Python's text-mode decoding didn't tolerate the console's codepage)."""
        from app.core import ping_check

        captured_kwargs: dict = {}

        def fake_run(command, **kwargs):
            captured_kwargs.update(kwargs)
            return subprocess.CompletedProcess(command, returncode=0, stdout="time=1ms", stderr="")

        with patch("app.core.ping_check.subprocess.run", side_effect=fake_run):
            result = ping_check.check("127.0.0.1")
        self.assertEqual(captured_kwargs.get("errors"), "replace")
        self.assertEqual(result["status"], "up")

    def test_ping_uses_configured_count_and_timeout(self) -> None:
        from app.core import ping_check

        captured_command: list = []

        def fake_run(command, **kwargs):
            captured_command.extend(command)
            return subprocess.CompletedProcess(command, returncode=0, stdout="Average = 5ms", stderr="")

        with patch("app.core.ping_check.subprocess.run", side_effect=fake_run):
            result = ping_check.check("127.0.0.1", count=4, timeout_ms=1000)
        self.assertEqual(result["status"], "up")
        self.assertEqual(result["latency"], 5.0)
        self.assertIn("4", captured_command)
        expected_timeout = "1000" if platform.system() == "Windows" else "1"
        self.assertIn(expected_timeout, captured_command)

    def test_ping_reports_packet_loss_detail_for_multi_packet_runs(self) -> None:
        from app.core import ping_check

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(
                command, returncode=0,
                stdout="Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),\nAverage = 5ms", stderr="",
            )

        with patch("app.core.ping_check.subprocess.run", side_effect=fake_run):
            result = ping_check.check("127.0.0.1", count=4)
        self.assertEqual(result["status"], "up")
        self.assertIn("25%", result["detail"])

    def test_ping_omits_detail_for_a_single_packet(self) -> None:
        from app.core import ping_check

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, returncode=0, stdout="time=1ms", stderr="")

        with patch("app.core.ping_check.subprocess.run", side_effect=fake_run):
            result = ping_check.check("127.0.0.1", count=1)
        self.assertEqual(result.get("detail", ""), "")

    def test_ping_clamps_out_of_range_count_and_timeout(self) -> None:
        from app.core import ping_check

        captured_command: list = []

        def fake_run(command, **kwargs):
            captured_command.extend(command)
            return subprocess.CompletedProcess(command, returncode=0, stdout="time=1ms", stderr="")

        with patch("app.core.ping_check.subprocess.run", side_effect=fake_run):
            ping_check.check("127.0.0.1", count=999, timeout_ms=50)
        self.assertIn("10", captured_command)
        expected_timeout = "100" if platform.system() == "Windows" else "1"
        self.assertIn(expected_timeout, captured_command)

    def test_ping_still_up_when_some_packets_are_lost(self) -> None:
        """Windows/POSIX ping exit 0 whenever at least one reply arrived, so a
        partial-loss multi-packet run must still resolve to status='up'."""
        from app.core import ping_check

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(
                command, returncode=0,
                stdout="Packets: Sent = 4, Received = 1, Lost = 3 (75% loss),\nAverage = 5ms", stderr="",
            )

        with patch("app.core.ping_check.subprocess.run", side_effect=fake_run):
            result = ping_check.check("127.0.0.1", count=4)
        self.assertEqual(result["status"], "up")
        self.assertIn("75%", result["detail"])


if __name__ == "__main__":
    unittest.main()
