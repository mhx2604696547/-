"""Target history latency-chart dialog and live Ping / Tracert diagnostics dialog."""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from app.utils.database import MonitorDatabase, extract_status_transitions


class TargetHistoryDialog(tk.Toplevel):
    """Display latency trend chart and state-transition timeline for a single target."""

    def __init__(self, parent: tk.Misc, target: dict[str, Any], database: MonitorDatabase) -> None:
        super().__init__(parent)
        self.target = target
        self.database = database
        self._records: list[dict[str, Any]] = []
        self._hours_var = tk.IntVar(value=24)

        self.title(f"历史趋势与故障时间线 · {target.get('name', '')}")
        self.resizable(True, True)
        self.transient(parent)
        self.minsize(720, 500)
        self.geometry("800x560")
        self._build_widgets()
        self._load_data()

    def _build_widgets(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=3)
        body.rowconfigure(2, weight=2)

        top = ttk.Frame(body)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.summary_var = tk.StringVar(value="正在加载历史数据...")
        ttk.Label(top, textvariable=self.summary_var, font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)

        range_box = ttk.Frame(top)
        range_box.pack(side=tk.RIGHT)
        for hrs, label in ((1, "近 1 小时"), (6, "近 6 小时"), (24, "近 24 小时")):
            ttk.Radiobutton(
                range_box, text=label, value=hrs, variable=self._hours_var, command=self._load_data,
            ).pack(side=tk.LEFT, padx=(6, 0))

        chart_box = ttk.LabelFrame(body, text="延迟变化曲线 (ms)", padding=8)
        chart_box.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        chart_box.columnconfigure(0, weight=1)
        chart_box.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(chart_box, background="#0F172A", highlightthickness=0, height=220)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _e: self._draw_chart())

        events_box = ttk.LabelFrame(body, text="状态跳变时间线（最新在前）", padding=8)
        events_box.grid(row=2, column=0, sticky="nsew")
        events_box.columnconfigure(0, weight=1)
        events_box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            events_box, columns=("time", "change", "duration", "error"), show="headings", height=6,
        )
        for key, title, width in (
            ("time", "发生时间", 150), ("change", "状态变化", 130), ("duration", "前状态持续", 100), ("error", "说明 / 错误", 320),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=tk.CENTER if key in {"change", "duration"} else tk.W)
        sb = ttk.Scrollbar(events_box, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("down", foreground="#D14343")
        self.tree.tag_configure("up", foreground="#16854B")

    def _load_data(self) -> None:
        hours = self._hours_var.get()
        try:
            self._records = self.database.get_target_history(self.target, hours=hours, limit=400)
        except Exception as error:
            self.summary_var.set(f"读取历史记录失败：{error}")
            self._records = []
            return

        total = len(self._records)
        up = sum(1 for r in self._records if r["status"] == "up")
        down = sum(1 for r in self._records if r["status"] == "down")
        latencies = [float(r["latency"]) for r in self._records if r.get("latency") is not None]
        if total == 0:
            self.summary_var.set(f"近 {hours} 小时暂无历史检测记录")
        else:
            rate = round(up / total * 100, 1)
            avg_lat = f"{sum(latencies) / len(latencies):.1f} ms" if latencies else "-"
            max_lat = f"{max(latencies):.1f} ms" if latencies else "-"
            self.summary_var.set(f"采样 {total} 次 · 成功率 {rate}% · 异常 {down} 次 · 平均 {avg_lat} · 峰值 {max_lat}")

        self._draw_chart()
        self.tree.delete(*self.tree.get_children())
        transitions = extract_status_transitions(self._records)
        labels = {"up": "正常", "down": "异常", "unknown": "未知"}
        for ev in transitions[:40]:
            change_txt = f"{labels.get(ev['from_status'], ev['from_status'])} → {labels.get(ev['to_status'], ev['to_status'])}"
            tag = "down" if ev["to_status"] == "down" else "up"
            self.tree.insert("", tk.END, values=(ev["time"], change_txt, ev["duration"], ev["error_msg"]), tags=(tag,))

    def _draw_chart(self) -> None:
        if not self.canvas.winfo_exists():
            return
        c = self.canvas
        c.delete("all")
        w, h = max(240, c.winfo_width()), max(140, c.winfo_height())
        if not self._records:
            c.create_text(w / 2, h / 2, text="暂无历史采样数据", fill="#94A3B8", font=("Microsoft YaHei UI", 10))
            return

        pad_l, pad_r, pad_t, pad_b = 50, 18, 18, 28
        plot_w = max(40, w - pad_l - pad_r)
        plot_h = max(40, h - pad_t - pad_b)

        latencies = [float(r["latency"]) for r in self._records if r.get("latency") is not None]
        max_val = max(latencies) if latencies else 10.0
        y_top = max(5.0, max_val * 1.15)

        # Horizontal grid lines
        for i in range(4):
            y = pad_t + plot_h * (i / 3)
            val = y_top * (1 - i / 3)
            c.create_line(pad_l, y, w - pad_r, y, fill="#1E293B", dash=(3, 3))
            c.create_text(pad_l - 6, y, anchor="e", text=f"{val:.0f}", fill="#94A3B8", font=("Segoe UI", 8))

        n = len(self._records)
        step_x = plot_w / max(1, n - 1)
        prev_pt: tuple[float, float] | None = None
        for idx, rec in enumerate(self._records):
            x = pad_l + idx * step_x
            if rec["status"] == "down" or rec.get("latency") is None:
                c.create_oval(x - 3, pad_t + plot_h - 6, x + 3, pad_t + plot_h, fill="#F43F5E", outline="")
                prev_pt = None
                continue
            lat = float(rec["latency"])
            y = pad_t + plot_h * (1.0 - min(1.0, lat / y_top))
            if prev_pt is not None:
                c.create_line(prev_pt[0], prev_pt[1], x, y, fill="#38BDF8", width=2)
            prev_pt = (x, y)

        # Start / end timestamps
        first_t = str(self._records[0].get("check_time", "")).replace("T", " ")[-8:]
        last_t = str(self._records[-1].get("check_time", "")).replace("T", " ")[-8:]
        c.create_text(pad_l, h - 10, anchor="w", text=first_t, fill="#64748B", font=("Segoe UI", 8))
        c.create_text(w - pad_r, h - 10, anchor="e", text=last_t, fill="#64748B", font=("Segoe UI", 8))


class NetworkDiagDialog(tk.Toplevel):
    """Streaming live Ping and Tracert diagnostic window."""

    def __init__(self, parent: tk.Misc, *, initial_host: str = "", mode: str = "ping") -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen[bytes] | None = None
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self.title("网络排障工具 · Ping / 路由追踪")
        self.resizable(True, True)
        self.transient(parent)
        self.minsize(640, 420)
        self.geometry("720x480")

        self.host_var = tk.StringVar(value=initial_host.strip())
        self.mode_var = tk.StringVar(value=mode if mode in ("ping", "tracert") else "ping")
        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.host_var.get():
            self.after(150, self._start)

    def _build_widgets(self) -> None:
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        bar = ttk.Frame(body)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(bar, text="目标主机").pack(side=tk.LEFT)
        ttk.Entry(bar, textvariable=self.host_var, width=26).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Radiobutton(bar, text="持续 Ping", value="ping", variable=self.mode_var).pack(side=tk.LEFT)
        ttk.Radiobutton(bar, text="路由追踪 (Tracert)", value="tracert", variable=self.mode_var).pack(side=tk.LEFT, padx=(8, 12))
        self.start_btn = ttk.Button(bar, text="开始", command=self._start, style="Primary.TButton")
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(bar, text="停止", command=self._stop, state=tk.DISABLED, style="Secondary.TButton")
        self.stop_btn.pack(side=tk.LEFT, padx=(6, 0))

        out_box = ttk.Frame(body)
        out_box.grid(row=1, column=0, sticky="nsew")
        out_box.columnconfigure(0, weight=1)
        out_box.rowconfigure(0, weight=1)
        self.output = tk.Text(
            out_box, background="#0B1120", foreground="#E2E8F0", insertbackground="#FFFFFF",
            font=("Consolas", 10), wrap=tk.WORD, state=tk.DISABLED,
        )
        sb = ttk.Scrollbar(out_box, orient=tk.VERTICAL, command=self.output.yview)
        self.output.configure(yscrollcommand=sb.set)
        self.output.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

    def _append(self, text: str) -> None:
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.configure(state=tk.DISABLED)

    def _start(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showerror("输入错误", "请输入主机 IP 或域名。", parent=self)
            return
        self._stop()
        self.output.configure(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.configure(state=tk.DISABLED)

        mode = self.mode_var.get()
        if sys.platform == "win32":
            cmd = ["ping", "-t", host] if mode == "ping" else ["tracert", "-d", host]
        else:
            cmd = ["ping", host] if mode == "ping" else ["traceroute", "-n", host]

        self._append(f"> {' '.join(cmd)}\n\n")
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self._queue = queue.Queue()

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self._proc = proc
                assert proc.stdout is not None
                for raw_line in iter(proc.stdout.readline, b""):
                    try:
                        decoded = raw_line.decode("gbk", errors="replace") if sys.platform == "win32" else raw_line.decode("utf-8", errors="replace")
                    except Exception:
                        decoded = raw_line.decode("utf-8", errors="replace")
                    self._queue.put(decoded)
                proc.wait()
            except Exception as error:
                self._queue.put(f"\n执行失败：{error}\n")
            finally:
                self._proc = None
                self._queue.put(None)

        threading.Thread(target=worker, name="net-diag", daemon=True).start()
        self.after(80, self._poll)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    self.start_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self._append("\n--- 诊断已结束 ---\n")
                    return
                self._append(item)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _stop(self) -> None:
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            self._proc = None

    def _on_close(self) -> None:
        self._stop()
        self.destroy()
