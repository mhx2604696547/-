"""Regression tests for the read-only monitoring wall."""
from __future__ import annotations

import tkinter as tk
import unittest

from app.gui.monitor_wall import MonitorWallPanel, wall_view_state


def _snapshot() -> dict[str, object]:
    return {
        "generated_at": "2026-09-07T10:30:00",
        "status_counts": {"total": 4, "up": 2, "down": 1, "unknown": 1},
        "targets": [
            {
                "key": "ok", "name": "办公门户", "address": "https://portal.example.test", "check_type": "http",
                "status": "up", "latency": 18.3, "error_msg": "", "check_time": "2026-09-07T10:30:00",
            },
            {
                "key": "bad", "name": "防火墙登录页", "address": "https://firewall.example.test/login", "check_type": "http",
                "status": "down", "latency": None, "error_msg": "连接超时", "check_time": "2026-09-07T10:29:45",
            },
            {
                "key": "unknown", "name": "业务数据库", "address": "192.168.1.20:3306", "check_type": "tcp",
                "status": "unknown", "latency": None, "error_msg": "", "check_time": "2026-09-07T10:20:00",
            },
        ],
        "exceptions": [
            {
                "key": "bad", "name": "防火墙登录页", "address": "https://firewall.example.test/login",
                "error_msg": "连接超时", "check_time": "2026-09-07T10:29:45",
            },
        ],
        "trends": [
            {"key": "ok", "name": "办公门户", "up": 143, "total": 144, "avg_latency": 19.7, "last_check_time": "2026-09-07T10:30:00"},
        ],
        "hourly_history": {
            "bucket_minutes": 60,
            "hours": ["2026-09-07T08:00:00", "2026-09-07T09:00:00", "2026-09-07T10:00:00"],
            "buckets": [
                {"key": "ok", "hour_start": "2026-09-07T08:00:00", "up": 1, "down": 0, "other": 0, "total": 1},
                {"key": "ok", "hour_start": "2026-09-07T09:00:00", "up": 1, "down": 0, "other": 0, "total": 1},
                {"key": "ok", "hour_start": "2026-09-07T10:00:00", "up": 1, "down": 0, "other": 0, "total": 1},
                {"key": "bad", "hour_start": "2026-09-07T08:00:00", "up": 1, "down": 0, "other": 0, "total": 1},
                {"key": "bad", "hour_start": "2026-09-07T09:00:00", "up": 1, "down": 1, "other": 0, "total": 2},
            ],
        },
        "today_inspection": {"run_id": "run-1", "status": "finished", "total": 2, "completed": 2, "up": 1, "down": 1},
    }


class WallViewStateTests(unittest.TestCase):
    """The wall derives only display values from the shared snapshot."""

    def test_prioritizes_down_then_unknown_targets_and_calculates_known_availability(self) -> None:
        state = wall_view_state(_snapshot(), target_limit=2)

        self.assertEqual([target["name"] for target in state["targets"]], ["防火墙登录页", "业务数据库"])
        self.assertEqual(state["availability"], "67%")
        self.assertEqual(state["hidden_target_count"], 1)

    def test_uses_a_clear_placeholder_when_no_target_has_a_known_result(self) -> None:
        snapshot = _snapshot()
        snapshot["status_counts"] = {"total": 3, "up": 0, "down": 0, "unknown": 3}
        state = wall_view_state(snapshot)

        self.assertEqual(state["availability"], "--")
        self.assertEqual(state["incident_count"], 0)

    def test_uses_real_hourly_history_in_the_same_priority_order_as_target_cards(self) -> None:
        state = wall_view_state(_snapshot())

        heatmap = state["heatmap"]
        self.assertEqual(heatmap["bucket_labels"], ["08:00", "09:00", "10:00"])
        self.assertEqual([row["name"] for row in heatmap["rows"]], ["防火墙登录页", "业务数据库", "办公门户"])
        self.assertEqual(heatmap["rows"][0]["points"], ["up", "mixed", None])


class MonitorWallPanelTests(unittest.TestCase):
    """The wall can render a shared snapshot without I/O or a worker thread."""

    def test_renders_metrics_incident_focus_and_daily_inspection(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            wall = MonitorWallPanel(
                root,
                on_back=lambda: None,
                on_filter_status=lambda _state: None,
                on_open_target=lambda _name: None,
                on_toggle_fullscreen=lambda: None,
                on_check_now=lambda: None,
                on_check_websites=lambda: None,
                on_refresh=lambda: None,
            )
            wall.pack(fill=tk.BOTH, expand=True)
            wall.refresh(_snapshot(), monitoring_running=True)
            root.update_idletasks()

            self.assertEqual(wall.metric_values["down"].get(), "1")
            self.assertEqual(wall.availability_var.get(), "67%")
            self.assertIn("异常 1", wall.inspection_var.get())
            self.assertEqual(len(wall.incident_rows), 1)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
