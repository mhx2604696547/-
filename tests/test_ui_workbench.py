"""Small, display-safe checks for the personal operations workbench UI."""
from __future__ import annotations

import tkinter as tk
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.gui.dashboard import DashboardPanel, inspection_footer_state
from app.gui.main_window import ABOUT_MESSAGE, MainWindow
from app.gui.theme import PALETTE, apply_workbench_theme


class WorkbenchThemeTests(unittest.TestCase):
    """The chosen light workbench visual language remains intentional."""

    def test_light_workbench_theme_registers_primary_and_surface_styles(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            style = apply_workbench_theme(root)
            self.assertEqual(style.lookup("Workbench.TFrame", "background"), PALETTE["workspace"])
            self.assertEqual(style.lookup("Primary.TButton", "background"), PALETTE["primary"])
        finally:
            root.destroy()


class InspectionFooterStateTests(unittest.TestCase):
    """The persistent daily checklist bar must communicate useful next steps."""

    def test_not_started_inspection_has_zero_progress_and_no_actions(self) -> None:
        state = inspection_footer_state(
            {"run_id": None, "status": "not_started", "total": 3, "completed": 0, "up": 0, "down": 0}
        )

        self.assertEqual(state["text"], "今日网页巡检 · 待检 3 项")
        self.assertEqual(state["progress"], 0)
        self.assertFalse(state["can_retry"])
        self.assertFalse(state["can_export"])

    def test_running_inspection_exposes_progress_and_failed_item_retry(self) -> None:
        state = inspection_footer_state(
            {"run_id": "run-1", "status": "running", "total": 8, "completed": 6, "up": 5, "down": 1}
        )

        self.assertEqual(state["text"], "今日网页巡检 · 进行中 6/8 · 正常 5 · 异常 1")
        self.assertEqual(state["progress"], 75)
        self.assertTrue(state["can_retry"])
        self.assertTrue(state["can_export"])

    def test_finished_empty_checklist_is_complete_without_division_error(self) -> None:
        state = inspection_footer_state(
            {"run_id": "run-empty", "status": "finished", "total": 0, "completed": 0, "up": 0, "down": 0}
        )

        self.assertEqual(state["progress"], 100)
        self.assertEqual(state["text"], "今日网页巡检 · 已完成 · 正常 0 · 异常 0")


class DashboardPresentationTests(unittest.TestCase):
    """A dashboard renders an at-a-glance operational summary without I/O."""

    def test_signal_desk_prioritizes_actionable_exceptions(self) -> None:
        """A single exception should be called out before secondary trend data."""
        root = tk.Tk()
        root.withdraw()
        try:
            apply_workbench_theme(root)
            dashboard = DashboardPanel(root, lambda _state: None, lambda _name: None, lambda: None, lambda: None)
            dashboard.pack(fill=tk.BOTH, expand=True)
            dashboard.refresh(
                {
                    "generated_at": "2026-09-07T10:30:00",
                    "status_counts": {"total": 4, "up": 2, "down": 1, "unknown": 1},
                    "exceptions": [{
                        "key": "firewall|https://fw.example.test|http", "name": "防火墙登录页",
                        "address": "https://fw.example.test", "error_msg": "HTTP 503",
                        "check_time": "2026-09-07T10:29:00",
                    }],
                    "trends": [],
                    "today_inspection": {
                        "run_id": "run-2", "status": "finished", "total": 2, "completed": 2, "up": 1, "down": 1,
                    },
                },
                monitoring_running=True,
            )
            root.update_idletasks()

            self.assertEqual(dashboard.exception_count_var.get(), "1 项异常待处理")
            self.assertEqual(dashboard.exception_notice_var.get(), "双击异常项可定位到目标列表")
            self.assertIn("防火墙登录页", dashboard.exception_tree.item(
                dashboard.exception_tree.get_children()[0], "values"
            ))
        finally:
            root.destroy()

    def test_dashboard_renders_lightweight_operational_summary(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            apply_workbench_theme(root)
            dashboard = DashboardPanel(root, lambda _state: None, lambda _name: None, lambda: None, lambda: None)
            dashboard.pack(fill=tk.BOTH, expand=True)
            dashboard.refresh(
                {
                    "generated_at": "2026-09-07T10:30:00",
                    "status_counts": {"total": 4, "up": 2, "down": 1, "unknown": 1},
                    "exceptions": [],
                    "trends": [],
                    "today_inspection": {
                        "run_id": "run-2", "status": "finished", "total": 2, "completed": 2, "up": 1, "down": 1,
                    },
                },
                monitoring_running=True,
            )
            root.update_idletasks()

            self.assertEqual(dashboard.card_values["down"].get(), "1")
            self.assertEqual(dashboard.operating_state_var.get(), "监控服务运行中")
            self.assertIn("2026-09-07 10:30:00", dashboard.updated_var.get())
        finally:
            root.destroy()


class MainWindowWorkbenchTests(unittest.TestCase):
    """The full shell keeps navigation and the daily checklist visible."""

    def test_sidebar_filter_and_persistent_daily_footer_render_in_main_window(self) -> None:
        class FakeTray:
            def __init__(self, *_args: object) -> None:
                pass

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

        class FakeMonitor:
            is_running = False
            last_inspection_run_id: str | None = None

            def shutdown(self) -> None:
                pass

        target = {
            "name": "设备登录页", "address": "https://device.example.test", "check_type": "http",
            "interval": 60, "device_role": "security_device",
        }
        target_key = "设备登录页|https://device.example.test|http"
        snapshot = {
            "generated_at": "2026-09-07T10:30:00",
            "status_counts": {"total": 1, "up": 1, "down": 0, "unknown": 0},
            "targets": [{
                "key": target_key, "name": target["name"], "address": target["address"], "check_type": "http",
                "status": "up", "stale": False, "latency": 12.0, "error_msg": "", "detail": "HTTP 200",
                "check_time": "2026-09-07T10:30:00",
            }],
            "exceptions": [], "trends": [],
            "today_inspection": {
                "run_id": "run-3", "status": "finished", "total": 1, "completed": 1, "up": 1, "down": 0,
            },
        }

        class FakeDatabase:
            def get_dashboard_snapshot(self, _targets: list[dict[str, object]]) -> dict[str, object]:
                return snapshot

        root = tk.Tk()
        root.withdraw()
        try:
            with patch("app.gui.main_window.TrayIcon", FakeTray):
                window = MainWindow(
                    root, [target], {}, FakeDatabase(), FakeMonitor(), queue.Queue(), lambda *_args: None, Mock(),
                )
            root.geometry("1000x650")
            root.update_idletasks()
            self.assertEqual(window._active_surface, "wall")
            self.assertEqual(window.monitor_wall.winfo_manager(), "pack")
            window.monitor.is_running = True
            window.sync_monitor_state()
            self.assertEqual(window.status_var.get(), "监控运行中")
            window._wall_fullscreen = True
            window._handle_escape(Mock())
            self.assertEqual(window._active_surface, "workbench")
            self.assertFalse(window._wall_fullscreen)
            window.show_monitor_wall()
            window.show_dashboard()
            self.assertEqual(window._active_surface, "workbench")
            window._handle_f11(Mock())
            self.assertEqual(window._active_surface, "wall")
            self.assertTrue(window._wall_fullscreen)
            window._handle_f11(Mock())
            self.assertEqual(window._active_surface, "wall")
            self.assertFalse(window._wall_fullscreen)
            window._handle_escape(Mock())
            window._filter_targets_by_category("web")

            self.assertEqual(window.target_filter_var.get(), "网页 / 设备页面")
            self.assertEqual(window.target_count_var.get(), "1 个目标")
            self.assertEqual(window.inspection_footer_var.get(), "今日网页巡检 · 已完成 · 正常 1 · 异常 0")
            self.assertIn("web", window._nav_items)
            window.show_dashboard()
            root.update_idletasks()
            footer_bottom = window.export_footer_button.winfo_rooty() + window.export_footer_button.winfo_height()
            self.assertLessEqual(footer_bottom - root.winfo_rooty(), root.winfo_height())
        finally:
            root.destroy()


class LiveResultOverlayTests(unittest.TestCase):
    """Temporary results must not hide a stale persisted state forever."""

    def test_discards_an_expired_unsaved_result_before_rendering_snapshot(self) -> None:
        target = {"name": "办公门户", "address": "https://portal.example.test", "check_type": "http", "interval": 60}
        target_key = "办公门户|https://portal.example.test|http"
        window = MainWindow.__new__(MainWindow)
        window.targets = [target]
        window._live_storage_errors = {target_key: "database is locked"}
        window._live_results = {
            target_key: {
                "status": "up", "latency": 12.0, "error_msg": "", "detail": "HTTP 200",
                "check_time": "2000-01-01T00:00:00",
            }
        }
        snapshot = {
            "targets": [{
                "key": target_key, "name": target["name"], "address": target["address"], "check_type": "http",
                "status": "unknown", "stale": True, "latency": None, "error_msg": "", "detail": "", "check_time": "",
            }],
        }

        window._apply_live_results(snapshot)

        self.assertEqual(window._live_results, {})
        self.assertEqual(window._live_storage_errors, {})
        self.assertEqual(snapshot["status_counts"], {"total": 1, "up": 0, "down": 0, "unknown": 1})


class StorageErrorStateTests(unittest.TestCase):
    """A successful later refresh must remove a past snapshot-read error."""

    def test_successful_snapshot_refresh_clears_only_the_snapshot_error(self) -> None:
        snapshot = {
            "targets": [], "today_inspection": {},
            "status_counts": {"total": 0, "up": 0, "down": 0, "unknown": 0},
            "exceptions": [], "trends": [],
        }

        class FlakyDatabase:
            def __init__(self) -> None:
                self.calls = 0

            def get_dashboard_snapshot(self, _targets: list[dict[str, object]]) -> dict[str, object]:
                self.calls += 1
                if self.calls == 1:
                    raise OSError("database is locked")
                return snapshot

        window = MainWindow.__new__(MainWindow)
        window.targets = []
        window.database = FlakyDatabase()
        window.monitor = SimpleNamespace(is_running=False)
        window.logger = Mock()
        window._history_rows = {}
        window._live_results = {}
        window._live_storage_errors = {}
        window._inspection_storage_error = ""
        window._snapshot_read_error = ""
        window._last_snapshot = None
        window._apply_live_results = lambda _snapshot: None
        window._render_inspection_footer = lambda _inspection: None
        window.dashboard = Mock()
        window.monitor_wall = Mock()

        window._refresh_dashboard()
        self.assertIn("无法读取数据面板", window._storage_error_message())
        window._refresh_dashboard()

        self.assertEqual(window._storage_error_message(), "")
        self.assertEqual(window.dashboard.refresh.call_args.args[2], "")


class EventBatchingTests(unittest.TestCase):
    """A burst of worker results should repaint the UI only once per poll."""

    def test_polling_two_results_refreshes_the_snapshot_once(self) -> None:
        class Value:
            def __init__(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        target_one = {"name": "门户", "address": "https://portal.example.test", "check_type": "http", "interval": 60}
        target_two = {"name": "交换机", "address": "192.168.1.1", "check_type": "ping", "interval": 60}
        events: queue.Queue[dict[str, object]] = queue.Queue()
        for target in (target_one, target_two):
            events.put({
                "event": "result", "target": target,
                "result": {"status": "up", "latency": 1.0, "error_msg": "", "check_time": "2026-09-07T10:30:00"},
            })
        window = MainWindow.__new__(MainWindow)
        window._closing = False
        window.targets = [target_one, target_two]
        window.event_queue = events
        window.monitor = SimpleNamespace(is_running=True)
        window.status_var = Value("监控未启动")
        window.root = SimpleNamespace(after=lambda *_args: None)
        window._live_results = {}
        window._live_storage_errors = {}
        window._refresh_dashboard = Mock()
        window._render_targets = Mock()

        window._poll_events()

        self.assertEqual(window._refresh_dashboard.call_count, 1)
        self.assertEqual(window._render_targets.call_count, 1)
        self.assertEqual(window.status_var.get(), "监控运行中")


class DesktopAlertUiBridgeTests(unittest.TestCase):
    """A worker result must become a desktop alert only on Tk's event path."""

    def test_result_transition_is_delivered_by_the_main_window(self) -> None:
        class Value:
            def __init__(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        target = {"name": "核心交换机", "address": "10.0.0.1", "check_type": "ping", "interval": 60}
        result = {"status": "down", "latency": None, "error_msg": "请求超时", "check_time": "2026-09-08T09:00:00"}
        window = MainWindow.__new__(MainWindow)
        window.targets = [target]
        window._live_results = {}
        window._live_storage_errors = {}
        window.monitor = SimpleNamespace(is_running=True)
        window.status_var = Value("监控未启动")
        window.desktop_alert_policy = SimpleNamespace(process=Mock(return_value="alert"))
        window._active_desktop_alerts = {}
        window._deliver_desktop_alert = Mock()

        window._update_result(target, result)

        window.desktop_alert_policy.process.assert_called_once_with(target, result)
        window._deliver_desktop_alert.assert_called_once_with("alert", target, result)

    def test_inflight_result_for_removed_target_is_ignored(self) -> None:
        """A queued worker result cannot resurrect a deleted target's alarm."""
        target = {"name": "旧交换机", "address": "10.0.0.2", "check_type": "ping", "interval": 60}
        result = {"status": "down", "latency": None, "error_msg": "请求超时", "check_time": "2026-09-08T09:00:00"}
        window = MainWindow.__new__(MainWindow)
        window.targets = []
        window._live_results = {}
        window._live_storage_errors = {}
        window.desktop_alert_policy = SimpleNamespace(process=Mock(return_value="alert"))
        window._deliver_desktop_alert = Mock()
        window.logger = Mock()

        window._update_result(target, result)

        window.desktop_alert_policy.process.assert_not_called()
        window._deliver_desktop_alert.assert_not_called()
        self.assertEqual(window._live_results, {})

    def test_inflight_result_uses_current_target_alert_setting(self) -> None:
        """A queued result must respect an alert switch just saved by the user."""
        queued_target = {"name": "核心交换机", "address": "10.0.0.1", "check_type": "ping", "alert_enabled": True}
        current_target = {**queued_target, "alert_enabled": False}
        result = {"status": "down", "latency": None, "error_msg": "请求超时", "check_time": "2026-09-08T09:00:00"}
        policy = SimpleNamespace(
            process=Mock(side_effect=lambda target, _result: "alert" if target["alert_enabled"] else None)
        )
        window = MainWindow.__new__(MainWindow)
        window.targets = [current_target]
        window._live_results = {}
        window._live_storage_errors = {}
        window.desktop_alert_policy = policy
        window._deliver_desktop_alert = Mock()
        window.monitor = SimpleNamespace(is_running=True)
        window.status_var = Mock()

        window._update_result(queued_target, result)

        policy.process.assert_called_once_with(current_target, result)
        window._deliver_desktop_alert.assert_not_called()

    def test_about_dialog_shows_personal_maintenance_information(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.root = object()

        with patch("app.gui.main_window.messagebox.showinfo") as show_info:
            window.show_about()

        show_info.assert_called_once_with("关于", ABOUT_MESSAGE, parent=window.root)
        self.assertIn("马海兴", ABOUT_MESSAGE)
        self.assertIn("19995372230", ABOUT_MESSAGE)

    def test_disabling_an_alerting_target_clears_its_desktop_alarm(self) -> None:
        target = {"name": "核心交换机", "address": "10.0.0.1", "check_type": "ping", "alert_enabled": False}
        target_key = "核心交换机|10.0.0.1|ping"
        window = MainWindow.__new__(MainWindow)
        window.targets = [target]
        window.settings = {"desktop_alert_enabled": True}
        window.save_config = Mock()
        window.desktop_alert_policy = SimpleNamespace(update_settings=Mock(), sync_targets=Mock())
        window._live_results = {}
        window._live_storage_errors = {}
        window._history_rows = {}
        window._active_desktop_alerts = {target_key: "核心交换机"}
        window._alarm_flash_generation = 4
        window.root = SimpleNamespace(title=Mock())
        window._normal_window_title = Mock(return_value="监控大屏 · 网络监控")
        window._flash_alarm_title = Mock()
        window._refresh_desktop_alert_visuals = Mock()
        window._refresh_dashboard = Mock()
        window._render_targets = Mock()

        with patch("app.gui.main_window.messagebox.showinfo"):
            window._save_config([target], {"desktop_alert_enabled": True})

        self.assertEqual(window._active_desktop_alerts, {})
        self.assertEqual(window._alarm_flash_generation, 5)
        window.root.title.assert_called_once_with("监控大屏 · 网络监控")

    def test_disabling_one_of_two_alerting_targets_stops_the_old_title_flash(self) -> None:
        disabled = {"name": "核心交换机", "address": "10.0.0.1", "check_type": "ping", "alert_enabled": False}
        remaining = {"name": "办公门户", "address": "https://portal.example.test", "check_type": "http", "alert_enabled": True}
        disabled_key = "核心交换机|10.0.0.1|ping"
        remaining_key = "办公门户|https://portal.example.test|http"
        window = MainWindow.__new__(MainWindow)
        window.targets = [disabled, remaining]
        window.settings = {"desktop_alert_enabled": True, "desktop_alert_flash_enabled": True}
        window.save_config = Mock()
        window.desktop_alert_policy = SimpleNamespace(update_settings=Mock(), sync_targets=Mock())
        window._live_results = {}
        window._live_storage_errors = {}
        window._history_rows = {}
        window._active_desktop_alerts = {disabled_key: "核心交换机", remaining_key: "办公门户"}
        window._alarm_flash_generation = 8
        window.root = SimpleNamespace(title=Mock())
        window._normal_window_title = Mock(return_value="监控大屏 · 网络监控")
        window._flash_alarm_title = Mock()
        window._refresh_desktop_alert_visuals = Mock()
        window._refresh_dashboard = Mock()
        window._render_targets = Mock()

        with patch("app.gui.main_window.messagebox.showinfo"):
            window._save_config([disabled, remaining], window.settings)

        self.assertEqual(window._active_desktop_alerts, {remaining_key: "办公门户"})
        self.assertEqual(window._alarm_flash_generation, 9)
        window.root.title.assert_called_once_with("监控大屏 · 网络监控")
        window._flash_alarm_title.assert_called_once_with("⚠ 断网告警 · 办公门户")

    def test_recovery_of_one_target_keeps_another_target_title_flash_active(self) -> None:
        recovered = {"name": "核心交换机", "address": "10.0.0.1", "check_type": "ping"}
        remaining_key = "办公门户|https://portal.example.test|http"
        window = MainWindow.__new__(MainWindow)
        window._active_desktop_alerts = {
            "核心交换机|10.0.0.1|ping": "核心交换机",
            remaining_key: "办公门户",
        }
        window._alarm_flash_generation = 12
        window.root = SimpleNamespace(title=Mock())
        window._normal_window_title = Mock(return_value="监控大屏 · 网络监控")
        window._refresh_desktop_alert_visuals = Mock()
        window.settings = {"send_recovery_notification": False, "desktop_alert_flash_enabled": True}
        window.logger = Mock()

        window._deliver_desktop_alert("recovery", recovered, {"status": "up"})

        self.assertEqual(window._active_desktop_alerts, {remaining_key: "办公门户"})
        self.assertEqual(window._alarm_flash_generation, 12)
        window.root.title.assert_not_called()


if __name__ == "__main__":
    unittest.main()
