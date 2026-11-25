"""Unit tests for autostart, alert closure, TLS expiry, SNMP bandwidth, history, and config backup."""
from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.alert import AlertEngine, evaluate_performance_warnings, is_target_muted
from app.core.daily_report import build_daily_report
from app.core.http_check import parse_tls_not_after_days
from app.core.monitor import detect_sleep_resume
from app.core.snmp_check import compute_rate_mbps
from app.utils.autostart import build_autostart_command, parse_launch_args
from app.utils.config import export_config_bundle, import_config_bundle
from app.utils.database import MonitorDatabase, extract_status_transitions


class RecordingNotifier:
    def __init__(self, succeed: bool = True) -> None:
        self.messages: list[str] = []
        self.succeed = succeed

    def send_markdown(self, content: str) -> bool:
        self.messages.append(content)
        return self.succeed


class AutostartHelperTests(unittest.TestCase):
    def test_parses_autostart_cli_flag(self) -> None:
        self.assertTrue(parse_launch_args(["--autostart"])["autostart"])
        self.assertFalse(parse_launch_args([])["autostart"])

    def test_builds_registry_command_for_frozen_and_source(self) -> None:
        self.assertEqual(
            build_autostart_command(r"C:\Apps\NetworkMonitor.exe", frozen=True),
            r'"C:\Apps\NetworkMonitor.exe" --autostart',
        )
        self.assertIn("-m app.main --autostart", build_autostart_command(r"C:\Python313\pythonw.exe", frozen=False))


class AlertClosureTests(unittest.TestCase):
    def test_mute_window_blocks_alerts_while_active(self) -> None:
        future = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
        past = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        self.assertTrue(is_target_muted({"muted_until": future}))
        self.assertFalse(is_target_muted({"muted_until": past}))
        self.assertFalse(is_target_muted({}))

        notifier = RecordingNotifier()
        engine = AlertEngine({"failure_threshold": 1, "cooldown_minutes": 5}, notifier, logging.getLogger("t"))
        target = {"name": "SW1", "address": "10.0.0.1", "check_type": "ping", "muted_until": future}
        res = engine.process(target, {"status": "down", "error_msg": "timeout", "check_time": "2026-11-24T10:00:00"})
        self.assertIsNone(res)
        self.assertEqual(notifier.messages, [])

    def test_correlation_merges_multi_target_storm(self) -> None:
        notifier = RecordingNotifier()
        engine = AlertEngine(
            {"failure_threshold": 1, "cooldown_minutes": 5, "correlation_alert_threshold": 3},
            notifier,
            logging.getLogger("t"),
        )
        down = {"status": "down", "error_msg": "timeout", "check_time": "2026-11-24T10:00:00"}
        r1 = engine.process({"name": "A", "address": "10.0.0.1", "check_type": "ping"}, down)
        r2 = engine.process({"name": "B", "address": "10.0.0.2", "check_type": "ping"}, down)
        r3 = engine.process({"name": "C", "address": "10.0.0.3", "check_type": "ping"}, down)
        r4 = engine.process({"name": "D", "address": "10.0.0.4", "check_type": "ping"}, down)
        self.assertEqual((r1, r2, r3, r4), ("alert", "alert", "correlation", None))
        self.assertIn("关联告警", notifier.messages[-1])

    def test_evaluates_slow_snmp_resource_and_tls_warnings(self) -> None:
        settings = {
            "slow_warning_threshold_ms": 200,
            "snmp_cpu_threshold": 85,
            "snmp_mem_threshold": 80,
            "snmp_disk_threshold": 90,
            "tls_expiry_warn_days": 30,
        }
        snmp_target = {"name": "App01", "address": "10.0.0.9", "check_type": "snmp"}
        snmp_res = {
            "status": "up", "latency": 250.0,
            "detail": "设备名：App01，CPU 92%，内存 84%，磁盘最高 95%",
        }
        warns = evaluate_performance_warnings(snmp_target, snmp_res, settings)
        self.assertEqual(len(warns), 4)

        http_target = {"name": "Portal", "address": "https://portal.local", "check_type": "http"}
        http_warns = evaluate_performance_warnings(http_target, {"status": "up", "latency": 20, "tls_days_left": 10}, settings)
        self.assertEqual(len(http_warns), 1)
        self.assertIn("剩余 10 天", http_warns[0])


