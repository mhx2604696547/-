"""Modern dark NOC monitoring wall rendered with native Tkinter Canvas widgets."""
from __future__ import annotations

import math
import re
import tkinter as tk
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Callable


WALL_COLORS = {
    "background": "#070B14",
    "header": "#0B1120",
    "panel": "#0F172A",
    "panel_alt": "#162033",
    "panel_hover": "#1E2D45",
    "border": "#1E293B",
    "border_bright": "#334155",
    "text": "#F1F5F9",
    "muted": "#94A3B8",
    "dim": "#64748B",
    "success": "#10B981",
    "success_bg": "#0D2B24",
    "danger": "#F43F5E",
    "danger_bg": "#2A121D",
    "danger_border": "#7F1D3A",
    "warning": "#F59E0B",
    "warning_bg": "#2B2110",
    "mixed": "#F59E0B",
    "unknown": "#64748B",
    "no_data": "#1E293B",
    "accent": "#38BDF8",
    "accent_bg": "#0E2A3F",
    "purple": "#A78BFA",
}
STATUS_LABELS = {"up": "正常", "down": "异常", "unknown": "等待结果"}
STATUS_ORDER = {"down": 0, "unknown": 1, "up": 2}
CARD_PAGE_SIZE = 9
AUTO_PAGE_INTERVAL_MS = 12000


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


def parse_snmp_metrics(detail: str) -> dict[str, Any]:
    """Extract structured visual metrics from an SNMP detail summary string."""
    text = str(detail or "")
    metrics: dict[str, Any] = {}
    cpu_match = re.search(r"CPU\s*(\d+(?:\.\d+)?)\s*%", text)
    if cpu_match:
        metrics["cpu"] = max(0.0, min(100.0, float(cpu_match.group(1))))
    mem_match = re.search(r"内存\s*(\d+(?:\.\d+)?)\s*%", text)
    if mem_match:
        metrics["mem"] = max(0.0, min(100.0, float(mem_match.group(1))))
    port_match = re.search(r"端口\s*(\d+)\s*个[：:]\s*(\d+)\s*up[，,]\s*(\d+)\s*down", text)
    if port_match:
        metrics["ports_total"] = int(port_match.group(1))
        metrics["ports_up"] = int(port_match.group(2))
        metrics["ports_down"] = int(port_match.group(3))
    return metrics


def paginate_targets(
    targets: list[dict[str, Any]], page: int, page_size: int = CARD_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int, int]:
    """Slice targets into a page, clamping page index cleanly."""
    size = max(1, int(page_size))
    total_pages = max(1, math.ceil(len(targets) / size))
    clamped = max(0, min(int(page), total_pages - 1))
    start = clamped * size
    return targets[start : start + size], clamped, total_pages


