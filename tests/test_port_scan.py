"""Behavior tests for the manual port scanning tool."""
from __future__ import annotations

import queue
import socket
import threading
import unittest

from app.core import port_scan


def _open_local_listener() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return listener, listener.getsockname()[1]


def _closed_local_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class ParsePortsTests(unittest.TestCase):
    def test_single_and_list_and_range(self) -> None:
        self.assertEqual(port_scan.parse_ports("80"), [80])
        self.assertEqual(port_scan.parse_ports("80,443"), [80, 443])
        self.assertEqual(port_scan.parse_ports("8000-8003"), [8000, 8001, 8002, 8003])
        self.assertEqual(port_scan.parse_ports("443,8000-8002,80"), [80, 443, 8000, 8001, 8002])

    def test_reversed_range_is_normalized(self) -> None:
        self.assertEqual(port_scan.parse_ports("8003-8000"), [8000, 8001, 8002, 8003])

    def test_duplicate_ports_are_deduplicated(self) -> None:
        self.assertEqual(port_scan.parse_ports("80,80,80-80"), [80])

    def test_invalid_input_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            port_scan.parse_ports("")
        with self.assertRaises(ValueError):
            port_scan.parse_ports("abc")
        with self.assertRaises(ValueError):
            port_scan.parse_ports("0")
        with self.assertRaises(ValueError):
            port_scan.parse_ports("65536")
        with self.assertRaises(ValueError):
            port_scan.parse_ports("1-2000")  # exceeds MAX_PORTS


class CheckPortTests(unittest.TestCase):
    def test_open_port_reports_open_with_latency(self) -> None:
        listener, port = _open_local_listener()
        try:
            result = port_scan.check_port("127.0.0.1", port, timeout=1.0)
            self.assertEqual(result["status"], "open")
            self.assertIsNotNone(result["latency"])
            self.assertEqual(result["error_msg"], "")
        finally:
            listener.close()

    def test_closed_port_reports_closed(self) -> None:
        port = _closed_local_port()
        result = port_scan.check_port("127.0.0.1", port, timeout=1.0)
        self.assertEqual(result["status"], "closed")
        self.assertIsNone(result["latency"])
        self.assertTrue(result["error_msg"])


class ScanPortsTests(unittest.TestCase):
    def test_scan_streams_results_then_signals_done(self) -> None:
        listener, open_port = _open_local_listener()
        closed_port = _closed_local_port()
        try:
            result_queue: "queue.Queue[dict]" = queue.Queue()
            stop_event = threading.Event()
            port_scan.scan_ports("127.0.0.1", [open_port, closed_port], 1.0, stop_event, result_queue)

            collected = []
            while True:
                item = result_queue.get(timeout=2)
                if item.get("done"):
                    break
                collected.append(item)

            self.assertEqual(len(collected), 2)
            statuses = {item["port"]: item["status"] for item in collected}
            self.assertEqual(statuses[open_port], "open")
            self.assertEqual(statuses[closed_port], "closed")
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main()
