"""Tests for the daily WeCom inspection report scheduling logic."""
from __future__ import annotations

import logging
import queue
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from app.core.alert import AlertEngine
from app.core.monitor import (
    LAST_REPORT_DATE_KEY,
    MonitorEngine,
    _parse_report_time,
    should_send_daily_report,
)
from app.utils.database import MonitorDatabase


class RecordingNotifier:
    """Minimal notification boundary for observable report behavior."""

    def __init__(self, succeed: bool = True) -> None:
        self.messages: list[str] = []
        self.succeed = succeed

    def send_markdown(self, content: str) -> bool:
        self.messages.append(content)
        return self.succeed


class ParseReportTimeTests(unittest.TestCase):
    def test_valid_hhmm(self) -> None:
        self.assertEqual(_parse_report_time("08:30"), (8, 30))
        self.assertEqual(_parse_report_time("23:59"), (23, 59))

    def test_invalid_values_return_none(self) -> None:
        self.assertIsNone(_parse_report_time("8点"))
        self.assertIsNone(_parse_report_time("25:00"))
        self.assertIsNone(_parse_report_time("-1:00"))
        self.assertIsNone(_parse_report_time(""))


class DailyReportDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = datetime(2026, 10, 22, 8, 0, 0)

    def test_disabled_never_sends(self) -> None:
        self.assertFalse(
            should_send_daily_report(self.today, "08:00", None, False, False)
        )

    def test_already_sent_today_skips(self) -> None:
        self.assertFalse(
            should_send_daily_report(self.today, "08:00", self.today.date(), False, True)
        )

    def test_suppressed_today_skips(self) -> None:
        self.assertFalse(
            should_send_daily_report(self.today, "08:00", None, True, True)
        )

    def test_before_due_time_waits(self) -> None:
        before = datetime(2026, 10, 22, 7, 59)
        self.assertFalse(should_send_daily_report(before, "08:00", None, False, True))

    def test_at_due_time_fires(self) -> None:
        self.assertTrue(should_send_daily_report(self.today, "08:00", None, False, True))

    def test_after_due_time_fires_when_not_sent(self) -> None:
        after = datetime(2026, 10, 22, 9, 30)
        self.assertTrue(should_send_daily_report(after, "08:00", None, False, True))

    def test_yesterday_sent_fires_today(self) -> None:
        yesterday = date(2026, 10, 21)
        self.assertTrue(should_send_daily_report(self.today, "08:00", yesterday, False, True))

    def test_malformed_report_time_never_fires(self) -> None:
        self.assertFalse(
            should_send_daily_report(self.today, "not-a-time", None, False, True)
        )
        self.assertFalse(
            should_send_daily_report(self.today, "25:00", None, False, True)
        )


class DailyReportSendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = MonitorDatabase(Path(self._tmp.name) / "monitor.db")
        self.settings: dict = {
            "daily_report_enabled": True,
            "daily_report_time": "08:00",
        }
        self.notifier = RecordingNotifier()
        self.alert = AlertEngine(self.settings, self.notifier, logging.getLogger("test"))
        self.engine = MonitorEngine(
            targets=[],
            database=self.database,
            alert_engine=self.alert,
            event_queue=queue.Queue(),
            logger=logging.getLogger("test"),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_send_writes_last_report_date_on_success(self) -> None:
        self.engine._send_daily_report()
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertIn("巡检报告", self.notifier.messages[0])
        state = self.database.load_app_state()
        self.assertEqual(state.get(LAST_REPORT_DATE_KEY), date.today().isoformat())

    def test_send_failure_does_not_raise_or_persist(self) -> None:
        self.notifier.succeed = False
        # Must not raise even when the webhook returns failure.
        self.engine._send_daily_report()
        self.assertEqual(len(self.notifier.messages), 1)
        state = self.database.load_app_state()
        self.assertNotIn(LAST_REPORT_DATE_KEY, state)

    def test_init_state_marks_today_as_sent_when_persisted(self) -> None:
        self.database.save_app_state(LAST_REPORT_DATE_KEY, date.today().isoformat())
        self.engine._init_daily_report_state()
        self.assertEqual(self.engine._last_report_date, date.today())
        self.assertTrue(self.engine._report_suppressed_today)


if __name__ == "__main__":
    unittest.main()