def _draw_rounded_rect(
    canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, radius: float = 10, **kwargs: Any,
) -> int:
    """Draw a smooth rounded rectangle on a standard Tk Canvas."""
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def wall_view_state(snapshot: Mapping[str, Any], target_limit: int = 12) -> dict[str, Any]:
    """Derive an immutable display model from MainWindow's shared snapshot."""
    counts_source = snapshot.get("status_counts", {})
    counts = counts_source if isinstance(counts_source, Mapping) else {}
    fleet = {key: _count(counts.get(key)) for key in ("total", "up", "down", "unknown")}
    known_results = fleet["up"] + fleet["down"]
    availability_ratio = (fleet["up"] / known_results) if known_results else None
    availability = f"{round(availability_ratio * 100)}%" if availability_ratio is not None else "--"

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
        "availability_ratio": availability_ratio,
        "targets": targets[:limit],
        "all_targets": targets,
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
    """Dark NOC monitoring wall using native Canvas rounded cards and visual gauges."""

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
        self.running_var = tk.StringVar(value="● 监控未启动")
        self.inspection_var = tk.StringVar(value="今日网页巡检尚未开始")
        self.inspection_detail_var = tk.StringVar(value="")
        self.footer_var = tk.StringVar(value="等待数据同步")
        self.desktop_alert_var = tk.StringVar(value="")
        self._desktop_alert_visible = False
        self.incident_rows: list[tk.Frame] = []
        self._heatmap_hitboxes: list[tuple[float, float, str]] = []
        self._card_hitboxes: list[tuple[float, float, float, float, str]] = []
        self._hovered_card: str | None = None
        self._current_page = 0
        self._total_pages = 1
        self._auto_rotate = True
        self._incident_map: dict[str, tk.Frame] = {}
        self._trend_rows: dict[str, tk.Frame] = {}
        self._inspection_progress = 0.0
        self._build_widgets()
        self.after(1000, self._tick_clock)
        self.after(AUTO_PAGE_INTERVAL_MS, self._tick_auto_page)

    def _panel(self, parent: tk.Misc, *, padding: int = 14) -> tk.Frame:
        return tk.Frame(
            parent,
            background=WALL_COLORS["panel"],
            highlightbackground=WALL_COLORS["border"],
            highlightthickness=1,
            padx=padding,
            pady=padding,
        )

    def _build_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=3)
        self.grid_rowconfigure(3, weight=2)

        self._build_header()
        self._build_metrics()
        self._build_main_region()
        self._build_quality_region()
        self._build_footer()

    def _build_header(self) -> None:
        header = tk.Frame(
            self, background=WALL_COLORS["header"],
            highlightbackground=WALL_COLORS["border"], highlightthickness=1, padx=22, pady=10,
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        badge = tk.Canvas(header, width=38, height=38, background=WALL_COLORS["header"], highlightthickness=0)
        badge.grid(row=0, column=0, sticky="w")
        _draw_rounded_rect(badge, 2, 2, 36, 36, radius=9, fill=WALL_COLORS["accent_bg"], outline=WALL_COLORS["accent"], width=1.5)
        badge.create_text(19, 19, text="网哨", fill=WALL_COLORS["accent"], font=("Microsoft YaHei UI", 10, "bold"))

        title = tk.Frame(header, background=WALL_COLORS["header"])
        title.grid(row=0, column=1, sticky="w", padx=(14, 0))
        title_row = tk.Frame(title, background=WALL_COLORS["header"])
        title_row.pack(anchor="w")
        tk.Label(
            title_row, text="实时网络监控大屏", background=WALL_COLORS["header"],
            foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(side=tk.LEFT)
        self.running_label = tk.Label(
            title_row, textvariable=self.running_var, background=WALL_COLORS["success_bg"],
            foreground=WALL_COLORS["success"], font=("Microsoft YaHei UI", 9, "bold"), padx=10, pady=3,
        )
        self.running_label.pack(side=tk.LEFT, padx=(12, 0))

        self.desktop_alert_label = tk.Label(
            title,
            textvariable=self.desktop_alert_var,
            background=WALL_COLORS["danger_bg"],
            foreground="#FFE4E8",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=9,
            pady=3,
            anchor="w",
        )

        self.clock_label = tk.Label(
            header, text="", background=WALL_COLORS["header"],
            foreground=WALL_COLORS["muted"], font=("Segoe UI", 11, "bold"),
        )
        self.clock_label.grid(row=0, column=2, sticky="e", padx=(10, 18))

        actions = tk.Frame(header, background=WALL_COLORS["header"])
        actions.grid(row=0, column=3, sticky="e")
        self._action(actions, "刷新", self._on_refresh, quiet=True).pack(side=tk.LEFT)
        self._action(actions, "立即检测", self._on_check_now).pack(side=tk.LEFT, padx=(7, 0))
        self._action(actions, "网页巡检", self._on_check_websites, accent=True).pack(side=tk.LEFT, padx=(7, 0))
        self._action(actions, "全屏", self._on_toggle_fullscreen, quiet=True).pack(side=tk.LEFT, padx=(7, 0))
        self._action(actions, "工作台", self._on_back, quiet=True).pack(side=tk.LEFT, padx=(7, 0))

    def _action(
        self, parent: tk.Misc, text: str, command: Callable[[], None], *, accent: bool = False, quiet: bool = False,
    ) -> tk.Label:
        bg = WALL_COLORS["accent"] if accent else (WALL_COLORS["panel"] if quiet else WALL_COLORS["panel_alt"])
        hover = "#7DD3FC" if accent else WALL_COLORS["panel_hover"]
        fg = "#04121F" if accent else WALL_COLORS["text"]
        label = tk.Label(
            parent, text=text, background=bg, foreground=fg, cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"), padx=12, pady=6,
            highlightbackground=WALL_COLORS["accent"] if accent else WALL_COLORS["border_bright"],
            highlightthickness=1,
        )
        label.bind("<Button-1>", lambda _event: command())
        label.bind("<Enter>", lambda _event: label.configure(background=hover))
        label.bind("<Leave>", lambda _event: label.configure(background=bg))
        return label

    def _build_metrics(self) -> None:
        metrics = tk.Frame(self, background=WALL_COLORS["background"], padx=18, pady=12)
        metrics.grid(row=1, column=0, sticky="ew")
        for column in range(5):
            metrics.grid_columnconfigure(column, weight=2 if column < 4 else 3)

        self._metric_canvases: dict[str, tuple[tk.Canvas, str, str, str | None]] = {}
        for column, (key, title, color, filter_state) in enumerate((
            ("total", "监控目标总数", WALL_COLORS["accent"], None),
            ("up", "运行正常", WALL_COLORS["success"], "up"),
            ("down", "当前异常", WALL_COLORS["danger"], "down"),
            ("unknown", "等待 / 未知", WALL_COLORS["warning"], "unknown"),
        )):
            canvas = tk.Canvas(
                metrics, height=78, background=WALL_COLORS["background"], highlightthickness=0, cursor="hand2",
            )
            canvas.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 10, 0))
            canvas.bind("<Configure>", lambda _e, k=key: self._draw_metric_card(k))
            canvas.bind("<Button-1>", lambda _e, state=filter_state: self._on_filter_status(state))
            self._metric_canvases[key] = (canvas, title, color, filter_state)

        self.gauge_canvas = tk.Canvas(
            metrics, height=78, background=WALL_COLORS["background"], highlightthickness=0,
        )
        self.gauge_canvas.grid(row=0, column=4, sticky="nsew", padx=(10, 0))
        self.gauge_canvas.bind("<Configure>", lambda _e: self._draw_availability_gauge())

    def _draw_metric_card(self, key: str) -> None:
        canvas, title, color, _ = self._metric_canvases[key]
        if not canvas.winfo_exists():
            return
        w, h = max(120, canvas.winfo_width()), max(70, canvas.winfo_height())
        canvas.delete("all")
        val = self.metric_values[key].get()
        is_danger_active = key == "down" and _count(val) > 0
        fill = WALL_COLORS["danger_bg"] if is_danger_active else WALL_COLORS["panel"]
        outline = WALL_COLORS["danger_border"] if is_danger_active else WALL_COLORS["border"]
        _draw_rounded_rect(canvas, 1, 1, w - 2, h - 2, radius=11, fill=fill, outline=outline, width=1.2)
        # Left vertical accent pill
        _draw_rounded_rect(canvas, 10, 14, 14, h - 14, radius=2, fill=color, outline="")
        canvas.create_text(26, 22, anchor="w", text=title, fill=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9))
        canvas.create_text(26, 52, anchor="w", text=val, fill=color, font=("Segoe UI", 23, "bold"))

    def _draw_availability_gauge(self) -> None:
        canvas = self.gauge_canvas
        if not canvas.winfo_exists():
            return
        w, h = max(180, canvas.winfo_width()), max(70, canvas.winfo_height())
        canvas.delete("all")
        _draw_rounded_rect(
            canvas, 1, 1, w - 2, h - 2, radius=11,
            fill=WALL_COLORS["panel"], outline=WALL_COLORS["border"], width=1.2,
        )
        ratio = self._state.get("availability_ratio")
        cx, cy, r = 44, h / 2, 24
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=WALL_COLORS["no_data"], width=6)
        if isinstance(ratio, (int, float)):
            color = WALL_COLORS["success"] if ratio >= 0.95 else WALL_COLORS["warning"] if ratio >= 0.75 else WALL_COLORS["danger"]
            extent = max(2.0, min(359.5, ratio * 360.0))
            canvas.create_arc(
                cx - r, cy - r, cx + r, cy + r,
                start=90, extent=-extent, style=tk.ARC, outline=color, width=6,
            )
            ring_text = self.availability_var.get()
        else:
            color = WALL_COLORS["muted"]
            ring_text = "--"
        canvas.create_text(cx, cy, text=ring_text, fill=WALL_COLORS["text"], font=("Segoe UI", 10, "bold"))

        fleet = self._state.get("fleet", {})
        canvas.create_text(84, 24, anchor="w", text="整体健康可用率", fill=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9))
        canvas.create_text(
            84, 50, anchor="w",
            text=f"正常 {fleet.get('up', 0)} / 已检 {fleet.get('up', 0) + fleet.get('down', 0)}",
            fill=color if ratio is not None else WALL_COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        )

    def _build_main_region(self) -> None:
        body = tk.Frame(self, background=WALL_COLORS["background"], padx=18)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=7)
        body.grid_columnconfigure(1, weight=4)
        body.grid_rowconfigure(0, weight=1)

        fleet_panel = self._panel(body, padding=14)
        fleet_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        fleet_panel.grid_columnconfigure(0, weight=1)
        fleet_panel.grid_rowconfigure(1, weight=1)

        head = tk.Frame(fleet_panel, background=WALL_COLORS["panel"])
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tk.Label(
            head, text="实时节点状态矩阵", background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side=tk.LEFT)
        self.matrix_hint = tk.Label(
            head, text="异常优先 · 点击卡片定位目标", background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9),
        )
        self.matrix_hint.pack(side=tk.LEFT, padx=(12, 0))

        pager = tk.Frame(head, background=WALL_COLORS["panel"])
        pager.pack(side=tk.RIGHT)
        self.rotate_btn = self._pager_btn(pager, "自动轮播: 开", self._toggle_auto_rotate)
        self.rotate_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._pager_btn(pager, "‹", lambda: self._step_page(-1)).pack(side=tk.LEFT)
        self.page_label = tk.Label(
            pager, text="1 / 1", background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["muted"], font=("Segoe UI", 9, "bold"), padx=7,
        )
        self.page_label.pack(side=tk.LEFT)
        self._pager_btn(pager, "›", lambda: self._step_page(1)).pack(side=tk.LEFT)

        self.cards_canvas = tk.Canvas(
            fleet_panel, background=WALL_COLORS["panel"], highlightthickness=0, cursor="hand2",
        )
        self.cards_canvas.grid(row=1, column=0, sticky="nsew")
        self.cards_canvas.bind("<Configure>", lambda _e: self._redraw_cards())
        self.cards_canvas.bind("<Button-1>", self._on_card_click)
        self.cards_canvas.bind("<Motion>", self._on_card_hover)
        self.cards_canvas.bind("<Leave>", self._on_card_leave)

        sidebar = tk.Frame(body, background=WALL_COLORS["background"])
        sidebar.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        sidebar.grid_rowconfigure(0, weight=3)
        sidebar.grid_rowconfigure(1, weight=2)
        sidebar.grid_columnconfigure(0, weight=1)

        attention = self._panel(sidebar, padding=14)
        attention.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        att_head = tk.Frame(attention, background=WALL_COLORS["panel"])
        att_head.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            att_head, text="实时异常告警", background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side=tk.LEFT)
        self.incident_caption = tk.Label(
            att_head, text="当前无异常", background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["success"], font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.incident_caption.pack(side=tk.RIGHT)
        self.incident_container = tk.Frame(attention, background=WALL_COLORS["panel"])
        self.incident_container.pack(fill=tk.BOTH, expand=True)

        inspection = self._panel(sidebar, padding=14)
        inspection.grid(row=1, column=0, sticky="nsew")
        tk.Label(
            inspection, text="今日网页 / 设备页面巡检", background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            inspection, textvariable=self.inspection_var, background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"),
            wraplength=380, justify=tk.LEFT,
        ).pack(anchor="w", pady=(8, 4))
        self.progress_canvas = tk.Canvas(
            inspection, height=12, background=WALL_COLORS["panel"], highlightthickness=0,
        )
        self.progress_canvas.pack(fill=tk.X, pady=(4, 8))
        self.progress_canvas.bind("<Configure>", lambda _e: self._draw_inspection_bar())
        tk.Label(
            inspection, textvariable=self.inspection_detail_var, background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")

    def _pager_btn(self, parent: tk.Misc, text: str, command: Callable[[], None]) -> tk.Label:
        btn = tk.Label(
            parent, text=text, background=WALL_COLORS["panel_alt"], foreground=WALL_COLORS["text"],
            font=("Microsoft YaHei UI", 8, "bold"), padx=7, pady=2, cursor="hand2",
            highlightbackground=WALL_COLORS["border_bright"], highlightthickness=1,
        )
        btn.bind("<Button-1>", lambda _e: command())
        btn.bind("<Enter>", lambda _e: btn.configure(background=WALL_COLORS["panel_hover"]))
        btn.bind("<Leave>", lambda _e: btn.configure(background=WALL_COLORS["panel_alt"]))
        return btn

    def _step_page(self, delta: int) -> None:
        if self._total_pages <= 1:
            return
        self._current_page = (self._current_page + delta) % self._total_pages
        self._redraw_cards()

    def _toggle_auto_rotate(self) -> None:
        self._auto_rotate = not self._auto_rotate
        self.rotate_btn.configure(text=f"自动轮播: {'开' if self._auto_rotate else '关'}")

    def _tick_auto_page(self) -> None:
        if not self.winfo_exists():
            return
        if self._auto_rotate and self._total_pages > 1 and self._hovered_card is None:
            self._current_page = (self._current_page + 1) % self._total_pages
            self._redraw_cards()
        self.after(AUTO_PAGE_INTERVAL_MS, self._tick_auto_page)

    def _build_quality_region(self) -> None:
        quality = self._panel(self, padding=14)
        quality.grid(row=3, column=0, sticky="nsew", padx=18, pady=(12, 10))
        quality.grid_columnconfigure(0, weight=7)
        quality.grid_columnconfigure(1, weight=4)
        quality.grid_rowconfigure(1, weight=1)

        header = tk.Frame(quality, background=WALL_COLORS["panel"])
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        tk.Label(
            header, text="近 24 小时状态轨迹", background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["text"], font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            header, text="● 正常   ● 失败   ● 本小时波动   ● 无记录",
            background=WALL_COLORS["panel"], foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.RIGHT)

        heatmap_box = tk.Frame(quality, background=WALL_COLORS["panel"])
        heatmap_box.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        heatmap_box.grid_columnconfigure(0, weight=1)
        heatmap_box.grid_rowconfigure(0, weight=1)
        self.heatmap_canvas = tk.Canvas(
            heatmap_box, background=WALL_COLORS["panel"], highlightthickness=0, height=118, cursor="hand2",
        )
        self.heatmap_canvas.grid(row=0, column=0, sticky="nsew")
        self.heatmap_canvas.bind("<Configure>", lambda _event: self._redraw_heatmap())
        self.heatmap_canvas.bind("<Button-1>", self._open_heatmap_target)

        trend_box = tk.Frame(quality, background=WALL_COLORS["panel"])
        trend_box.grid(row=1, column=1, sticky="nsew")
        tk.Label(
            trend_box, text="近 24 小时稳定性", background=WALL_COLORS["panel"],
            foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w", pady=(1, 5))
        self.trend_container = tk.Frame(trend_box, background=WALL_COLORS["panel"])
        self.trend_container.pack(fill=tk.BOTH, expand=True)

    def _build_footer(self) -> None:
        footer = tk.Frame(
            self, background=WALL_COLORS["header"],
            highlightbackground=WALL_COLORS["border"], highlightthickness=1, padx=22, pady=6,
        )
        footer.grid(row=4, column=0, sticky="ew")
        tk.Label(
            footer, textvariable=self.footer_var, background=WALL_COLORS["header"],
            foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)
        tk.Label(
            footer, text="F11 全屏 / 退出全屏 · Esc 返回工作台",
            background=WALL_COLORS["header"], foreground=WALL_COLORS["dim"], font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.RIGHT)

    def refresh(self, snapshot: Mapping[str, Any], monitoring_running: bool, storage_error: str = "") -> None:
        """Render the shared main-thread snapshot without mutating it."""
        state = wall_view_state(snapshot)
        self._state = state
        fleet = state["fleet"]
        for key, value in fleet.items():
            self.metric_values[key].set(str(value))
            self._draw_metric_card(key)
        self.availability_var.set(str(state["availability"]))
        self._draw_availability_gauge()

        if monitoring_running:
            self.running_var.set("● 监控运行中")
            self.running_label.configure(background=WALL_COLORS["success_bg"], foreground=WALL_COLORS["success"])
        else:
            self.running_var.set("● 监控未启动")
            self.running_label.configure(background=WALL_COLORS["warning_bg"], foreground=WALL_COLORS["warning"])

        self._redraw_cards()
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
        """Show or hide the compact local-alert marker in the wall header."""
        text = " ".join(str(message).split()) if message else ""
        if text:
            self.desktop_alert_var.set(f"⚠ 本机告警 · {_short_text(text, 52)}")
            if not self._desktop_alert_visible:
                self.desktop_alert_label.pack(anchor="w", fill=tk.X, pady=(6, 0))
                self._desktop_alert_visible = True
            return
        self.desktop_alert_var.set("")
        if self._desktop_alert_visible:
            self.desktop_alert_label.pack_forget()
            self._desktop_alert_visible = False

    def _redraw_cards(self) -> None:
        if not hasattr(self, "cards_canvas") or not self.cards_canvas.winfo_exists():
            return
        canvas = self.cards_canvas
        canvas.delete("all")
        self._card_hitboxes = []

        all_targets: list[dict[str, Any]] = self._state.get("all_targets", [])
        page_targets, clamped_page, total_pages = paginate_targets(all_targets, self._current_page, CARD_PAGE_SIZE)
        self._current_page = clamped_page
        self._total_pages = total_pages
        self.page_label.configure(text=f"{clamped_page + 1} / {total_pages}")

        w, h = max(300, canvas.winfo_width()), max(180, canvas.winfo_height())
        if not page_targets:
            canvas.create_text(
                w / 2, h / 2, text="暂无监控目标，请点击右上角「工作台」添加目标",
                fill=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 11),
            )
            return

        cols = 3
        rows = max(1, math.ceil(len(page_targets) / cols))
        gap = 10
        card_w = (w - gap * (cols - 1)) / cols
        card_h = min(118.0, max(78.0, (h - gap * (rows - 1)) / rows))

        for idx, target in enumerate(page_targets):
            r, c = idx // cols, idx % cols
            x1 = c * (card_w + gap)
            y1 = r * (card_h + gap)
            x2 = x1 + card_w
            y2 = y1 + card_h
            self._draw_target_card(canvas, target, x1, y1, x2, y2)

    def _draw_target_card(
        self, canvas: tk.Canvas, target: dict[str, Any], x1: float, y1: float, x2: float, y2: float,
    ) -> None:
        name = str(target.get("name", "")) or "-"
        addr = str(target.get("address", "")) or "-"
        ctype = str(target.get("check_type", "ping")).upper()
        state = str(target.get("status", "unknown"))
        color = WALL_COLORS["success"] if state == "up" else WALL_COLORS["danger"] if state == "down" else WALL_COLORS["warning"]
        is_hover = self._hovered_card == name

        if state == "down":
            bg = "#351725" if is_hover else WALL_COLORS["danger_bg"]
            border = WALL_COLORS["danger"] if is_hover else WALL_COLORS["danger_border"]
        else:
            bg = WALL_COLORS["panel_hover"] if is_hover else WALL_COLORS["panel_alt"]
            border = WALL_COLORS["accent"] if is_hover else WALL_COLORS["border_bright"]

        _draw_rounded_rect(canvas, x1, y1, x2, y2, radius=10, fill=bg, outline=border, width=1.2)
        # Left status bar
        _draw_rounded_rect(canvas, x1 + 6, y1 + 10, x1 + 10, y2 - 10, radius=2, fill=color, outline="")

        # Protocol badge on top-right
        badge_w = 42
        bx1, by1, bx2, by2 = x2 - badge_w - 10, y1 + 10, x2 - 10, y1 + 27
        badge_bg = WALL_COLORS["accent_bg"] if ctype == "SNMP" else WALL_COLORS["panel"]
        badge_fg = WALL_COLORS["accent"] if ctype == "SNMP" else WALL_COLORS["muted"]
        _draw_rounded_rect(canvas, bx1, by1, bx2, by2, radius=5, fill=badge_bg, outline=WALL_COLORS["border_bright"], width=1)
        canvas.create_text((bx1 + bx2) / 2, (by1 + by2) / 2, text=ctype, fill=badge_fg, font=("Segoe UI", 7, "bold"))

        # Title & address
        max_name_chars = max(8, int((x2 - x1 - 78) / 11))
        canvas.create_text(
            x1 + 18, y1 + 18, anchor="w", text=_short_text(name, max_name_chars),
            fill=WALL_COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"),
        )
        max_addr_chars = max(14, int((x2 - x1 - 30) / 7))
        canvas.create_text(
            x1 + 18, y1 + 38, anchor="w", text=_short_text(addr, max_addr_chars),
            fill=WALL_COLORS["muted"], font=("Segoe UI", 8),
        )

        # Bottom visual bar: SNMP CPU/Mem, Switch Ports, or Latency/Error
        bar_left, bar_right, bar_y = x1 + 18, x2 - 12, y2 - 24
        detail = str(target.get("detail") or "")
        snmp = parse_snmp_metrics(detail) if ctype == "SNMP" and state == "up" else {}

        if "cpu" in snmp or "mem" in snmp:
            mid = (bar_left + bar_right) / 2 - 4
            if "cpu" in snmp:
                self._draw_mini_gauge(canvas, bar_left, bar_y, mid, "CPU", snmp["cpu"], WALL_COLORS["accent"])
            if "mem" in snmp:
                self._draw_mini_gauge(canvas, mid + 8, bar_y, bar_right, "MEM", snmp["mem"], WALL_COLORS["purple"])
        elif "ports_total" in snmp and snmp["ports_total"] > 0:
            total_p, up_p, down_p = snmp["ports_total"], snmp["ports_up"], snmp["ports_down"]
            ratio = up_p / total_p
            canvas.create_text(
                bar_left, bar_y - 4, anchor="w",
                text=f"端口 {up_p} UP / {down_p} DOWN", fill=WALL_COLORS["text"], font=("Microsoft YaHei UI", 8, "bold"),
            )
            _draw_rounded_rect(canvas, bar_left, bar_y + 7, bar_right, bar_y + 13, radius=3, fill=WALL_COLORS["no_data"], outline="")
            fill_w = bar_left + max(4.0, (bar_right - bar_left) * ratio)
            _draw_rounded_rect(canvas, bar_left, bar_y + 7, fill_w, bar_y + 13, radius=3, fill=WALL_COLORS["success"], outline="")
        elif state == "up":
            latency = target.get("latency")
            lat_text = f"{float(latency):.1f} ms" if latency is not None else "可达"
            canvas.create_text(bar_left, bar_y + 2, anchor="w", text=f"● 正常   {lat_text}", fill=color, font=("Microsoft YaHei UI", 9, "bold"))
        elif state == "down":
            err = _short_text(target.get("error_msg") or "连接失败", max(10, int((x2 - x1 - 36) / 10)))
            canvas.create_text(bar_left, bar_y + 2, anchor="w", text=f"● 异常 · {err}", fill=color, font=("Microsoft YaHei UI", 9, "bold"))
        else:
            canvas.create_text(bar_left, bar_y + 2, anchor="w", text="● 等待首次检测结果", fill=color, font=("Microsoft YaHei UI", 9))

        self._card_hitboxes.append((x1, y1, x2, y2, name))

    @staticmethod
    def _draw_mini_gauge(
        canvas: tk.Canvas, x1: float, y: float, x2: float, label: str, percent: float, base_color: str,
    ) -> None:
        fill_color = WALL_COLORS["danger"] if percent >= 85 else WALL_COLORS["warning"] if percent >= 70 else base_color
        canvas.create_text(x1, y - 4, anchor="w", text=f"{label} {percent:.0f}%", fill=fill_color, font=("Segoe UI", 8, "bold"))
        _draw_rounded_rect(canvas, x1, y + 7, x2, y + 13, radius=3, fill=WALL_COLORS["no_data"], outline="")
        span = max(3.0, (x2 - x1) * (percent / 100.0))
        _draw_rounded_rect(canvas, x1, y + 7, x1 + span, y + 13, radius=3, fill=fill_color, outline="")

    def _hit_test_card(self, x: float, y: float) -> str | None:
        for x1, y1, x2, y2, name in self._card_hitboxes:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return name
        return None

    def _on_card_click(self, event: tk.Event[tk.Misc]) -> None:
        hit = self._hit_test_card(event.x, event.y)
        if hit:
            self._on_open_target(hit)

    def _on_card_hover(self, event: tk.Event[tk.Misc]) -> None:
        hit = self._hit_test_card(event.x, event.y)
        if hit != self._hovered_card:
            self._hovered_card = hit
            self._redraw_cards()

    def _on_card_leave(self, _event: tk.Event[tk.Misc]) -> None:
        if self._hovered_card is not None:
            self._hovered_card = None
            self._redraw_cards()

    def _render_incidents(self, incidents: list[dict[str, Any]]) -> None:
        current_names: set[str] = set()
        self.incident_rows = []
        if not incidents:
            self.incident_caption.configure(text="全部运行正常", foreground=WALL_COLORS["success"])
            for name in list(self._incident_map):
                self._incident_map[name].destroy()
                del self._incident_map[name]
            if not hasattr(self, "_incident_empty_label"):
                self._incident_empty_label = tk.Label(
                    self.incident_container, text="✓ 当前所有监控目标状态健康",
                    background=WALL_COLORS["success_bg"], foreground=WALL_COLORS["success"],
                    font=("Microsoft YaHei UI", 10, "bold"), padx=12, pady=12, anchor="w",
                )
            self._incident_empty_label.pack(fill=tk.X, pady=(6, 0))
            return
        if hasattr(self, "_incident_empty_label"):
            self._incident_empty_label.pack_forget()
        self.incident_caption.configure(text=f"{len(incidents)} 项异常待处理", foreground=WALL_COLORS["danger"])
        for item in incidents:
            name = str(item.get("name", ""))
            current_names.add(name)
            row = self._incident_map.get(name)
            if row is None:
                row = self._create_incident_row(item, name)
                self._incident_map[name] = row
            else:
                self._update_incident_row(row, item)
            self.incident_rows.append(row)
        for stale_name in list(self._incident_map):
            if stale_name not in current_names:
                self._incident_map[stale_name].destroy()
                del self._incident_map[stale_name]

    def _create_incident_row(self, item: dict[str, Any], name: str) -> tk.Frame:
        row = tk.Frame(
            self.incident_container, background=WALL_COLORS["danger_bg"],
            highlightbackground=WALL_COLORS["danger_border"], highlightthickness=1, padx=10, pady=7, cursor="hand2",
        )
        row.pack(fill=tk.X, pady=(0, 7))
        top = tk.Frame(row, background=WALL_COLORS["danger_bg"])
        top.pack(fill=tk.X)
        name_label = tk.Label(
            top, text="", background=WALL_COLORS["danger_bg"], foreground=WALL_COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"), anchor="w",
        )
        name_label.pack(side=tk.LEFT)
        time_label = tk.Label(
            top, text="", background=WALL_COLORS["danger_bg"], foreground=WALL_COLORS["muted"],
            font=("Segoe UI", 8), anchor="e",
        )
        time_label.pack(side=tk.RIGHT)
        error_label = tk.Label(
            row, text="", background=WALL_COLORS["danger_bg"], foreground=WALL_COLORS["danger"],
            font=("Microsoft YaHei UI", 9), anchor="w",
        )
        error_label.pack(fill=tk.X, pady=(3, 0))
        for widget in (row, top, name_label, time_label, error_label):
            widget.bind("<Button-1>", lambda _event, value=name: self._on_open_target(value))
        row._name_label = name_label  # type: ignore[attr-defined]
        row._error_label = error_label  # type: ignore[attr-defined]
        row._time_label = time_label  # type: ignore[attr-defined]
        self._update_incident_row(row, item)
        return row

    @staticmethod
    def _update_incident_row(row: tk.Frame, item: dict[str, Any]) -> None:
        row._name_label.configure(text=f"● {_short_text(item.get('name'), 18)}")  # type: ignore[attr-defined]
        row._error_label.configure(text=_short_text(item.get("error_msg") or "连接失败", 34))  # type: ignore[attr-defined]
        timestamp = str(item.get("check_time") or "-").replace("T", " ")[-8:]
        row._time_label.configure(text=timestamp)  # type: ignore[attr-defined]

    def _render_inspection(self, inspection: Mapping[str, Any]) -> None:
        total, completed, up, down = (_count(inspection.get(key)) for key in ("total", "completed", "up", "down"))
        status = str(inspection.get("status", "not_started"))
        if status == "not_started":
            self.inspection_var.set(f"今日尚未开始巡检（待检 {total} 项）")
            self.inspection_detail_var.set("点击右上角“网页巡检”立即执行")
            progress = 0.0
        elif status == "running":
            self.inspection_var.set(f"正在巡检 {completed}/{total} 项 · 正常 {up} · 异常 {down}")
            self.inspection_detail_var.set(f"正常 {up} · 异常 {down} · 待检 {max(0, total - completed)}")
            progress = completed / total if total else 0.0
        else:
            self.inspection_var.set(f"已完成 {completed}/{total} 项 · 正常 {up} · 异常 {down}")
            self.inspection_detail_var.set(f"今日网页巡检已归档 · 正常 {up} · 异常 {down}")
            progress = completed / total if total else 1.0
        self._inspection_progress = max(0.0, min(1.0, progress))
        self._draw_inspection_bar()

    def _draw_inspection_bar(self) -> None:
        canvas = self.progress_canvas
        if not canvas.winfo_exists():
            return
        w, h = max(40, canvas.winfo_width()), max(8, canvas.winfo_height())
        canvas.delete("all")
        _draw_rounded_rect(canvas, 0, 2, w, h - 2, radius=4, fill=WALL_COLORS["no_data"], outline="")
        if self._inspection_progress > 0:
            fill_w = max(8.0, w * self._inspection_progress)
            _draw_rounded_rect(canvas, 0, 2, fill_w, h - 2, radius=4, fill=WALL_COLORS["accent"], outline="")

    def _render_trends(self, trends: list[dict[str, Any]]) -> None:
        if not getattr(self, "_trend_header_built", False):
            for column, title in enumerate(("目标", "成功率", "平均延迟")):
                self.trend_container.grid_columnconfigure(column, weight=3 if column == 0 else 1)
                tk.Label(
                    self.trend_container, text=title, background=WALL_COLORS["panel"],
                    foreground=WALL_COLORS["dim"], font=("Microsoft YaHei UI", 8, "bold"), anchor="w",
                ).grid(row=0, column=column, sticky="ew", padx=(0, 10), pady=(0, 4))
            self._trend_header_built = True
        current_names: set[str] = set()
        for row_index, item in enumerate(trends, start=1):
            name = str(item.get("name", ""))
            current_names.add(name)
            row = self._trend_rows.get(name)
            if row is None:
                row = self._create_trend_row(item, row_index)
                self._trend_rows[name] = row
            else:
                self._update_trend_row(row, item, row_index)
        for stale_name in list(self._trend_rows):
            if stale_name not in current_names:
                self._trend_rows[stale_name].destroy()
                del self._trend_rows[stale_name]
        if not trends:
            if not hasattr(self, "_trend_empty_label"):
                self._trend_empty_label = tk.Label(
                    self.trend_container, text="正在积累历史数据", background=WALL_COLORS["panel"],
                    foreground=WALL_COLORS["muted"], font=("Microsoft YaHei UI", 9),
                )
            self._trend_empty_label.grid(row=1, column=0, sticky="w")
        elif hasattr(self, "_trend_empty_label"):
            self._trend_empty_label.grid_forget()

    def _create_trend_row(self, item: dict[str, Any], row_index: int) -> tk.Frame:
        row = tk.Frame(self.trend_container, background=WALL_COLORS["panel"])
        row.grid(row=row_index, column=0, columnspan=3, sticky="ew", pady=2)
        for column in range(3):
            row.grid_columnconfigure(column, weight=3 if column == 0 else 1)
        row._cells = []  # type: ignore[attr-defined]
        for column in range(3):
            cell = tk.Label(
                row, text="", background=WALL_COLORS["panel"], foreground=WALL_COLORS["text"],
                font=("Microsoft YaHei UI", 9), anchor="w",
            )
            cell.grid(row=0, column=column, sticky="ew", padx=(0, 10))
            row._cells.append(cell)  # type: ignore[attr-defined]
        self._update_trend_row(row, item, row_index)
        return row

    @staticmethod
    def _update_trend_row(row: tk.Frame, item: dict[str, Any], row_index: int) -> None:
        row.grid_configure(row=row_index)
        total = _count(item.get("total"))
        ratio = (_count(item.get("up")) / total) if total else None
        success = "--" if ratio is None else f"{round(ratio * 100)}%"
        latency = "--" if item.get("avg_latency") is None else f"{float(item['avg_latency']):.1f} ms"
        values = (_short_text(item.get("name"), 16), success, latency)
        cells = row._cells  # type: ignore[attr-defined]
        for column, value in enumerate(values):
            if column == 1 and ratio is not None:
                color = WALL_COLORS["success"] if ratio >= 0.95 else WALL_COLORS["warning"] if ratio >= 0.8 else WALL_COLORS["danger"]
            else:
                color = WALL_COLORS["text"]
            cells[column].configure(text=value, foreground=color)

    def _render_heatmap(self, heatmap: Mapping[str, Any]) -> None:
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
        name_width = min(126, max(86, width // 5))
        grid_left, grid_right = name_width + 6, width - 4
        cell_width = max(4.0, (grid_right - grid_left) / len(labels))
        row_top, usable_height = 20, max(18, height - 24)
        row_height = max(16, min(24, usable_height // max(1, len(rows))))
        self._heatmap_hitboxes = []
        for column, label in enumerate(labels):
            if column % 4 == 0 or column == len(labels) - 1:
                x = grid_left + column * cell_width
                canvas.create_text(x, 4, anchor="nw", text=str(label), fill=WALL_COLORS["dim"], font=("Segoe UI", 7))
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            y = row_top + row_index * row_height
            target_name = str(row.get("name", "-"))
            canvas.create_text(
                0, y + (row_height - 4) / 2, anchor="w", text=_short_text(target_name, 13),
                fill=WALL_COLORS["text"], font=("Microsoft YaHei UI", 8, "bold"),
            )
            points = row.get("points", [])
            if not isinstance(points, list):
                points = []
            for column in range(len(labels)):
                point = points[column] if column < len(points) else None
                color = {
                    "up": WALL_COLORS["success"], "down": WALL_COLORS["danger"],
                    "mixed": WALL_COLORS["mixed"], "unknown": WALL_COLORS["warning"],
                }.get(point, WALL_COLORS["no_data"])
                x1 = grid_left + column * cell_width + 1.5
                x2 = grid_left + (column + 1) * cell_width - 1.5
                _draw_rounded_rect(canvas, x1, y + 3, x2, y + row_height - 4, radius=3, fill=color, outline="")
            self._heatmap_hitboxes.append((y, y + row_height, target_name))
        hidden_count = _count(self._heatmap.get("hidden_row_count")) if hasattr(self, "_heatmap") else 0
        if hidden_count:
            canvas.create_text(
                0, height - 2, anchor="sw", text=f"另有 {hidden_count} 个目标可在工作台查看",
                fill=WALL_COLORS["dim"], font=("Microsoft YaHei UI", 8),
            )

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
