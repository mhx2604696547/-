"""Tkinter main window for the personal network-monitoring workbench."""
from __future__ import annotations

import csv
import queue
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

try:  # ``winsound`` is part of Windows Python but not available on Linux.
    import winsound
except ImportError:  # pragma: no cover - exercised only on non-Windows hosts.
    winsound = None

from app.core.desktop_alert import DesktopAlertPolicy, as_bool
from app.gui.config_dialog import DEVICE_ROLE_LABELS, ConfigDialog
from app.gui.dashboard import DashboardPanel, inspection_footer_state
from app.gui.monitor_wall import MonitorWallPanel
from app.gui.port_test_dialog import PortTestDialog
from app.gui.theme import PALETTE, apply_workbench_theme
from app.gui.tray import TrayIcon
from app.utils.database import MonitorDatabase


STATUS_TEXT = {"up": "正常", "down": "异常", "unknown": "未知"}
ABOUT_MESSAGE = (
    "网络监控系统\n"
    "本机个人运维工作台\n\n"
    "功能：连通性监控、网页巡检、设备状态检测、断网声光告警\n\n"
    "开发与维护：马海兴\n"
    "联系电话：19995372230"
)


class MainWindow:
    """Presents current monitor state, daily web checks, and saved dashboard data."""

    def __init__(
        self,
        root: tk.Tk,
        targets: list[dict[str, Any]],
        settings: dict[str, Any],
        database: MonitorDatabase,
        monitor: Any,
        event_queue: queue.Queue[dict[str, Any]],
        save_config: Callable[[list[dict[str, Any]], dict[str, Any]], None],
        logger: Any,
    ) -> None:
        self.root, self.targets, self.settings = root, targets, settings
        self.database, self.monitor, self.event_queue = database, monitor, event_queue
        self.save_config, self.logger = save_config, logger
        self._closing = False
        self._filter_state: str | None = None
        self._filter_category: str | None = None
        # Only results SQLite could not save live here. Persisted results are
        # read back from SQLite so the normal stale-result rule still applies
        # after a laptop sleep or a long monitoring pause.
        self._live_results: dict[str, dict[str, Any]] = {}
        self._history_rows: dict[str, dict[str, Any]] = {}
        self._live_storage_errors: dict[str, str] = {}
        self._inspection_storage_error = ""
        self._snapshot_read_error = ""
        self._last_snapshot: dict[str, Any] | None = None
        self.desktop_alert_policy = DesktopAlertPolicy(settings)
        self._active_desktop_alerts: dict[str, str] = {}
        self._alarm_flash_generation = 0
        self._active_surface = "workbench"
        self._wall_fullscreen = False
        self._pages: dict[str, ttk.Frame] = {}
        self._nav_items: dict[str, tk.Label] = {}
        self._build_window()
        # Tray callbacks run on pystray's thread. They only enqueue commands;
        # every Tkinter call remains on the thread that created ``root``.
        self.tray = TrayIcon(
            lambda: self.event_queue.put({"event": "tray_show"}),
            lambda: self.event_queue.put({"event": "tray_exit"}),
            logger,
        )
        self.tray.start()
        self._refresh_dashboard()
        self._render_targets()
        self._poll_events()

    def _build_window(self) -> None:
        self.root.title("网络监控 · 个人运维工作台")
        self.root.minsize(860, 560)
        self._fit_window_to_screen()
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        apply_workbench_theme(self.root)
        self._build_menu()

        self.workbench_shell = ttk.Frame(self.root, style="Workbench.TFrame")
        self.workbench_shell.pack(fill=tk.BOTH, expand=True)
        self._build_topbar(self.workbench_shell)

        # Pack the persistent footer first so the expanding page body can
        # never consume its space on a shorter laptop display.
        body = ttk.Frame(self.workbench_shell, style="Workbench.TFrame")
        self._build_inspection_footer(self.workbench_shell)
        body.pack(fill=tk.BOTH, expand=True)
        self._build_sidebar(body)
        self.workspace = ttk.Frame(body, style="Workbench.TFrame")
        self.workspace.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.workspace.columnconfigure(0, weight=1)
        self.workspace.rowconfigure(0, weight=1)
        self._build_pages()
        self.monitor_wall = MonitorWallPanel(
            self.root,
            on_back=self.show_dashboard,
            on_filter_status=self._filter_targets_by_status,
            on_open_target=self._select_target,
            on_toggle_fullscreen=self._toggle_wall_fullscreen,
            on_check_now=self.check_all_targets,
            on_check_websites=self.check_websites_now,
            on_refresh=self._refresh_dashboard,
        )
        self._show_page("overview", "overview")
        self.root.bind("<F11>", self._handle_f11)
        self.root.bind("<Escape>", self._handle_escape)
        self._enter_monitor_wall()

    def _fit_window_to_screen(self) -> None:
        """Start centered and fully visible on ordinary laptop resolutions."""
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1240, max(860, screen_width - 32))
        height = min(760, max(560, screen_height - 64))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_menu(self) -> None:
        menu_options = {
            "background": PALETTE["surface"], "foreground": PALETTE["ink"],
            "activebackground": PALETTE["surface_muted"], "activeforeground": PALETTE["ink"],
        }
        menu = tk.Menu(self.root, **menu_options)
        file_menu = tk.Menu(menu, tearoff=False, **menu_options)
        file_menu.add_command(label="配置目标", command=self.open_config)
        file_menu.add_command(label="导出检测历史", command=self.export_report)
        file_menu.add_command(label="导出今日巡检", command=self.export_today_inspection)
        file_menu.add_command(label="最小化到托盘", command=self.minimize_to_tray)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.exit_application)
        menu.add_cascade(label="文件", menu=file_menu)
        tools_menu = tk.Menu(menu, tearoff=False, **menu_options)
        tools_menu.add_command(label="端口测试工具", command=self.open_port_test)
        menu.add_cascade(label="工具", menu=tools_menu)
        help_menu = tk.Menu(menu, tearoff=False, **menu_options)
        help_menu.add_command(label="关于", command=self.show_about)
        menu.add_cascade(label="帮助", menu=help_menu)
        self._menu = menu
        self.root.configure(menu=self._menu)

    def _build_topbar(self, parent: ttk.Frame) -> None:
        topbar = ttk.Frame(parent, style="Topbar.TFrame", padding=(20, 13))
        topbar.pack(fill=tk.X)
        brand = ttk.Frame(topbar, style="Topbar.TFrame")
        brand.pack(side=tk.LEFT)
        tk.Label(
            brand, text="NM", foreground="#FFFFFF", background=PALETTE["primary"],
            font=("Segoe UI", 10, "bold"), padx=8, pady=5,
        ).pack(side=tk.LEFT)
        brand_copy = ttk.Frame(brand, style="Topbar.TFrame")
        brand_copy.pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(brand_copy, text="网络监控", style="TopbarTitle.TLabel").pack(anchor="w")
        ttk.Label(brand_copy, text="个人运维工作台", style="TopbarSubtle.TLabel").pack(anchor="w")

        status_area = ttk.Frame(topbar, style="Topbar.TFrame")
        status_area.pack(side=tk.RIGHT)
        self.status_var = tk.StringVar(value="监控未启动")
        self.status_badge = ttk.Label(status_area, textvariable=self.status_var, style="StatusWarn.TLabel")
        self.status_badge.pack(side=tk.RIGHT)
        self.status_var.trace_add("write", self._sync_status_badge)

        actions = ttk.Frame(topbar, style="Topbar.TFrame")
        actions.pack(side=tk.RIGHT, padx=(0, 18))
        self.start_button = ttk.Button(actions, text="开始监控", command=self.start_monitoring, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(actions, text="停止", command=self.stop_monitoring, style="Secondary.TButton")
        self.stop_button.pack(side=tk.LEFT, padx=(7, 0))
        ttk.Button(actions, text="立即检测", command=self.check_now, style="Secondary.TButton").pack(side=tk.LEFT, padx=(7, 0))
        ttk.Button(actions, text="网页巡检", command=self.check_websites_now, style="Primary.TButton").pack(side=tk.LEFT, padx=(7, 0))

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        sidebar = ttk.Frame(parent, style="Sidebar.TFrame", width=205)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        tk.Label(
            sidebar, text="工作台", anchor="w", background=PALETTE["sidebar"], foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 11, "bold"), padx=19, pady=18,
        ).pack(fill=tk.X)
        self._add_nav_item(sidebar, "wall", "监控大屏", self.show_monitor_wall)
        self._add_nav_item(sidebar, "overview", "概览", self.show_dashboard)
        self._add_nav_item(sidebar, "targets", "监控目标", self.show_targets)

        self._nav_group(sidebar, "快捷视图")
        self._add_nav_item(sidebar, "all", "全部目标", lambda: self._filter_targets_by_status(None, nav_key="all"))
        self._add_nav_item(sidebar, "web", "网页 / 设备页面", lambda: self._filter_targets_by_category("web"))
        self._add_nav_item(sidebar, "network", "网络与端口", lambda: self._filter_targets_by_category("network"))
        self._add_nav_item(sidebar, "alerts", "仅看异常", lambda: self._filter_targets_by_status("down", nav_key="alerts"))

        bottom = tk.Frame(sidebar, background=PALETTE["sidebar"])
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=14)
        tk.Label(
            bottom, text="本机模式\n配置与历史均保存在当前电脑", justify=tk.LEFT, anchor="w",
            background=PALETTE["sidebar"], foreground=PALETTE["sidebar_muted"], font=("Microsoft YaHei UI", 9),
            padx=9, pady=8,
        ).pack(fill=tk.X)
        config_link = tk.Label(
            bottom, text="管理目标与设置  ›", justify=tk.LEFT, anchor="w", cursor="hand2",
            background=PALETTE["sidebar"], foreground="#FFFFFF", font=("Microsoft YaHei UI", 9, "bold"), padx=9, pady=8,
        )
        config_link.pack(fill=tk.X, pady=(4, 0))
        config_link.bind("<Button-1>", lambda _event: self.open_config())

    def _nav_group(self, parent: ttk.Frame, text: str) -> None:
        tk.Label(
            parent, text=text, anchor="w", background=PALETTE["sidebar"], foreground=PALETTE["sidebar_muted"],
            font=("Microsoft YaHei UI", 8, "bold"), padx=19, pady=12,
        ).pack(fill=tk.X, pady=(11, 0))

    def _add_nav_item(self, parent: ttk.Frame, key: str, text: str, command: Callable[[], None]) -> None:
        label = tk.Label(
            parent, text=f"  {text}", anchor="w", cursor="hand2", background=PALETTE["sidebar"],
            foreground=PALETTE["sidebar_text"], font=("Microsoft YaHei UI", 10), padx=10, pady=9,
        )
        label.pack(fill=tk.X, padx=10, pady=1)
        label.bind("<Button-1>", lambda _event: command())
        self._nav_items[key] = label

    def _set_active_nav(self, key: str) -> None:
        for item_key, label in self._nav_items.items():
            active = item_key == key
            label.configure(
                background=PALETTE["sidebar_active"] if active else PALETTE["sidebar"],
                foreground="#FFFFFF" if active else PALETTE["sidebar_text"],
            )

    def _build_pages(self) -> None:
        self.list_tab = ttk.Frame(self.workspace, style="Workbench.TFrame", padding=(20, 18, 20, 14))
        self.dashboard = DashboardPanel(
            self.workspace,
            on_filter_status=self._filter_targets_by_status,
            on_open_target=self._select_target,
            on_retry_failures=self.retry_inspection_failures,
            on_export_today=self.export_today_inspection,
        )
        self._pages = {"overview": self.dashboard, "targets": self.list_tab}
        for page in self._pages.values():
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()
        self._build_target_page()

    def _build_target_page(self) -> None:
        self.list_tab.columnconfigure(0, weight=1)
        self.list_tab.rowconfigure(1, weight=1)
        header = ttk.Frame(self.list_tab, style="Workbench.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        header.columnconfigure(0, weight=1)
        title_stack = ttk.Frame(header, style="Workbench.TFrame")
        title_stack.grid(row=0, column=0, sticky="w")
        ttk.Label(title_stack, text="监控目标", style="PageTitle.TLabel").pack(anchor="w")
        subtitle = ttk.Frame(title_stack, style="Workbench.TFrame")
        subtitle.pack(anchor="w", pady=(4, 0))
        self.target_filter_var = tk.StringVar(value="全部目标")
        ttk.Label(subtitle, textvariable=self.target_filter_var, style="Filter.TLabel").pack(side=tk.LEFT)
        self.target_count_var = tk.StringVar(value="0 个目标")
        ttk.Label(subtitle, textvariable=self.target_count_var, style="TopbarSubtle.TLabel").pack(side=tk.LEFT, padx=(8, 0))
        actions = ttk.Frame(header, style="Workbench.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        ttk.Button(actions, text="管理目标", command=self.open_config, style="Secondary.TButton").pack(side=tk.LEFT)
        ttk.Button(actions, text="立即检测", command=self.check_now, style="Primary.TButton").pack(side=tk.LEFT, padx=(8, 0))

        table_card = ttk.Frame(self.list_tab, style="Card.TFrame", padding=(12, 12))
        table_card.grid(row=1, column=0, sticky="nsew")
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(0, weight=1)
        columns = ("name", "address", "check_type", "device_role", "status", "latency", "check_time", "error", "detail")
        self.tree = ttk.Treeview(table_card, columns=columns, show="headings")
        for key, title, width in (
            ("name", "名称", 140), ("address", "地址", 160), ("check_type", "类型", 66), ("device_role", "设备类型", 86),
            ("status", "状态", 86), ("latency", "延迟(ms)", 86), ("check_time", "最后检测时间", 150),
            ("error", "错误信息", 200), ("detail", "详情", 220),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=tk.CENTER if key in {"check_type", "device_role", "status", "latency"} else tk.W)
        vertical = ttk.Scrollbar(table_card, orient=tk.VERTICAL, command=self.tree.yview)
        horizontal = ttk.Scrollbar(table_card, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.tag_configure("up", foreground=PALETTE["success"])
        self.tree.tag_configure("down", foreground=PALETTE["danger"])
        self.tree.tag_configure("unknown", foreground=PALETTE["warning"])

    def _build_inspection_footer(self, parent: ttk.Frame) -> None:
        footer = ttk.Frame(parent, style="Footer.TFrame", padding=(20, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, text="每日巡检", style="FooterTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.inspection_footer_var = tk.StringVar(value="今日网页巡检 · 等待数据")
        ttk.Label(footer, textvariable=self.inspection_footer_var, style="FooterText.TLabel").grid(row=0, column=1, sticky="w", padx=(13, 15))
        self.inspection_progress_var = tk.IntVar(value=0)
        ttk.Progressbar(
            footer, variable=self.inspection_progress_var, maximum=100, length=210,
            style="Inspection.Horizontal.TProgressbar",
        ).grid(row=0, column=2, sticky="e", padx=(0, 14))
        self.retry_footer_button = ttk.Button(footer, text="重测失败项", command=self.retry_inspection_failures, style="Quiet.TButton", state=tk.DISABLED)
        self.retry_footer_button.grid(row=0, column=3, padx=(0, 7))
        self.export_footer_button = ttk.Button(footer, text="导出", command=self.export_today_inspection, style="Secondary.TButton", state=tk.DISABLED)
        self.export_footer_button.grid(row=0, column=4)

    def _sync_status_badge(self, *_args: object) -> None:
        if not hasattr(self, "status_badge"):
            return
        text = self.status_var.get()
        if "运行中" in text:
            style = "StatusGood.TLabel"
        elif any(word in text for word in ("失败", "错误", "异常", "告警")):
            style = "StatusBad.TLabel"
        else:
            style = "StatusWarn.TLabel"
        self.status_badge.configure(style=style)

    def _enter_monitor_wall(self) -> None:
        """Show the focused dark wall while retaining the same root window."""
        if self._active_surface == "wall":
            return
        self.workbench_shell.pack_forget()
        self.monitor_wall.pack(fill=tk.BOTH, expand=True)
        self.root.configure(menu="")
        self.root.title("监控大屏 · 网络监控")
        self._active_surface = "wall"
        self._set_active_nav("wall")

    def _show_workbench(self) -> None:
        """Return from the wall to configuration and target-management pages."""
        if self._active_surface != "wall":
            return
        if self._wall_fullscreen:
            self.root.attributes("-fullscreen", False)
            self._wall_fullscreen = False
        self.monitor_wall.pack_forget()
        self.workbench_shell.pack(fill=tk.BOTH, expand=True)
        self.root.configure(menu=self._menu)
        self.root.title("网络监控 · 个人运维工作台")
        self._active_surface = "workbench"

    def show_monitor_wall(self) -> None:
        """Open the read-only wall without creating a second monitor or thread."""
        self._enter_monitor_wall()
        self._refresh_dashboard()

    def _toggle_wall_fullscreen(self) -> None:
        if self._active_surface != "wall":
            self._enter_monitor_wall()
        self._wall_fullscreen = not self._wall_fullscreen
        self.root.attributes("-fullscreen", self._wall_fullscreen)

    def _handle_f11(self, _event: tk.Event[tk.Misc]) -> str:
        if self._active_surface != "wall":
            self._enter_monitor_wall()
            self._wall_fullscreen = True
            self.root.attributes("-fullscreen", True)
        else:
            self._toggle_wall_fullscreen()
        return "break"

    def _handle_escape(self, _event: tk.Event[tk.Misc]) -> str | None:
        if self._active_surface != "wall":
            return None
        if self._wall_fullscreen:
            self._wall_fullscreen = False
            self.root.attributes("-fullscreen", False)
        self.show_dashboard()
        return "break"

    def _show_page(self, page_name: str, nav_key: str) -> None:
        self._show_workbench()
        for name, page in self._pages.items():
            if name != page_name:
                page.grid_remove()
        self._pages[page_name].grid()
        self._set_active_nav(nav_key)

    def show_targets(self) -> None:
        self._show_workbench()
        self._filter_state = None
        self._filter_category = None
        self._render_targets()
        self._show_page("targets", "targets")

    @staticmethod
    def _target_id(target: dict[str, Any]) -> str:
        return MonitorDatabase.target_key(target)

    @staticmethod
    def _role_label(target: dict[str, Any]) -> str:
        return DEVICE_ROLE_LABELS.get(str(target.get("device_role", "other")), DEVICE_ROLE_LABELS["other"])

    def _target_view(self, target: dict[str, Any]) -> dict[str, Any]:
        target_id = self._target_id(target)
        live = self._live_results.get(target_id)
        if live:
            return {"status": str(live.get("status", "unknown")), **live}
        history = self._history_rows.get(target_id)
        if history:
            return history
        return {"status": "unknown", "latency": None, "error_msg": "", "detail": "", "check_time": ""}

    def _render_targets(self) -> None:
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        visible_count = 0
        for target in self.targets:
            check_type = str(target.get("check_type", "")).lower()
            if self._filter_category == "web" and check_type != "http":
                continue
            if self._filter_category == "network" and check_type == "http":
                continue
            view = self._target_view(target)
            state = str(view.get("status", "unknown"))
            if state not in STATUS_TEXT:
                state = "unknown"
            if self._filter_state is not None and state != self._filter_state:
                continue
            latency = "-"
            if view.get("latency") is not None:
                try:
                    latency = f"{float(view['latency']):.2f}"
                except (TypeError, ValueError):
                    latency = str(view["latency"])
            self.tree.insert("", tk.END, iid=self._target_id(target), values=(
                target["name"], target["address"], check_type.upper(), self._role_label(target),
                f"● {STATUS_TEXT[state]}", latency, str(view.get("check_time", "")).replace("T", " ") or "-",
                view.get("error_msg", ""), view.get("detail", ""),
            ), tags=(state,))
            visible_count += 1
        self.target_count_var.set(f"{visible_count} 个目标")
        self.target_filter_var.set(self._target_filter_label())
        for item_id in selected:
            if self.tree.exists(item_id):
                self.tree.selection_add(item_id)

    def _target_filter_label(self) -> str:
        if self._filter_category == "web":
            return "网页 / 设备页面"
        if self._filter_category == "network":
            return "网络与端口"
        return {None: "全部目标", "up": "仅正常", "down": "仅异常", "unknown": "未知 / 过期"}[self._filter_state]

    def _refresh_dashboard(self) -> None:
        """Read saved data on Tk's thread and overlay unpersisted live results."""
        try:
            snapshot = self.database.get_dashboard_snapshot(self.targets)
            self._snapshot_read_error = ""
            self._history_rows = {item["key"]: item for item in snapshot["targets"]}
            self._apply_live_results(snapshot)
            self._last_snapshot = snapshot
            storage_error = self._storage_error_message()
            self.dashboard.refresh(snapshot, self.monitor.is_running, storage_error)
            self.monitor_wall.refresh(snapshot, self.monitor.is_running, storage_error)
            self._render_inspection_footer(snapshot["today_inspection"])
        except Exception as error:
            self.logger.exception("刷新数据面板失败")
            self._snapshot_read_error = f"无法读取数据面板：{error}"

    def _storage_error_message(self) -> str:
        """Prioritize the error that currently prevents the freshest UI state."""
        if self._snapshot_read_error:
            return self._snapshot_read_error
        if self._live_storage_errors:
            return next(iter(self._live_storage_errors.values()))
        return self._inspection_storage_error

    def _render_inspection_footer(self, inspection: dict[str, Any]) -> None:
        state = inspection_footer_state(inspection)
        self.inspection_footer_var.set(state["text"])
        self.inspection_progress_var.set(int(state["progress"]))
        self.retry_footer_button.configure(state=tk.NORMAL if state["can_retry"] else tk.DISABLED)
        self.export_footer_button.configure(state=tk.NORMAL if state["can_export"] else tk.DISABLED)

    def _apply_live_results(self, snapshot: dict[str, Any]) -> None:
        """Keep a just-completed check visible even if SQLite was temporarily locked."""
        target_rows = {item["key"]: item for item in snapshot["targets"]}
        configured_targets = {self._target_id(target): target for target in self.targets}
        expired_live_results: list[str] = []
        for target_id, result in self._live_results.items():
            row = target_rows.get(target_id)
            target = configured_targets.get(target_id)
            if row is None or target is None:
                expired_live_results.append(target_id)
                continue
            if MonitorDatabase.is_result_stale(target, str(result.get("check_time", "")), datetime.now()):
                expired_live_results.append(target_id)
                continue
            status = str(result.get("status", "unknown"))
            row.update({
                "status": status if status in {"up", "down"} else "unknown", "stale": False,
                "latency": result.get("latency"), "error_msg": result.get("error_msg", ""),
                "detail": result.get("detail", ""), "check_time": result.get("check_time", ""),
            })
        for target_id in expired_live_results:
            self._live_results.pop(target_id, None)
            self._live_storage_errors.pop(target_id, None)
        counts = {"total": len(snapshot["targets"]), "up": 0, "down": 0, "unknown": 0}
        exceptions: list[dict[str, Any]] = []
        for row in snapshot["targets"]:
            state = row["status"] if row["status"] in {"up", "down"} else "unknown"
            counts[state] += 1
            if state == "down":
                exceptions.append(row)
        snapshot["status_counts"] = counts
        snapshot["exceptions"] = sorted(exceptions, key=lambda item: item.get("check_time", ""), reverse=True)

    def start_monitoring(self) -> None:
        if self.monitor.start():
            self.status_var.set("监控运行中")
        else:
            self.status_var.set("正在停止上一轮监控，请稍后再试")
        self._refresh_dashboard()

    def sync_monitor_state(self) -> None:
        """Reflect an engine start that happened during application bootstrap."""
        if self.monitor.is_running:
            self.status_var.set("监控运行中")
        self._refresh_dashboard()

    def stop_monitoring(self) -> None:
        self.monitor.stop()
        self.status_var.set("正在停止监控")
        self._refresh_dashboard()

    def check_now(self) -> None:
        selection = self.tree.selection()
        if selection:
            target_name = self.tree.item(selection[0], "values")[0]
            count = self.monitor.check_now(target_name)
            self.status_var.set(f"正在检测：{target_name}" if count else f"检测已在进行：{target_name}")
        else:
            count = self.monitor.check_now()
            self.status_var.set(f"正在检测 {count} 个目标" if count else "全部目标均在检测中")

    def check_all_targets(self) -> None:
        """Wall action: always check the full fleet, not a stale table selection."""
        count = self.monitor.check_now()
        self.status_var.set(f"正在检测 {count} 个目标" if count else "全部目标均在检测中")

    def check_websites_now(self) -> None:
        """Run each enabled HTTP target as the user's persisted daily checklist."""
        count = self.monitor.check_websites_now()
        if count:
            self.status_var.set(f"正在网页巡检：{count} 个网站/设备页面")
            if self._active_surface == "wall":
                self._refresh_dashboard()
            else:
                self.show_dashboard()
        else:
            messagebox.showinfo(
                "网页巡检", "没有可立即巡检的 HTTP 目标；请检查是否已配置，或目标是否正在检测中。", parent=self.root,
            )

    def retry_inspection_failures(self) -> None:
        run_id = self.dashboard.current_run_id or self.monitor.last_inspection_run_id
        count = self.monitor.retry_inspection_failures(run_id)
        if count:
            self.status_var.set(f"正在重测 {count} 个网页巡检失败项")
        else:
            messagebox.showinfo("重测失败项", "当前巡检没有可重测的失败项，或目标仍在检测中。", parent=self.root)

    def show_dashboard(self) -> None:
        self._refresh_dashboard()
        self._show_page("overview", "overview")

    def _filter_targets_by_status(self, state: str | None, nav_key: str = "targets") -> None:
        self._filter_state = state
        self._filter_category = None
        self._render_targets()
        self._show_page("targets", nav_key)
        label = {None: "全部", "up": "正常", "down": "异常", "unknown": "未知/过期"}[state]
        self.status_var.set(f"已筛选：{label}目标")

    def _filter_targets_by_category(self, category: str) -> None:
        self._filter_state = None
        self._filter_category = category
        self._render_targets()
        self._show_page("targets", category)
        label = "网页 / 设备页面" if category == "web" else "网络与端口"
        self.status_var.set(f"已筛选：{label}")

    def _select_target(self, target_name: str) -> None:
        self._filter_state = None
        self._filter_category = None
        self._render_targets()
        target = next((item for item in self.targets if item.get("name") == target_name), None)
        if target:
            target_id = self._target_id(target)
            self.tree.selection_set(target_id)
            self.tree.focus(target_id)
            self.tree.see(target_id)
        self._show_page("targets", "targets")

    def _poll_events(self) -> None:
        if self._closing:
            return
        needs_refresh = False
        needs_target_render = False
        try:
            while True:
                event = self.event_queue.get_nowait()
                event_type = event.get("event")
                if event_type == "result":
                    self._update_result(event["target"], event["result"], str(event.get("storage_error", "")))
                    needs_refresh = True
                    needs_target_render = True
                elif event_type == "inspection_started":
                    self._inspection_storage_error = ""
                    action = "正在重测网页巡检失败项" if event.get("retry") else "网页巡检已开始"
                    self.status_var.set(f"{action}（{event.get('total', 0)} 项）")
                    needs_refresh = True
                elif event_type == "inspection_progress":
                    self._inspection_storage_error = ""
                    summary = event["summary"]
                    self.status_var.set(f"网页巡检进度：{summary['completed']}/{summary['total']}，异常 {summary['down']} 项")
                    needs_refresh = True
                elif event_type == "inspection_finished":
                    self._inspection_storage_error = ""
                    summary = event["summary"]
                    self.status_var.set(f"网页巡检完成：正常 {summary['up']}，异常 {summary['down']}")
                    needs_refresh = True
                elif event_type == "inspection_error":
                    self._inspection_storage_error = str(event.get("message", "巡检数据保存失败"))
                    needs_refresh = True
                elif event_type == "tray_show":
                    self._restore_window()
                elif event_type == "tray_exit":
                    self.exit_application()
        except queue.Empty:
            pass
        if self.status_var.get() == "正在停止监控" and not self.monitor.is_running:
            self.status_var.set("监控已停止")
            needs_refresh = True
        if needs_refresh:
            self._refresh_dashboard()
            if needs_target_render:
                self._render_targets()
        self.root.after(200, self._poll_events)

    def _update_result(self, target: dict[str, Any], result: dict[str, Any], storage_error: str = "") -> None:
        target_id = self._target_id(target)
        # A check can finish after the user has deleted or edited its target.
        # Resolve it back to the current saved configuration before either
        # drawing it or feeding it into the local alert state machine.  This
        # prevents an already queued worker result from resurrecting a deleted
        # target or bypassing an alert switch the user just turned off.
        current_target = next(
            (configured for configured in self.targets if self._target_id(configured) == target_id),
            None,
        )
        if current_target is None:
            self.logger.debug("忽略已删除目标的延迟检测结果：%s", target.get("name", "未知目标"))
            return
        target = current_target
        if storage_error:
            self._live_results[target_id] = result.copy()
            self._live_storage_errors[target_id] = storage_error
        else:
            self._live_results.pop(target_id, None)
            self._live_storage_errors.pop(target_id, None)

        transition: str | None = None
        policy = getattr(self, "desktop_alert_policy", None)
        if policy is not None:
            try:
                transition = policy.process(target, result)
            except Exception:
                self.logger.exception("处理本机声光告警状态失败：%s", target.get("name", "未知"))
        if transition:
            self._deliver_desktop_alert(transition, target, result)

        if not getattr(self, "_active_desktop_alerts", {}) and self.monitor.is_running:
            self.status_var.set("监控运行中")
        elif not getattr(self, "_active_desktop_alerts", {}) and self.status_var.get() == "正在停止监控":
            self.status_var.set("监控已停止")

    def _deliver_desktop_alert(self, transition: str, target: dict[str, Any], result: dict[str, Any]) -> None:
        """Deliver a policy transition on Tk's main thread only."""
        target_id = self._target_id(target)
        name = str(target.get("name") or "未知目标")
        address = str(target.get("address") or "")
        if transition == "alert":
            self._active_desktop_alerts[target_id] = name
            message = self._refresh_desktop_alert_visuals()
            self._play_desktop_alert_sound()
            if as_bool(self.settings.get("desktop_alert_flash_enabled", True)):
                self._flash_alarm_title(f"⚠ 断网告警 · {name}")
                detail = str(result.get("error_msg") or "连接失败")
                self._notify_tray("网络断开告警", f"{name} {address}\n{detail}".strip())
            self.logger.warning("本机声光告警：%s（%s）", name, address)
            return

        if transition == "recovery":
            was_alerting = self._active_desktop_alerts.pop(target_id, None) is not None
            if was_alerting and not self._active_desktop_alerts:
                self._alarm_flash_generation += 1
                self.root.title(self._normal_window_title())
            self._refresh_desktop_alert_visuals()
            if was_alerting and as_bool(self.settings.get("send_recovery_notification", True)) and as_bool(
                self.settings.get("desktop_alert_flash_enabled", True)
            ):
                self._notify_tray("网络恢复", f"{name} {address}\n已恢复正常".strip())
            if was_alerting:
                self.logger.info("本机告警恢复：%s（%s）", name, address)

    def _refresh_desktop_alert_visuals(self) -> str:
        """Keep the workbench badge and the monitoring wall in the same state."""
        names = list(self._active_desktop_alerts.values())
        if names:
            summary = f"{names[0]} 网络不可达" if len(names) == 1 else f"{len(names)} 项网络异常"
            self.status_var.set(f"⚠ 本机声光告警：{summary}")
            message = f"⚠ 断网告警：{summary}"
        else:
            message = ""
            if self.monitor.is_running:
                self.status_var.set("监控运行中")
            elif self.status_var.get() == "正在停止监控":
                self.status_var.set("监控已停止")

        wall = getattr(self, "monitor_wall", None)
        set_alert = getattr(wall, "set_desktop_alert", None)
        if callable(set_alert):
            set_alert(message or None)
        return message

    def _play_desktop_alert_sound(self) -> None:
        """Play an asynchronous Windows system sound, with a Tk fallback."""
        if not as_bool(self.settings.get("desktop_alert_sound_enabled", True)):
            return
        try:
            if winsound is not None:
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            else:
                self.root.bell()
        except (OSError, RuntimeError, tk.TclError):
            self.logger.debug("播放本机告警声音失败", exc_info=True)

    def _notify_tray(self, title: str, message: str) -> None:
        notify = getattr(getattr(self, "tray", None), "notify", None)
        if callable(notify):
            notify(title, message)

    def _normal_window_title(self) -> str:
        return "监控大屏 · 网络监控" if self._active_surface == "wall" else "网络监控 · 个人运维工作台"

    def _flash_alarm_title(self, alert_title: str, flashes: int = 8) -> None:
        """Flash only the title text so alarms never create blocking dialogs."""
        self._alarm_flash_generation += 1
        generation = self._alarm_flash_generation

        def pulse(index: int = 0) -> None:
            if self._closing or generation != self._alarm_flash_generation:
                return
            self.root.title(alert_title if index % 2 == 0 else self._normal_window_title())
            if index < flashes:
                self.root.after(350, pulse, index + 1)
            else:
                self.root.title(self._normal_window_title())

        pulse()

    def show_about(self) -> None:
        """Show concise identity, support, and current desktop-alert information."""
        messagebox.showinfo("关于", ABOUT_MESSAGE, parent=self.root)

    def open_config(self) -> None:
        ConfigDialog(self.root, self.targets, self.settings, self._save_config)

    def open_port_test(self) -> None:
        PortTestDialog(self.root, self.logger)

    def _save_config(self, targets: list[dict[str, Any]], settings: dict[str, Any]) -> None:
        previous_settings = getattr(self, "settings", {})
        previous_flash_enabled = as_bool(previous_settings.get("desktop_alert_flash_enabled", True))
        self.targets, self.settings = targets, settings
        self.save_config(targets, settings)
        self.desktop_alert_policy.update_settings(settings)
        self.desktop_alert_policy.sync_targets(targets)
        valid_keys = {self._target_id(target) for target in targets}
        self._live_results = {key: result for key, result in self._live_results.items() if key in valid_keys}
        self._live_storage_errors = {
            key: error for key, error in self._live_storage_errors.items() if key in valid_keys
        }
        desktop_alert_keys = {
            self._target_id(target)
            for target in targets
            if as_bool(settings.get("desktop_alert_enabled", True))
            and as_bool(target.get("alert_enabled", True))
            and as_bool(target.get("desktop_alert_enabled", True))
        }
        previous_desktop_alerts = dict(self._active_desktop_alerts)
        self._active_desktop_alerts = {
            key: name
            for key, name in self._active_desktop_alerts.items()
            if key in valid_keys and key in desktop_alert_keys
        }
        if not as_bool(settings.get("desktop_alert_enabled", True)):
            self.desktop_alert_policy.reset()
            self._active_desktop_alerts.clear()
        active_alerts_changed = previous_desktop_alerts != self._active_desktop_alerts
        flash_enabled = as_bool(settings.get("desktop_alert_flash_enabled", True))
        if previous_desktop_alerts and (
            active_alerts_changed or not flash_enabled
        ):
            self._alarm_flash_generation += 1
            self.root.title(self._normal_window_title())
        if self._active_desktop_alerts and flash_enabled and (active_alerts_changed or not previous_flash_enabled):
            remaining_name = next(iter(self._active_desktop_alerts.values()))
            self._flash_alarm_title(f"⚠ 断网告警 · {remaining_name}")
        self._refresh_desktop_alert_visuals()
        self._history_rows = {}
        self._refresh_dashboard()
        self._render_targets()
        messagebox.showinfo("配置已保存", "监控目标和系统设置已保存。")

    def export_report(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root, title="导出检测历史", defaultextension=".csv", filetypes=[("CSV 文件", "*.csv")], initialfile="monitor_report.csv",
        )
        if not destination:
            return
        try:
            self._write_csv(
                Path(destination),
                ["名称", "地址", "检测类型", "状态", "延迟(ms)", "错误信息", "检测时间", "详情"],
                self.database.export_rows(),
            )
            messagebox.showinfo("导出完成", f"报告已导出至：\n{destination}", parent=self.root)
        except OSError as error:
            self.logger.exception("导出报告失败")
            messagebox.showerror("导出失败", str(error), parent=self.root)

    def export_today_inspection(self) -> None:
        run_id = self.dashboard.current_run_id or self.monitor.last_inspection_run_id
        if not run_id:
            messagebox.showinfo("导出今日巡检", "今天尚未生成网页巡检记录。", parent=self.root)
            return
        destination = filedialog.asksaveasfilename(
            parent=self.root, title="导出今日网页巡检", defaultextension=".csv", filetypes=[("CSV 文件", "*.csv")],
            initialfile="today_web_inspection.csv",
        )
        if not destination:
            return
        try:
            self._write_csv(
                Path(destination),
                ["名称", "地址", "检测类型", "状态", "延迟(ms)", "错误信息", "检测时间", "详情", "检测次数"],
                self.database.inspection_export_rows(run_id),
            )
            messagebox.showinfo("导出完成", f"今日巡检结果已导出至：\n{destination}", parent=self.root)
        except (OSError, ValueError) as error:
            self.logger.exception("导出今日巡检失败")
            messagebox.showerror("导出失败", str(error), parent=self.root)

    @staticmethod
    def _write_csv(destination: Path, headers: list[str], rows: list[tuple[Any, ...]]) -> None:
        with destination.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(rows)

    def minimize_to_tray(self) -> None:
        self.root.withdraw()

    def restore_from_tray(self) -> None:
        """Backward-compatible request to restore, safe from any thread."""
        self.event_queue.put({"event": "tray_show"})

    def _restore_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def exit_application(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.monitor.shutdown()
        self.tray.stop()
        self.root.destroy()
