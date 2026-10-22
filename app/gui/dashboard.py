"""Read-only dashboard widgets for the personal operations workbench."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from app.gui.theme import PALETTE


STATUS_COLORS = {"up": PALETTE["success"], "down": PALETTE["danger"], "unknown": PALETTE["warning"]}


def inspection_footer_state(inspection: dict[str, Any]) -> dict[str, Any]:
    """Format one persisted daily checklist into a small, user-facing state.

    Keeping this transformation pure lets the always-visible footer use the
    exact same wording as the dashboard without querying the database again.
    """
    def count(name: str) -> int:
        try:
            return max(0, int(inspection.get(name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    run_id = inspection.get("run_id")
    status = str(inspection.get("status", "not_started"))
    total, completed, up, down = (count(name) for name in ("total", "completed", "up", "down"))
    if status == "not_started":
        text, progress = f"今日网页巡检 · 待检 {total} 项", 0
    elif status == "running":
        progress = round((completed / total) * 100) if total else 0
        text = f"今日网页巡检 · 进行中 {completed}/{total} · 正常 {up} · 异常 {down}"
    else:
        progress = round((completed / total) * 100) if total else 100
        text = f"今日网页巡检 · 已完成 · 正常 {up} · 异常 {down}"
    return {
        "text": text,
        "progress": min(100, max(0, progress)),
        "can_retry": bool(run_id and down),
        "can_export": bool(run_id),
    }


class DashboardPanel(ttk.Frame):
    """Shows saved monitor data without triggering extra network checks."""

    def __init__(
        self,
        parent: tk.Misc,
        on_filter_status: Callable[[str | None], None],
        on_open_target: Callable[[str], None],
        on_retry_failures: Callable[[], None],
        on_export_today: Callable[[], None],
    ) -> None:
        super().__init__(parent, padding=(20, 18, 20, 14), style="Workbench.TFrame")
        self._on_filter_status = on_filter_status
        self._on_open_target = on_open_target
        # Actions live in MainWindow's always-visible daily-check footer. Keep
        # these callbacks on the panel API so older callers remain compatible.
        self._on_retry_failures = on_retry_failures
        self._on_export_today = on_export_today
        self.current_run_id: str | None = None
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, style="Workbench.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        title_stack = ttk.Frame(header, style="Workbench.TFrame")
        title_stack.grid(row=0, column=0, sticky="w")
        ttk.Label(title_stack, text="Signal Desk", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(
            title_stack, text="异常优先：先处理故障，再查看稳定性与目标详情",
            style="TopbarSubtle.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        self.updated_var = tk.StringVar(value="等待首条检测结果")
        ttk.Label(header, textvariable=self.updated_var, style="TopbarSubtle.TLabel").grid(row=0, column=1, sticky="e")

        # One compact signal strip replaces the old stack of large cards.  It
        # keeps the counts available without competing with the exception
        # queue below, which is the operator's primary working area.
        signal_strip = ttk.Frame(self, style="Card.TFrame", padding=(14, 11))
        signal_strip.grid(row=1, column=0, sticky="ew", pady=(15, 12))
        for column in range(4):
            signal_strip.columnconfigure(column, weight=1, minsize=92)
        signal_strip.columnconfigure(5, weight=2, minsize=220)
        self.cards: dict[str, ttk.Frame] = {}
        self.card_values: dict[str, tk.StringVar] = {}
        for column, (key, title, color, filter_name, hint) in enumerate((
            ("total", "全部", PALETTE["info"], None, "目标"),
            ("up", "正常", STATUS_COLORS["up"], "up", "可用"),
            ("down", "异常", STATUS_COLORS["down"], "down", "待处理"),
            ("unknown", "未知", STATUS_COLORS["unknown"], "unknown", "待确认"),
        )):
            card = ttk.Frame(signal_strip, style="Card.TFrame", padding=(4, 0))
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 9, 0))
            ttk.Label(card, text=title, style="Muted.TLabel").pack(anchor="w")
            value = tk.StringVar(value="0")
            value_label = ttk.Label(card, textvariable=value, style="MetricValue.TLabel", foreground=color)
            value_label.pack(anchor="w", pady=(1, 0))
            hint_label = ttk.Label(card, text=hint, style="MetricHint.TLabel")
            hint_label.pack(anchor="w", pady=(0, 1))
            for widget in (card, value_label, hint_label):
                widget.bind("<Button-1>", lambda _event, state=filter_name: self._on_filter_status(state))
            self.cards[key] = card
            self.card_values[key] = value

        strip_separator = ttk.Separator(signal_strip, orient=tk.VERTICAL)
        strip_separator.grid(row=0, column=4, sticky="ns", padx=(12, 14))
        strip_context = ttk.Frame(signal_strip, style="Card.TFrame")
        strip_context.grid(row=0, column=5, sticky="nsew")
        ttk.Label(strip_context, text="本机监控", style="Muted.TLabel").pack(anchor="w")
        self.operating_state_var = tk.StringVar(value="监控服务未启动")
        self.operating_state_label = ttk.Label(strip_context, textvariable=self.operating_state_var, style="StatusWarn.TLabel")
        self.operating_state_label.pack(anchor="w", pady=(3, 5))
        self.storage_var = tk.StringVar(value="数据保存正常")
        ttk.Label(strip_context, textvariable=self.storage_var, style="Muted.TLabel", wraplength=250).pack(anchor="w")

        # Keep legacy state variables up to date for callers, but do not
        # duplicate the daily-inspection card here: the persistent footer is
        # the single, actionable place for that workflow.
        self.inspection_var = tk.StringVar(value="尚未生成今日巡检记录")
        self.inspection_time_var = tk.StringVar(value="点击右上角“网页巡检”开始")

        body = ttk.Frame(self, style="Workbench.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3, minsize=340)
        body.columnconfigure(1, weight=2, minsize=230)
        body.rowconfigure(0, weight=1)

        exception_box = ttk.Frame(body, style="Card.TFrame", padding=(14, 13))
        exception_box.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        exception_box.columnconfigure(0, weight=1)
        exception_box.rowconfigure(1, weight=1)
        exception_header = ttk.Frame(exception_box, style="Card.TFrame")
        exception_header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        exception_header.columnconfigure(0, weight=1)
        ttk.Label(exception_header, text="待处理异常", style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.exception_count_var = tk.StringVar(value="等待检测结果")
        self.exception_count_label = ttk.Label(
            exception_header, textvariable=self.exception_count_var, style="StatusWarn.TLabel",
        )
        self.exception_count_label.grid(row=0, column=1, sticky="e")
        self.exception_notice_var = tk.StringVar(value="异常会优先显示在这里")
        ttk.Label(
            exception_header, textvariable=self.exception_notice_var, style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        self.exception_tree = self._build_tree(
            exception_box,
            (("name", "名称", 95), ("address", "地址", 130), ("error", "原因", 155), ("check_time", "最后检测", 120)),
            height=9,
        )
        self.exception_tree.tag_configure("down", foreground=STATUS_COLORS["down"])
        self.exception_tree.bind("<Double-1>", self._open_exception_target)

        right_column = ttk.Frame(body, style="Workbench.TFrame")
        right_column.grid(row=0, column=1, sticky="nsew", padx=(9, 0))
        right_column.columnconfigure(0, weight=1)
        right_column.rowconfigure(1, weight=1)

        context_box = ttk.Frame(right_column, style="Card.TFrame", padding=(14, 13))
        context_box.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        ttk.Label(context_box, text="工作提示", style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(
            context_box,
            text="网页与设备页面巡检请使用窗口底部常驻栏；需要重测或导出时无需离开当前视图。",
            style="Muted.TLabel", wraplength=300, justify=tk.LEFT,
        ).pack(anchor="w", pady=(6, 0))

        trend_box = ttk.Frame(right_column, style="Card.TFrame", padding=(14, 13))
        trend_box.grid(row=1, column=0, sticky="nsew")
        trend_box.columnconfigure(0, weight=1)
        trend_box.rowconfigure(1, weight=1)
        trend_header = ttk.Frame(trend_box, style="Card.TFrame")
        trend_header.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        ttk.Label(trend_header, text="近 24 小时稳定性", style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(trend_header, text="基于已保存历史，不会触发新的检测", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self.trend_tree = self._build_tree(
            trend_box,
            (("name", "目标", 88), ("success", "成功率", 70), ("latency", "平均延迟(ms)", 82), ("check_time", "最近检测", 96)),
            height=6,
        )

    @staticmethod
    def _build_tree(
        parent: ttk.Frame, definitions: tuple[tuple[str, str, int], ...], height: int,
    ) -> ttk.Treeview:
        table = ttk.Frame(parent, style="Card.TFrame")
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = tuple(item[0] for item in definitions)
        tree = ttk.Treeview(table, columns=columns, show="headings", height=height)
        for key, title, width in definitions:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor=tk.CENTER if key in {"success", "latency", "check_time"} else tk.W)
        scrollbar = ttk.Scrollbar(table, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        return tree

    def refresh(self, snapshot: dict[str, Any], monitoring_running: bool, storage_error: str = "") -> None:
        """Render a saved-data snapshot supplied by Tk's main thread."""
        counts = snapshot["status_counts"]
        for key in ("total", "up", "down", "unknown"):
            self.card_values[key].set(str(counts.get(key, 0)))
        generated_at = str(snapshot.get("generated_at", "")).replace("T", " ")
        self.updated_var.set(f"数据更新时间：{generated_at or '-'}")

        inspection = snapshot["today_inspection"]
        self.current_run_id = inspection.get("run_id")
        footer = inspection_footer_state(inspection)
        if inspection.get("status") == "not_started":
            self.inspection_var.set(footer["text"])
            self.inspection_time_var.set("点击右上角“网页巡检”开始")
        else:
            self.inspection_var.set(footer["text"])
            changed_at = str(inspection.get("finished_at") or inspection.get("started_at") or "-").replace("T", " ")
            self.inspection_time_var.set(f"本轮更新时间：{changed_at}")

        if monitoring_running:
            self.operating_state_var.set("监控服务运行中")
            self.operating_state_label.configure(style="StatusGood.TLabel")
        else:
            self.operating_state_var.set("监控服务已停止或正在启动")
            self.operating_state_label.configure(style="StatusWarn.TLabel")
        self.storage_var.set(f"数据保存：{storage_error}" if storage_error else "数据保存正常")
        self._render_exceptions(snapshot["exceptions"])
        self._render_trends(snapshot["trends"])

    def _render_exceptions(self, exceptions: list[dict[str, Any]]) -> None:
        self.exception_tree.delete(*self.exception_tree.get_children())
        count = len(exceptions)
        if count:
            self.exception_count_var.set(f"{count} 项异常待处理")
            self.exception_count_label.configure(style="DangerPill.TLabel")
            self.exception_notice_var.set("双击异常项可定位到目标列表")
        else:
            self.exception_count_var.set("当前无异常")
            self.exception_count_label.configure(style="StatusGood.TLabel")
            self.exception_notice_var.set("当前没有待处理的异常；未知目标仍建议确认")
        for item in exceptions:
            self.exception_tree.insert(
                "", tk.END, iid=item["key"], tags=("down",),
                values=(item["name"], item["address"], item.get("error_msg") or "连接失败", str(item.get("check_time") or "").replace("T", " ")),
            )

    def _render_trends(self, trends: list[dict[str, Any]]) -> None:
        self.trend_tree.delete(*self.trend_tree.get_children())
        for item in trends:
            total = int(item["total"])
            success = "-" if total == 0 else f"{int(item['up']) / total:.0%} ({int(item['up'])}/{total})"
            latency = "-" if item.get("avg_latency") is None else f"{float(item['avg_latency']):.1f}"
            self.trend_tree.insert(
                "", tk.END, iid=item["key"],
                values=(item["name"], success, latency, str(item.get("last_check_time") or "").replace("T", " ")),
            )

    def _open_exception_target(self, _event: tk.Event[tk.Misc]) -> None:
        selection = self.exception_tree.selection()
        if selection:
            self._on_open_target(self.exception_tree.item(selection[0], "values")[0])
