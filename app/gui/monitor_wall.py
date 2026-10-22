"""A read-only dark monitoring wall for fast single-operator triage."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import tkinter as tk
from typing import Any, Callable


WALL_COLORS = {
    "background": "#0B1220",
    "panel": "#111C2D",
    "panel_alt": "#16263F",
    "border": "#28405F",
    "text": "#E8EEF7",
    "muted": "#92A4BE",
    "success": "#2DD4A1",
    "danger": "#FB5B6B",
    "warning": "#F5B942",
    "mixed": "#F5B942",
    "unknown": "#61738D",
    "no_data": "#233650",
    "accent": "#44C9F5",
}
STATUS_LABELS = {"up": "正常", "down": "异常", "unknown": "等待结果"}
STATUS_ORDER = {"down": 0, "unknown": 1, "up": 2}


def _count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _short_text(value: object, limit: int) -> str:
    text = str(value or "-")
    return text if len(text) <= limit else f"{text[: max(1, limit - 1)]}…"


def _hour_bucket_state(bucket: Mapping[str, Any] | None) -> str | None:
    """Turn an actual saved hourly aggregate into one honest display state."""
    if bucket is None or _count(bucket.get("total")) == 0:
        return None
    up, down, other = (_count(bucket.get(key)) for key in ("up", "down", "other"))
    if down and up:
        return "mixed"
    if down:
        return "down"
    if other:
        return "unknown"
    return "up" if up else "unknown"


def wall_view_state(snapshot: Mapping[str, Any], target_limit: int = 12) -> dict[str, Any]:
    """Derive an immutable display model from MainWindow's shared snapshot."""
    counts_source = snapshot.get("status_counts", {})
    counts = counts_source if isinstance(counts_source, Mapping) else {}
    fleet = {key: _count(counts.get(key)) for key in ("total", "up", "down", "unknown")}
    known_results = fleet["up"] + fleet["down"]
    availability = f"{round(fleet['up'] / known_results * 100)}%" if known_results else "--"

    rows = snapshot.get("targets", [])
    targets = [dict(item) for item in rows if isinstance(item, Mapping)] if isinstance(rows, list) else []
    targets.sort(key=lambda item: (STATUS_ORDER.get(str(item.get("status")), 1), str(item.get("name", ""))))
    limit = max(1, int(target_limit))

    exception_rows = snapshot.get("exceptions", [])
    raw_incidents = [dict(item) for item in exception_rows if isinstance(item, Mapping)] if isinstance(exception_rows, list) else []
    incidents = raw_incidents[:5] if fleet["down"] else []
    trends_source = snapshot.get("trends", [])
    trends = [dict(item) for item in trends_source if isinstance(item, Mapping)] if isinstance(trends_source, list) else []
    history_source = snapshot.get("hourly_history", {})
    history = history_source if isinstance(history_source, Mapping) else {}
    raw_hours = history.get("hours", [])
    hours = [str(hour) for hour in raw_hours] if isinstance(raw_hours, list) else []
    bucket_labels: list[str] = []
    for hour in hours:
        try:
            bucket_labels.append(datetime.fromisoformat(hour).strftime("%H:00"))
        except ValueError:
            bucket_labels.append(hour)
    raw_buckets = history.get("buckets", [])
    bucket_by_key_and_hour = {
        (str(bucket.get("key", "")), str(bucket.get("hour_start", ""))): bucket
        for bucket in raw_buckets
        if isinstance(bucket, Mapping)
    } if isinstance(raw_buckets, list) else {}
    heatmap_rows: list[dict[str, Any]] = []
    for target in targets:
        target_key = str(target.get("key", ""))
        normalized_points = [
            _hour_bucket_state(bucket_by_key_and_hour.get((target_key, hour)))
            for hour in hours
        ]
        heatmap_rows.append({
            "key": target_key, "name": str(target.get("name", "-")), "points": normalized_points,
        })
    heatmap_limit = 6
    inspection_source = snapshot.get("today_inspection", {})
    inspection = dict(inspection_source) if isinstance(inspection_source, Mapping) else {}
    return {
        "fleet": fleet,
        "availability": availability,
        "targets": targets[:limit],
        "hidden_target_count": max(0, len(targets) - limit),
        "incidents": incidents,
        "incident_count": len(incidents),
        "trends": trends[:5],
        "heatmap": {
            "bucket_labels": bucket_labels,
            "rows": heatmap_rows[:heatmap_limit],
            "hidden_row_count": max(0, len(heatmap_rows) - heatmap_limit),
        },
        "inspection": inspection,
        "generated_at": str(snapshot.get("generated_at") or ""),
    }


