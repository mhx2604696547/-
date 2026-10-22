"""Tests for the daily website and security-device inspection workflow."""
from __future__ import annotations

import logging
import queue
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core import http_check
from app.core.monitor import MonitorEngine


class _DeviceLoginHandler(BaseHTTPRequestHandler):
    """A local device-login page for the HTTP integration test."""

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler.
        body = "<html><title>安全设备管理登录</title></html>".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep the test output focused on unittest results."""


class HttpInspectionTests(unittest.TestCase):
    """A website is healthy only when its response and optional marker match."""

    def test_accepts_self_signed_device_page_and_expected_text(self) -> None:
        response = Mock(status_code=200, text="<title>防火墙管理登录</title>")
        with patch("app.core.http_check.requests.get", return_value=response) as get:
            result = http_check.check(
                "https://firewall.example.test/login",
                timeout_seconds=8,
                expected_text="管理登录",
                verify_tls=False,
            )

        self.assertEqual(result["status"], "up")
        self.assertIn("HTTP 200", result["detail"])
        get.assert_called_once_with(
            "https://firewall.example.test/login",
            timeout=8.0,
            allow_redirects=True,
            verify=False,
            headers={"User-Agent": "NetworkMonitor/1.0"},
        )

    def test_marks_page_down_when_expected_text_is_missing(self) -> None:
        response = Mock(status_code=200, text="<title>设备维护中</title>")
        with patch("app.core.http_check.requests.get", return_value=response):
            result = http_check.check("https://device.example.test", expected_text="登录")

        self.assertEqual(result["status"], "down")
        self.assertIn("预期文本未找到", result["error_msg"])

    def test_clamps_timeout_to_a_safe_range(self) -> None:
        response = Mock(status_code=200, text="ok")
        with patch("app.core.http_check.requests.get", return_value=response) as get:
            http_check.check("portal.example.test", timeout_seconds=999)

        self.assertEqual(get.call_args.kwargs["timeout"], 60.0)

    def test_accepts_false_string_from_hand_edited_json_for_tls_check(self) -> None:
        response = Mock(status_code=200, text="ok")
        with patch("app.core.http_check.requests.get", return_value=response) as get:
            http_check.check("https://device.example.test", verify_tls="false")

        self.assertFalse(get.call_args.kwargs["verify"])

    def test_checks_a_real_local_login_page(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _DeviceLoginHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            result = http_check.check(
                f"http://{host}:{port}/login",
                expected_text="安全设备管理登录",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(result["status"], "up")


class WebsiteBatchDispatchTests(unittest.TestCase):
    """The daily button must dispatch only configured HTTP targets."""

    def test_check_websites_now_dispatches_only_http_targets(self) -> None:
        targets = [
            {"name": "门户", "address": "https://portal.example.test", "check_type": "http"},
            {"name": "网关", "address": "192.168.1.1", "check_type": "ping"},
            {"name": "防火墙", "address": "https://fw.example.test", "check_type": "http"},
        ]
        engine = MonitorEngine(
            targets,
            database=object(),
            alert_engine=SimpleNamespace(settings={}),
            event_queue=queue.Queue(),
            logger=logging.getLogger("test"),
        )
        submitted: list[dict[str, object]] = []
        engine._executor = SimpleNamespace(
            submit=lambda _func, target, _stop_event: submitted.append(target),
        )

        count = engine.check_websites_now()

        self.assertEqual(count, 2)
        self.assertEqual([target["name"] for target in submitted], ["门户", "防火墙"])

    def test_monitor_passes_webpage_settings_to_http_checker(self) -> None:
        saved: list[tuple[dict[str, object], dict[str, object]]] = []
        alert_results: list[dict[str, object]] = []
        database = SimpleNamespace(save_result=lambda target, result: saved.append((target, result)))
        alert_engine = SimpleNamespace(
            settings={},
            process=lambda _target, result: alert_results.append(result),
        )
        engine = MonitorEngine([], database, alert_engine, queue.Queue(), logging.getLogger("test"))
        target = {
            "name": "防火墙登录页",
            "address": "https://fw.example.test/login",
            "check_type": "http",
            "http_timeout_seconds": 9,
            "http_expected_text": "管理登录",
            "http_verify_tls": False,
        }
        check_result = {"status": "up", "latency": 11.0, "error_msg": "", "detail": "HTTP 200"}

        with patch("app.core.monitor.http_check.check", return_value=check_result) as check:
            engine._check_target(target, None)

        check.assert_called_once_with(
            target["address"],
            timeout_seconds=9,
            expected_text="管理登录",
            verify_tls=False,
        )
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(alert_results), 1)


if __name__ == "__main__":
    unittest.main()
