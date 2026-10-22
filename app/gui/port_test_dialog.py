"""Manual port connectivity testing dialog."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from app.core import port_scan


class PortTestDialog(tk.Toplevel):
    """Lets an operator manually test whether one or more TCP ports are open."""

    def __init__(self, parent: tk.Misc, logger: Any) -> None:
        super().__init__(parent)
        self.title("端口测试工具")
        self.resizable(True, True)
        self.transient(parent)
        self.logger = logger
        self._stop_event: threading.Event | None = None
        self._result_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._total = 0
        self._done_count = 0
        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self) -> None:
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        form = ttk.Frame(body)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)
        self.host_var = tk.StringVar(value="")
        self.ports_var = tk.StringVar(value="80,443")
        self.timeout_var = tk.StringVar(value="1.5")
        ttk.Label(form, text="主机地址").grid(row=0, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Entry(form, textvariable=self.host_var, width=40).grid(row=0, column=1, sticky=tk.EW, pady=3)
        ttk.Label(form, text="端口").grid(row=1, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Entry(form, textvariable=self.ports_var, width=40).grid(row=1, column=1, sticky=tk.EW, pady=3)
        ttk.Label(form, text="例如：80,443,8000-8010", foreground="#666666").grid(row=1, column=2, sticky=tk.W, padx=(8, 0))
        ttk.Label(form, text="超时(秒)").grid(row=2, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Entry(form, textvariable=self.timeout_var, width=10).grid(row=2, column=1, sticky=tk.W, pady=3)

        controls = ttk.Frame(body)
        controls.pack(fill=tk.X, pady=(10, 8))
        self.start_button = ttk.Button(controls, text="开始测试", command=self._start_scan)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(controls, text="停止", command=self._stop_scan, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=8)
        self.progress_var = tk.StringVar(value="")
        ttk.Label(controls, textvariable=self.progress_var).pack(side=tk.RIGHT)

        columns = ("port", "status", "latency", "error")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", height=14)
        for key, title, width in (
            ("port", "端口", 80), ("status", "状态", 80), ("latency", "延迟(ms)", 90), ("error", "说明", 320),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=tk.CENTER if key in {"port", "status", "latency"} else tk.W)
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("open", foreground="#14833b")
        self.tree.tag_configure("closed", foreground="#c5221f")

    def _start_scan(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showerror("输入错误", "请输入主机地址。", parent=self)
            return
        try:
            ports = port_scan.parse_ports(self.ports_var.get())
            timeout = float(self.timeout_var.get())
            if timeout <= 0:
                raise ValueError("超时时间必须大于 0。")
        except ValueError as error:
            messagebox.showerror("输入错误", str(error), parent=self)
            return

        self.tree.delete(*self.tree.get_children())
        self._total = len(ports)
        self._done_count = 0
        self.progress_var.set(f"正在测试 0/{self._total}")
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self._result_queue = queue.Queue()
        self._stop_event = threading.Event()
        thread = threading.Thread(
            target=port_scan.scan_ports,
            args=(host, ports, timeout, self._stop_event, self._result_queue),
            daemon=True,
        )
        thread.start()
        self.after(100, self._poll_results)

    def _stop_scan(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        self.stop_button.configure(state=tk.DISABLED)

    def _poll_results(self) -> None:
        try:
            while True:
                item = self._result_queue.get_nowait()
                if item.get("done"):
                    self.start_button.configure(state=tk.NORMAL)
                    self.stop_button.configure(state=tk.DISABLED)
                    self.progress_var.set(f"完成 {self._done_count}/{self._total}")
                    return
                self._done_count += 1
                status = item["status"]
                latency = "-" if item.get("latency") is None else f"{float(item['latency']):.2f}"
                self.tree.insert("", tk.END, values=(
                    item["port"], "开放" if status == "open" else "关闭", latency, item.get("error_msg", ""),
                ), tags=(status,))
                self.progress_var.set(f"正在测试 {self._done_count}/{self._total}")
        except queue.Empty:
            pass
        self.after(100, self._poll_results)

    def _on_close(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        self.destroy()