class MonitorWallPanel(tk.Frame):
    """Dark value-first screen that renders an existing view snapshot only."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_back: Callable[[], None],
        on_filter_status: Callable[[str | None], None],
        on_open_target: Callable[[str], None],
        on_toggle_fullscreen: Callable[[], None],
        on_check_now: Callable[[], None],
        on_check_websites: Callable[[], None],
        on_refresh: Callable[[], None],
    ) -> None:
        super().__init__(parent, background=WALL_COLORS["background"])
        self._on_back = on_back
        self._on_filter_status = on_filter_status
        self._on_open_target = on_open_target
        self._on_toggle_fullscreen = on_toggle_fullscreen
        self._on_check_now = on_check_now
        self._on_check_websites = on_check_websites
        self._on_refresh = on_refresh
        self._state: dict[str, Any] = wall_view_state({})
        self.metric_values = {key: tk.StringVar(value="0") for key in ("total", "up", "down", "unknown")}
        self.availability_var = tk.StringVar(value="--")
        self.running_var = tk.StringVar(value="监控未启动")
        self.generated_var = tk.StringVar(value="等待首条检测结果")
        self.inspection_var = tk.StringVar(value="今日网页巡检尚未开始")
        self.inspection_detail_var = tk.StringVar(value="")
        self.footer_var = tk.StringVar(value="等待数据同步")
        self.desktop_alert_var = tk.StringVar(value="")
        self._desktop_alert_visible = False
        self.incident_rows: list[tk.Frame] = []
        self._heatmap_hitboxes: list[tuple[int, int, str]] = []
        self._build_widgets()
        self.after(1000, self._tick_clock)

    def _panel(self, parent: tk.Misc, *, padding: int = 12) -> tk.Frame:
        return tk.Frame(
            parent, background=WALL_COLORS["panel"], highlightbackground=WALL_COLORS["border"],
            highlightthickness=1, padx=padding, pady=padding,
        )

    def _build_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_metrics()
        self._build_main_region()
        self._build_quality_region()
        self._build_footer()

    def _build_header(self) -> None:
        header = tk.Frame(self, background="#091827", padx=22, pady=12)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        tk.Label(
            header, text="NETWORK\nMONITOR", justify=tk.LEFT, background="#091827", foreground=WALL_COLORS["accent"],
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w")
        title = tk.Frame(header, background="#091827")
        title.grid(row=0, column=1, sticky="w", padx=(16, 0))
        tk.Label(title, text="监控大屏", background="#091827", foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        tk.Label(title, text="值守模式 · 实时状态一眼可见", background="#091827", foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(2, 0))
        self.desktop_alert_label = tk.Label(
            title,
            textvariable=self.desktop_alert_var,
            background="#5A1928",
            foreground="#FFE5E9",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=9,
            pady=4,
            anchor="w",
        )

        status = tk.Frame(header, background="#091827")
        status.grid(row=0, column=2, sticky="e", padx=(10, 20))
        self.running_label = tk.Label(status, textvariable=self.running_var, background="#153329", foreground=WALL_COLORS["success"], font=("Microsoft YaHei UI", 9, "bold"), padx=9, pady=4)
        self.running_label.pack(anchor="e")
        self.clock_label = tk.Label(status, text="", background="#091827", foreground=WALL_COLORS["muted"], font=("Segoe UI", 10))
        self.clock_label.pack(anchor="e", pady=(5, 0))

        actions = tk.Frame(header, background="#091827")
        actions.grid(row=0, column=3, sticky="e")
        self._action(actions, "刷新", self._on_refresh, quiet=True).pack(side=tk.LEFT)
        self._action(actions, "立即检测", self._on_check_now).pack(side=tk.LEFT, padx=(8, 0))
        self._action(actions, "网页巡检", self._on_check_websites, accent=True).pack(side=tk.LEFT, padx=(8, 0))
        self._action(actions, "全屏", self._on_toggle_fullscreen, quiet=True).pack(side=tk.LEFT, padx=(8, 0))
        self._action(actions, "工作台", self._on_back, quiet=True).pack(side=tk.LEFT, padx=(8, 0))

    def _action(self, parent: tk.Misc, text: str, command: Callable[[], None], *, accent: bool = False, quiet: bool = False) -> tk.Label:
        background = WALL_COLORS["accent"] if accent else ("#172B46" if quiet else "#203A5B")
        foreground = "#06111E" if accent else WALL_COLORS["text"]
        label = tk.Label(parent, text=text, background=background, foreground=foreground, cursor="hand2", font=("Microsoft YaHei UI", 9, "bold"), padx=11, pady=6)
        label.bind("<Button-1>", lambda _event: command())
        label.bind("<Enter>", lambda _event: label.configure(background="#58D5FC" if accent else "#294A73"))
        label.bind("<Leave>", lambda _event: label.configure(background=background))
        return label

    def _build_metrics(self) -> None:
        metrics = tk.Frame(self, background=WALL_COLORS["background"], padx=18, pady=14)
        metrics.grid(row=1, column=0, sticky="ew")
        for column in range(5):
            metrics.grid_columnconfigure(column, weight=1 if column < 4 else 2)
        for column, (key, title, color, filter_state) in enumerate((
            ("total", "监控目标", WALL_COLORS["accent"], None),
            ("up", "运行正常", WALL_COLORS["success"], "up"),
            ("down", "需要处理", WALL_COLORS["danger"], "down"),
            ("unknown", "等待结果", WALL_COLORS["warning"], "unknown"),
        )):
            card = tk.Frame(metrics, background=WALL_COLORS["panel"], highlightbackground=WALL_COLORS["border"], highlightthickness=1, padx=13, pady=10, cursor="hand2")
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
            tk.Frame(card, background=color, height=3).pack(fill=tk.X, pady=(0, 7))
            tk.Label(card, text=title, background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w")
            value = tk.Label(card, textvariable=self.metric_values[key], background=WALL_COLORS["panel"], foreground=color, font=("Segoe UI", 24, "bold"))
            value.pack(anchor="w", pady=(1, 0))
            for widget in (card, value):
                widget.bind("<Button-1>", lambda _event, state=filter_state: self._on_filter_status(state))

        health = tk.Frame(metrics, background=WALL_COLORS["panel_alt"], highlightbackground=WALL_COLORS["border"], highlightthickness=1, padx=15, pady=10)
        health.grid(row=0, column=4, sticky="nsew", padx=(8, 0))
        tk.Label(health, text="已出结果目标可用率", background=WALL_COLORS["panel_alt"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        tk.Label(health, textvariable=self.availability_var, background=WALL_COLORS["panel_alt"], foreground=WALL_COLORS["text"], font=("Segoe UI", 25, "bold")).pack(anchor="w", pady=(1, 0))

    def _build_main_region(self) -> None:
        body = tk.Frame(self, background=WALL_COLORS["background"], padx=18)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        fleet_panel = self._panel(body, padding=14)
        fleet_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        fleet_panel.grid_columnconfigure(0, weight=1)
        fleet_panel.grid_rowconfigure(1, weight=1)
        head = tk.Frame(fleet_panel, background=WALL_COLORS["panel"])
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tk.Label(head, text="目标状态矩阵", background=WALL_COLORS["panel"], foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 13, "bold")).pack(side=tk.LEFT)
        self.matrix_hint = tk.Label(head, text="异常优先 · 最多展示 12 项", background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9))
        self.matrix_hint.pack(side=tk.RIGHT)
        self.target_grid = tk.Frame(fleet_panel, background=WALL_COLORS["panel"])
        self.target_grid.grid(row=1, column=0, sticky="nsew")

        sidebar = tk.Frame(body, background=WALL_COLORS["background"])
        sidebar.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        sidebar.grid_rowconfigure(0, weight=3)
        sidebar.grid_rowconfigure(1, weight=2)
        sidebar.grid_columnconfigure(0, weight=1)
        attention = self._panel(sidebar, padding=14)
        attention.grid(row=0, column=0, sticky="nsew", pady=(0, 9))
        tk.Label(attention, text="需要关注", background=WALL_COLORS["panel"], foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        self.incident_caption = tk.Label(attention, text="当前异常", background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9))
        self.incident_caption.pack(anchor="w", pady=(2, 9))
        self.incident_container = tk.Frame(attention, background=WALL_COLORS["panel"])
        self.incident_container.pack(fill=tk.BOTH, expand=True)

        inspection = self._panel(sidebar, padding=14)
        inspection.grid(row=1, column=0, sticky="nsew", pady=(9, 0))
        tk.Label(inspection, text="今日网页 / 设备页面巡检", background=WALL_COLORS["panel"], foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        tk.Label(inspection, textvariable=self.inspection_var, background=WALL_COLORS["panel"], foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"), wraplength=390, justify=tk.LEFT).pack(anchor="w", pady=(8, 3))
        self.progress_track = tk.Frame(inspection, background="#20314A", height=7)
        self.progress_track.pack(fill=tk.X, pady=(5, 8))
        self.progress_fill = tk.Frame(self.progress_track, background=WALL_COLORS["accent"], height=7)
        self.progress_fill.place(x=0, y=0, relheight=1, relwidth=0)
        tk.Label(inspection, textvariable=self.inspection_detail_var, background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w")

    def _build_quality_region(self) -> None:
        quality = self._panel(self, padding=14)
        quality.grid(row=3, column=0, sticky="nsew", padx=18, pady=(14, 12))
        quality.grid_columnconfigure(0, weight=3)
        quality.grid_columnconfigure(1, weight=2)
        quality.grid_rowconfigure(1, weight=1)
        header = tk.Frame(quality, background=WALL_COLORS["panel"])
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        tk.Label(header, text="近 24 小时状态轨迹", background=WALL_COLORS["panel"], foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)
        tk.Label(
            header, text="绿：正常 · 红：失败 · 橙：本小时有波动 · 灰：无检测记录",
            background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.RIGHT)
        heatmap_box = tk.Frame(quality, background=WALL_COLORS["panel"])
        heatmap_box.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        heatmap_box.grid_columnconfigure(0, weight=1)
        heatmap_box.grid_rowconfigure(0, weight=1)
        self.heatmap_canvas = tk.Canvas(
            heatmap_box, background=WALL_COLORS["panel"], highlightthickness=0, height=128,
        )
        self.heatmap_canvas.grid(row=0, column=0, sticky="nsew")
        self.heatmap_canvas.bind("<Configure>", lambda _event: self._redraw_heatmap())
        self.heatmap_canvas.bind("<Button-1>", self._open_heatmap_target)

        trend_box = tk.Frame(quality, background=WALL_COLORS["panel"])
        trend_box.grid(row=1, column=1, sticky="nsew")
        tk.Label(trend_box, text="近 24 小时质量", background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(1, 5))
        self.trend_container = tk.Frame(trend_box, background=WALL_COLORS["panel"])
        self.trend_container.pack(fill=tk.BOTH, expand=True)

    def _build_footer(self) -> None:
        footer = tk.Frame(self, background="#091827", padx=22, pady=7)
        footer.grid(row=4, column=0, sticky="ew")
        tk.Label(footer, textvariable=self.footer_var, background="#091827", foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)
        tk.Label(footer, text="F11 全屏 / 退出全屏 · Esc 返回工作台", background="#091827", foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(side=tk.RIGHT)

    def refresh(self, snapshot: Mapping[str, Any], monitoring_running: bool, storage_error: str = "") -> None:
        """Render the shared main-thread snapshot without mutating it."""
        state = wall_view_state(snapshot)
        self._state = state
        fleet = state["fleet"]
        for key, value in fleet.items():
            self.metric_values[key].set(str(value))
        self.availability_var.set(str(state["availability"]))
        if monitoring_running:
            self.running_var.set("● 监控运行中")
            self.running_label.configure(background="#153329", foreground=WALL_COLORS["success"])
        else:
            self.running_var.set("● 监控未启动")
            self.running_label.configure(background="#3A2C17", foreground=WALL_COLORS["warning"])
        self._render_target_cards(state["targets"], int(state["hidden_target_count"]))
        self._render_incidents(state["incidents"])
        self._render_inspection(state["inspection"])
        self._render_heatmap(state["heatmap"])
        self._render_trends(state["trends"])
        generated_at = str(state["generated_at"]).replace("T", " ") or "-"
        self.footer_var.set(
            f"最近同步 {generated_at} · {fleet['total']} 个目标 · 正常 {fleet['up']} · 异常 {fleet['down']} · 未知 {fleet['unknown']}"
            + (" · 数据暂未落库" if storage_error else "")
        )

    def set_desktop_alert(self, message: str | None) -> None:
        """Show or hide the compact local-alert marker in the wall header.

        This is intentionally a Tk-main-thread UI operation only.  It does
        not start background work or read data; callers pass a precomputed,
        display-safe alert message from the main window's event loop.
        """
        # Keep the header chip to one compact line even if an upstream error
        # message contains line breaks.
        text = " ".join(str(message).split()) if message else ""
        if text:
            self.desktop_alert_var.set(f"⚠ 本机告警 · {_short_text(text, 52)}")
            if not self._desktop_alert_visible:
                self.desktop_alert_label.pack(anchor="w", fill=tk.X, pady=(7, 0))
                self._desktop_alert_visible = True
            return

        self.desktop_alert_var.set("")
        if self._desktop_alert_visible:
            self.desktop_alert_label.pack_forget()
            self._desktop_alert_visible = False

    def _render_target_cards(self, targets: list[dict[str, Any]], hidden_count: int) -> None:
        for child in self.target_grid.winfo_children():
            child.destroy()
        for column in range(3):
            self.target_grid.grid_columnconfigure(column, weight=1)
        if not targets:
            tk.Label(self.target_grid, text="尚无监控目标", background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 11)).grid(row=0, column=0, sticky="w")
            return
        for index, target in enumerate(targets):
            state = str(target.get("status", "unknown"))
            color = WALL_COLORS.get("success" if state == "up" else "danger" if state == "down" else "warning", WALL_COLORS["unknown"])
            outer = tk.Frame(self.target_grid, background=color, padx=2, pady=2, cursor="hand2")
            outer.grid(row=index // 3, column=index % 3, sticky="nsew", padx=(0 if index % 3 == 0 else 7, 0), pady=(0 if index < 3 else 7, 0))
            card = tk.Frame(outer, background=WALL_COLORS["panel_alt"], padx=10, pady=8)
            card.pack(fill=tk.BOTH, expand=True)
            name = tk.Label(card, text=_short_text(target.get("name"), 15), background=WALL_COLORS["panel_alt"], foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
            name.pack(fill=tk.X)
            tk.Label(card, text=_short_text(target.get("address"), 24), background=WALL_COLORS["panel_alt"], foreground=WALL_COLORS["muted"], font=("Segoe UI", 8), anchor="w").pack(fill=tk.X, pady=(3, 0))
            if state == "up" and target.get("latency") is not None:
                try:
                    detail = f"● 正常  {float(target['latency']):.1f} ms"
                except (TypeError, ValueError):
                    detail = "● 正常"
            elif state == "down":
                detail = f"● 异常  {_short_text(target.get('error_msg') or '连接失败', 13)}"
            else:
                detail = "● 等待结果"
            detail_label = tk.Label(card, text=detail, background=WALL_COLORS["panel_alt"], foreground=color, font=("Microsoft YaHei UI", 9, "bold"), anchor="w")
            detail_label.pack(fill=tk.X, pady=(5, 0))
            target_name = str(target.get("name", ""))
            for widget in (outer, card, name, detail_label):
                widget.bind("<Button-1>", lambda _event, value=target_name: self._on_open_target(value))
        if hidden_count:
            self.matrix_hint.configure(text=f"异常优先 · 另有 {hidden_count} 个目标可在工作台查看")
        else:
            self.matrix_hint.configure(text="异常优先 · 点击目标进入工作台")

    def _render_incidents(self, incidents: list[dict[str, Any]]) -> None:
        for child in self.incident_container.winfo_children():
            child.destroy()
        self.incident_rows = []
        if not incidents:
            self.incident_caption.configure(text="当前没有需要处理的异常", foreground=WALL_COLORS["success"])
            tk.Label(self.incident_container, text="✓ 当前没有需要处理的异常", background=WALL_COLORS["panel"], foreground=WALL_COLORS["success"], font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(9, 0))
            return
        self.incident_caption.configure(text=f"{len(incidents)} 项当前异常", foreground=WALL_COLORS["danger"])
        for item in incidents:
            row = tk.Frame(self.incident_container, background="#2A1B29", highlightbackground="#5B3144", highlightthickness=1, padx=9, pady=7, cursor="hand2")
            row.pack(fill=tk.X, pady=(0, 7))
            tk.Label(row, text=_short_text(item.get("name"), 22), background="#2A1B29", foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(fill=tk.X)
            tk.Label(row, text=_short_text(item.get("error_msg") or "连接失败", 35), background="#2A1B29", foreground=WALL_COLORS["danger"], font=("Microsoft YaHei UI", 9), anchor="w").pack(fill=tk.X, pady=(3, 0))
            timestamp = str(item.get("check_time") or "-").replace("T", " ")
            tk.Label(row, text=timestamp, background="#2A1B29", foreground=WALL_COLORS["muted"], font=("Segoe UI", 8), anchor="w").pack(fill=tk.X, pady=(2, 0))
            target_name = str(item.get("name", ""))
            row.bind("<Button-1>", lambda _event, value=target_name: self._on_open_target(value))
            self.incident_rows.append(row)

    def _render_inspection(self, inspection: Mapping[str, Any]) -> None:
        total, completed, up, down = (_count(inspection.get(key)) for key in ("total", "completed", "up", "down"))
        status = str(inspection.get("status", "not_started"))
        if status == "not_started":
            self.inspection_var.set(f"今日尚未开始巡检（待检 {total} 项）")
            self.inspection_detail_var.set("点击右上角“网页巡检”开始")
            progress = 0
        elif status == "running":
            self.inspection_var.set(f"正在巡检 {completed}/{total} 项 · 正常 {up} · 异常 {down}")
            self.inspection_detail_var.set(f"正常 {up} · 异常 {down} · 待检 {max(0, total - completed)}")
            progress = completed / total if total else 0
        else:
            self.inspection_var.set(f"今日网页巡检：已完成 {completed}/{total} 项 · 正常 {up} · 异常 {down}")
            self.inspection_detail_var.set(f"正常 {up} · 异常 {down}")
            progress = completed / total if total else 1
        self.progress_fill.place_configure(relwidth=max(0, min(1, progress)))

    def _render_trends(self, trends: list[dict[str, Any]]) -> None:
        for child in self.trend_container.winfo_children():
            child.destroy()
        if not trends:
            tk.Label(self.trend_container, text="正在积累历史数据", background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w")
            return
        headings = ("目标", "成功率", "平均延迟")
        for column, title in enumerate(headings):
            self.trend_container.grid_columnconfigure(column, weight=3 if column == 0 else 1)
            tk.Label(self.trend_container, text=title, background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9, "bold"), anchor="w").grid(row=0, column=column, sticky="ew", padx=(0, 12), pady=(0, 4))
        for row_index, item in enumerate(trends, start=1):
            total = _count(item.get("total"))
            success = "--" if not total else f"{round(_count(item.get('up')) / total * 100)}%"
            latency = "--" if item.get("avg_latency") is None else f"{float(item['avg_latency']):.1f} ms"
            values = (_short_text(item.get("name"), 18), success, latency)
            for column, value in enumerate(values):
                color = WALL_COLORS["success"] if column == 1 and value != "--" else WALL_COLORS["text"]
                tk.Label(self.trend_container, text=value, background=WALL_COLORS["panel"], foreground=color, font=("Microsoft YaHei UI", 9), anchor="w").grid(row=row_index, column=column, sticky="ew", padx=(0, 12), pady=2)

    def _render_heatmap(self, heatmap: Mapping[str, Any]) -> None:
        """Render hourly history with Canvas after the layout has a real size."""
        self._heatmap = dict(heatmap)
        self._redraw_heatmap()

    def _redraw_heatmap(self) -> None:
        if not hasattr(self, "heatmap_canvas") or not self.heatmap_canvas.winfo_exists():
            return
        canvas = self.heatmap_canvas
        canvas.delete("all")
        labels = self._heatmap.get("bucket_labels", []) if hasattr(self, "_heatmap") else []
        rows = self._heatmap.get("rows", []) if hasattr(self, "_heatmap") else []
        if not isinstance(labels, list) or not isinstance(rows, list) or not labels or not rows:
            canvas.create_text(
                8, max(20, canvas.winfo_height() // 2), anchor="w",
                text="正在积累检测历史；数据会在此处按小时呈现。",
                fill=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9),
            )
            self._heatmap_hitboxes = []
            return

        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        name_width = min(122, max(82, width // 5))
        grid_left, grid_right = name_width + 5, width - 4
        cell_width = max(2, (grid_right - grid_left) / len(labels))
        row_top, usable_height = 23, max(18, height - 27)
        row_height = max(17, min(26, usable_height // max(1, len(rows))))
        self._heatmap_hitboxes = []
        for column, label in enumerate(labels):
            if column % 4 == 0 or column == len(labels) - 1:
                x = grid_left + column * cell_width
                canvas.create_text(x, 8, anchor="nw", text=str(label), fill=WALL_COLORS["muted"], font=("Segoe UI", 7))
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            y = row_top + row_index * row_height
            target_name = str(row.get("name", "-"))
            canvas.create_text(0, y + (row_height - 4) / 2, anchor="w", text=_short_text(target_name, 13), fill=WALL_COLORS["text"], font=("Microsoft YaHei UI", 8, "bold"))
            points = row.get("points", [])
            if not isinstance(points, list):
                points = []
            for column in range(len(labels)):
                point = points[column] if column < len(points) else None
                color = {
                    "up": WALL_COLORS["success"], "down": WALL_COLORS["danger"],
                    "mixed": WALL_COLORS["mixed"], "unknown": WALL_COLORS["warning"],
                }.get(point, WALL_COLORS["no_data"])
                x1 = grid_left + column * cell_width + 1
                x2 = grid_left + (column + 1) * cell_width - 1
                canvas.create_rectangle(x1, y + 2, x2, y + row_height - 3, fill=color, outline="")
            self._heatmap_hitboxes.append((y, y + row_height, target_name))
        hidden_count = _count(self._heatmap.get("hidden_row_count")) if hasattr(self, "_heatmap") else 0
        if hidden_count:
            canvas.create_text(0, height - 4, anchor="sw", text=f"另有 {hidden_count} 个目标可在工作台查看", fill=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 8))

    def _open_heatmap_target(self, event: tk.Event[tk.Misc]) -> None:
        for top, bottom, target_name in self._heatmap_hitboxes:
            if top <= event.y <= bottom:
                self._on_open_target(target_name)
                break

    def _tick_clock(self) -> None:
        if not self.winfo_exists():
            return
        self.clock_label.configure(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.after(1000, self._tick_clock)