class TlsAndBandwidthHelperTests(unittest.TestCase):
    def test_parses_tls_not_after_days_remaining(self) -> None:
        now = datetime(2026, 11, 24, 0, 0, 0, tzinfo=timezone.utc)
        days = parse_tls_not_after_days("Dec 24 00:00:00 2026 GMT", now=now)
        self.assertEqual(days, 30)

    def test_computes_interface_mbps_including_32bit_wrap(self) -> None:
        # 1,250,000 bytes in 1 second = 10.0 Mbps
        self.assertEqual(compute_rate_mbps(1000, 1_251_000, 1.0), 10.0)
        # Wraparound across 2**32
        prev = (2 ** 32) - 500_000
        curr = 750_000
        self.assertEqual(compute_rate_mbps(prev, curr, 1.0), 10.0)


class HistoryAndConfigBundleTests(unittest.TestCase):
    def test_extracts_status_transitions_and_reads_target_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = MonitorDatabase(Path(tmp) / "m.db")
            target = {"name": "CoreSW", "address": "10.0.0.1", "check_type": "ping"}
            t0 = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
            t1 = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
            t2 = datetime.now().isoformat(timespec="seconds")
            db.save_result(target, {"status": "up", "latency": 2.0, "error_msg": "", "detail": "", "check_time": t0})
            db.save_result(target, {"status": "down", "latency": None, "error_msg": "timeout", "detail": "", "check_time": t1})
            db.save_result(target, {"status": "up", "latency": 3.0, "error_msg": "", "detail": "", "check_time": t2})
            history = db.get_target_history(target, hours=1)
            self.assertEqual(len(history), 3)
            transitions = extract_status_transitions(history)
            self.assertEqual(len(transitions), 2)
            self.assertEqual(transitions[0]["to_status"], "up")
            self.assertEqual(transitions[1]["to_status"], "down")

    def test_exports_and_imports_config_bundle_with_optional_redaction(self) -> None:
        targets = [{"name": "SW", "address": "10.0.0.1", "check_type": "snmp", "snmp_community": "secret"}]
        settings = {"wechat_webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc", "failure_threshold": 5}
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "backup.zip"
            export_config_bundle(zip_path, targets, settings, redact_secrets=True)
            loaded_targets, loaded_settings = import_config_bundle(zip_path)
            self.assertEqual(loaded_targets[0]["snmp_community"], "")
            self.assertEqual(loaded_settings["wechat_webhook"], "")
            self.assertEqual(loaded_settings["failure_threshold"], 5)


class SleepResumeAndDailyReportNoticeTests(unittest.TestCase):
    def test_detects_sleep_resume_on_wall_clock_jump(self) -> None:
        self.assertFalse(detect_sleep_resume(1000.0, 1005.0))
        self.assertTrue(detect_sleep_resume(1000.0, 1045.0))

    def test_daily_report_includes_expiring_cert_and_high_resource_notices(self) -> None:
        snapshot = {
            "status_counts": {"total": 2, "up": 2, "down": 0, "unknown": 0},
            "targets": [
                {"name": "门户网站", "status": "up", "detail": "HTTP 200；证书剩 7 天"},
                {"name": "核心DB", "status": "up", "detail": "设备名：db01，CPU 95%，内存 88%，磁盘最高 93%"},
            ],
            "exceptions": [],
            "today_inspection": {"status": "not_started", "total": 1, "up": 0, "down": 0},
        }
        report = build_daily_report(
            snapshot, {}, {"snmp_cpu_threshold": 90, "snmp_mem_threshold": 90, "snmp_disk_threshold": 90, "tls_expiry_warn_days": 30}
        )
        self.assertIn("资源与证书提醒", report)
        self.assertIn("证书剩 7 天", report)
        self.assertIn("CPU 95%", report)
        self.assertIn("磁盘 93%", report)


if __name__ == "__main__":
    unittest.main()
