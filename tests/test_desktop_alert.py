"""Behavior-first tests for local desktop sound/light alert decisions.

The policy is deliberately UI-free: Tkinter, Winsound, tray flashing, and
other Windows side effects belong at the delivery boundary.  This makes the
state transitions deterministic and safe to exercise on a headless runner.
"""
from __future__ import annotations

import json
import logging
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core.desktop_alert import DesktopAlertPolicy
from app.core.monitor import MonitorEngine
from app.utils.config import ConfigManager


def make_target(**overrides: object) -> dict[str, object]:
    """Return an enabled target suitable for isolated policy tests."""
    target: dict[str, object] = {
        "name": "核心交换机",
        "address": "10.0.0.1",
        "check_type": "ping",
        "alert_enabled": True,
        "desktop_alert_enabled": True,
    }
    target.update(overrides)
    return target


def make_result(status: str) -> dict[str, object]:
    return {
        "status": status,
        "error_msg": "请求超时" if status == "down" else "",
        "check_time": "2026-09-08T09:00:00",
    }


class DesktopAlertPolicyTests(unittest.TestCase):
    """Local-notification state transitions independent of a GUI display."""

    def test_alert_is_returned_only_when_failure_threshold_is_reached(self) -> None:
        policy = DesktopAlertPolicy({"failure_threshold": 3})
        target = make_target()

        self.assertIsNone(policy.process(target, make_result("down")))
        self.assertIsNone(policy.process(target, make_result("down")))
        self.assertEqual(policy.process(target, make_result("down")), "alert")

    def test_continuing_failure_returns_one_local_alert_only(self) -> None:
        policy = DesktopAlertPolicy({"failure_threshold": 1})
        target = make_target()

        self.assertEqual(policy.process(target, make_result("down")), "alert")
        self.assertIsNone(policy.process(target, make_result("down")))
        self.assertIsNone(policy.process(target, make_result("down")))

    def test_recovery_returns_one_recovery_after_a_local_alert(self) -> None:
        policy = DesktopAlertPolicy({"failure_threshold": 1})
        target = make_target()

        self.assertEqual(policy.process(target, make_result("down")), "alert")
        self.assertEqual(policy.process(target, make_result("up")), "recovery")
        self.assertIsNone(policy.process(target, make_result("up")))

    def test_globally_disabled_target_does_not_create_local_alerts(self) -> None:
        policy = DesktopAlertPolicy({"failure_threshold": 1})
        target = make_target(alert_enabled=False)

        self.assertIsNone(policy.process(target, make_result("down")))
        self.assertIsNone(policy.process(target, make_result("down")))

    def test_target_with_desktop_alert_disabled_does_not_create_local_alerts(self) -> None:
        policy = DesktopAlertPolicy({"failure_threshold": 1})
        target = make_target(desktop_alert_enabled=False)

        self.assertIsNone(policy.process(target, make_result("down")))
        self.assertIsNone(policy.process(target, make_result("down")))

    def test_hand_edited_false_string_disables_the_global_desktop_alert(self) -> None:
        policy = DesktopAlertPolicy({"failure_threshold": 1, "desktop_alert_enabled": "false"})

        self.assertIsNone(policy.process(make_target(), make_result("down")))


class DesktopAlertConfigurationTests(unittest.TestCase):
    def test_old_settings_receive_the_enabled_desktop_alert_default(self) -> None:
        """Existing settings.json files must gain a safe opt-in default in memory."""
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "settings.json").write_text(
                json.dumps({"failure_threshold": 4}), encoding="utf-8"
            )

            settings = ConfigManager(root).load_settings()

        self.assertTrue(settings["desktop_alert_enabled"])


class ImmediateDesktopAlertDeliveryTests(unittest.TestCase):
    """A slow outbound alert must not delay the local GUI result event."""

    def test_result_is_queued_before_a_blocking_remote_alert(self) -> None:
        class BlockingAlertEngine:
            def __init__(self) -> None:
                self.settings: dict[str, object] = {}
                self.started = threading.Event()
                self.release = threading.Event()

            def process(self, _target: dict[str, object], _result: dict[str, object]) -> None:
                self.started.set()
                self.release.wait(timeout=2)

        target = {"name": "核心交换机", "address": "10.0.0.1", "check_type": "ping"}
        events: queue.Queue[dict[str, object]] = queue.Queue()
        alert_engine = BlockingAlertEngine()
        database = SimpleNamespace(save_result=lambda *_args: None)
        engine = MonitorEngine([target], database, alert_engine, events, logging.getLogger("test.desktop_alert"))
        worker = threading.Thread(target=engine._check_target, args=(target, None))

        with patch("app.core.monitor.ping_check.check", return_value={"status": "down", "latency": None, "error_msg": "请求超时"}):
            worker.start()
            try:
                self.assertTrue(alert_engine.started.wait(timeout=1))
                event = events.get(timeout=0.2)
            finally:
                alert_engine.release.set()
                worker.join(timeout=1)

        self.assertEqual(event["event"], "result")
        self.assertEqual(event["result"]["status"], "down")


if __name__ == "__main__":
    unittest.main()
