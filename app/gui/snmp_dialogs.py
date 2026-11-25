"""SNMP device inspection detail dialog and subnet SNMP auto-discovery dialog."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from app.core import snmp_check
from app.gui.config_dialog import DEFAULT_GROUP, DEVICE_ROLE_LABELS, expand_ip_range, normalize_target


class SnmpDetailDialog(tk.Toplevel):
    """On-demand SNMP device details: uptime, sysDescr, switch ports, and server resources."""

    def __init__(self, parent: tk.Misc, target: dict[str, Any]) -> None:
        super().__init__(parent)
        self.target = target
        self._loading = False
        self.title(f"SNMP 设备详情 · {target.get('name', target.get('address', ''))}")
        self.resizable(True, True)
        self.transient(parent)
        self.minsize(680, 480)
        self.geometry("760x540")
        self._build_widgets()
        self._refresh()

    def _build_widgets(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        head = ttk.Frame(body)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        head.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="正在通过 SNMP 读取设备实时状态...")
        ttk.Label(head, textvariable=self.status_var, font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.refresh_btn = ttk.Button(head, text="刷新", command=self._refresh, style="Primary.TButton")
        self.refresh_btn.grid(row=0, column=1, sticky="e")

        info = ttk.LabelFrame(body, text="基础信息", padding=10)
        info.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        info.columnconfigure(1, weight=1)
        info.columnconfigure(3, weight=1)
        self.sys_name_var = tk.StringVar(value="-")
        self.uptime_var = tk.StringVar(value="-")
        self.latency_var = tk.StringVar(value="-")
        self.resource_var = tk.StringVar(value="-")
        self.sys_descr_var = tk.StringVar(value="-")
        ttk.Label(info, text="设备名：").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Label(info, textvariable=self.sys_name_var, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(info, text="运行时长：").grid(row=0, column=2, sticky="w", padx=(16, 0), pady=2)
        ttk.Label(info, textvariable=self.uptime_var, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=3, sticky="w", pady=2)
        ttk.Label(info, text="管理地址：").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Label(info, text=str(self.target.get("address", "-"))).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(info, text="响应延迟：").grid(row=1, column=2, sticky="w", padx=(16, 0), pady=2)
        ttk.Label(info, textvariable=self.latency_var).grid(row=1, column=3, sticky="w", pady=2)
        ttk.Label(info, text="资源概览：").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Label(info, textvariable=self.resource_var).grid(row=2, column=1, columnspan=3, sticky="w", pady=2)
        ttk.Label(info, text="系统描述：").grid(row=3, column=0, sticky="nw", pady=2)
        ttk.Label(info, textvariable=self.sys_descr_var, wraplength=620, justify=tk.LEFT).grid(row=3, column=1, columnspan=3, sticky="w", pady=2)

        self.notebook = ttk.Notebook(body)
        self.notebook.grid(row=2, column=0, sticky="nsew")

        # Tab 1: Switch / Interface table
        if_tab = ttk.Frame(self.notebook, padding=8)
        if_tab.columnconfigure(0, weight=1)
        if_tab.rowconfigure(0, weight=1)
        self.if_tree = ttk.Treeview(
            if_tab, columns=("index", "name", "speed", "admin", "oper", "in_rate", "out_rate"), show="headings",
        )
        for key, title, width in (
            ("index", "索引", 50), ("name", "端口名称", 210), ("speed", "速率", 68),
            ("admin", "管理状态", 78), ("oper", "运行状态", 78), ("in_rate", "入带宽", 88), ("out_rate", "出带宽", 88),
        ):
            self.if_tree.heading(key, text=title)
            self.if_tree.column(key, width=width, anchor=tk.CENTER if key != "name" else tk.W)
        if_sb = ttk.Scrollbar(if_tab, orient=tk.VERTICAL, command=self.if_tree.yview)
        self.if_tree.configure(yscrollcommand=if_sb.set)
        self.if_tree.grid(row=0, column=0, sticky="nsew")
        if_sb.grid(row=0, column=1, sticky="ns")
        self.if_tree.tag_configure("up", foreground="#16854B")
        self.if_tree.tag_configure("down", foreground="#68778C")
        self.notebook.add(if_tab, text="网络端口状态")

        # Tab 2: Disk partitions
        disk_tab = ttk.Frame(self.notebook, padding=8)
        disk_tab.columnconfigure(0, weight=1)
        disk_tab.rowconfigure(0, weight=1)
        self.disk_tree = ttk.Treeview(
            disk_tab, columns=("name", "used", "total", "percent"), show="headings",
        )
        for key, title, width in (
            ("name", "挂载点 / 磁盘", 300), ("used", "已用 (GB)", 100), ("total", "总容量 (GB)", 100), ("percent", "使用率", 90),
        ):
            self.disk_tree.heading(key, text=title)
            self.disk_tree.column(key, width=width, anchor=tk.CENTER if key != "name" else tk.W)
        disk_sb = ttk.Scrollbar(disk_tab, orient=tk.VERTICAL, command=self.disk_tree.yview)
        self.disk_tree.configure(yscrollcommand=disk_sb.set)
        self.disk_tree.grid(row=0, column=0, sticky="nsew")
        disk_sb.grid(row=0, column=1, sticky="ns")
        self.disk_tree.tag_configure("warn", foreground="#BD7500")
        self.disk_tree.tag_configure("crit", foreground="#D14343")
        self.notebook.add(disk_tab, text="磁盘分区")

        if str(self.target.get("device_role", "other")) == "server":
            self.notebook.select(1)

    def _refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.refresh_btn.configure(state=tk.DISABLED)
        self.status_var.set("正在采集 SNMP 设备状态...")

        def worker() -> None:
            data = snmp_check.inspect_device(
                str(self.target.get("address", "")),
                port=int(self.target.get("snmp_port", 161)),
                version=str(self.target.get("snmp_version", "v2c")),
                community=str(self.target.get("snmp_community", "public")),
                device_role=str(self.target.get("device_role", "other")),
            )
            try:
                self.after(0, lambda: self._render(data))
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="snmp-inspect", daemon=True).start()

    def _render(self, data: dict[str, Any]) -> None:
        self._loading = False
        try:
            self.refresh_btn.configure(state=tk.NORMAL)
        except tk.TclError:
            return
        if not data.get("ok"):
            self.status_var.set(f"采集失败：{data.get('error_msg', '未知错误')}")
            return
        self.status_var.set("实时采集成功")
        self.sys_name_var.set(str(data.get("sys_name") or "-"))
        self.uptime_var.set(str(data.get("uptime") or "-"))
        self.latency_var.set(f"{data.get('latency', '-')} ms")
        self.sys_descr_var.set(str(data.get("sys_descr") or "-"))

        parts = []
        if data.get("cpu_percent") is not None:
            parts.append(f"CPU {float(data['cpu_percent']):.0f}%")
        if data.get("mem_percent") is not None:
            parts.append(f"内存 {float(data['mem_percent']):.0f}%")
        interfaces = data.get("interfaces", [])
        if interfaces:
            up_cnt = sum(1 for item in interfaces if item.get("oper_code") == 1)
            down_cnt = sum(1 for item in interfaces if item.get("oper_code") == 2)
            parts.append(f"端口 {len(interfaces)} 个（{up_cnt} UP / {down_cnt} DOWN）")
        self.resource_var.set(" · ".join(parts) if parts else "无额外资源指标")

        self.if_tree.delete(*self.if_tree.get_children())
        for iface in interfaces:
            tag = "up" if iface.get("oper_code") == 1 else "down"
            in_r = "-" if iface.get("in_mbps") is None else f"{iface['in_mbps']:.2f} Mbps"
            out_r = "-" if iface.get("out_mbps") is None else f"{iface['out_mbps']:.2f} Mbps"
            self.if_tree.insert("", tk.END, values=(
                iface["index"], iface["name"], iface["speed"], iface["admin_status"], iface["oper_status"], in_r, out_r,
            ), tags=(tag,))

        self.disk_tree.delete(*self.disk_tree.get_children())
        for disk in data.get("disks", []):
            pct = float(disk.get("percent", 0))
            tag = "crit" if pct >= 85 else "warn" if pct >= 70 else ""
            self.disk_tree.insert("", tk.END, values=(
                disk["name"],
                "-" if disk.get("used_gb") is None else f"{disk['used_gb']:.1f}",
                "-" if disk.get("total_gb") is None else f"{disk['total_gb']:.1f}",
                f"{pct:.1f}%",
            ), tags=(tag,) if tag else ())


class SnmpDiscoveryDialog(tk.Toplevel):
    """Concurrent subnet SNMP scanner that imports discovered switches/servers in one click."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        existing_addresses: set[str],
        existing_names: set[str],
        available_groups: list[str],
        on_import: Callable[[list[dict[str, Any]]], None],
    ) -> None:
        super().__init__(parent)
        self._existing_addresses = set(existing_addresses)
        self._existing_names = set(existing_names)
        self._groups = sorted({DEFAULT_GROUP, *(g.strip() for g in available_groups if g.strip())})
        self._on_import = on_import
        self._stop_event: threading.Event | None = None
        self._result_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._total = 0
        self._done = 0
        self._discovered: dict[str, dict[str, Any]] = {}
        self._selected: dict[str, bool] = {}

        self.title("网段 SNMP 自动发现")
        self.resizable(True, True)
        self.transient(parent)
        self.minsize(740, 500)
        self.geometry("820x540")
        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        form = ttk.LabelFrame(body, text="扫描范围与 SNMP 参数", padding=10)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        self.range_var = tk.StringVar(value="192.168.1.1-50")
        self.community_var = tk.StringVar(value="public")
        self.version_var = tk.StringVar(value="v2c")
        self.port_var = tk.StringVar(value="161")
        self.group_var = tk.StringVar(value=DEFAULT_GROUP)

        ttk.Label(form, text="IP 范围").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(form, textvariable=self.range_var, width=28).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(form, text="例如 192.168.1.1-254，多个范围用逗号分隔", foreground="#68778C").grid(row=0, column=2, columnspan=3, sticky="w", padx=(10, 0))

        row2 = ttk.Frame(form)
        row2.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(4, 0))
        ttk.Label(row2, text="Community").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.community_var, width=14, show="*").pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(row2, text="版本").pack(side=tk.LEFT)
        ttk.Combobox(row2, textvariable=self.version_var, values=("v2c", "v1"), state="readonly", width=6).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(row2, text="端口").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.port_var, width=6).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(row2, text="导入分组").pack(side=tk.LEFT)
        ttk.Combobox(row2, textvariable=self.group_var, values=self._groups, width=10).pack(side=tk.LEFT, padx=(6, 0))

        controls = ttk.Frame(body)
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        self.start_btn = ttk.Button(controls, text="开始扫描", command=self._start_scan, style="Primary.TButton")
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(controls, text="停止", command=self._stop_scan, state=tk.DISABLED, style="Secondary.TButton")
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.progress_var = tk.StringVar(value="填写网段后点击“开始扫描”；单击结果行可切换勾选")
        ttk.Label(controls, textvariable=self.progress_var, foreground="#68778C").pack(side=tk.RIGHT)

        table_box = ttk.Frame(body)
        table_box.grid(row=2, column=0, sticky="nsew")
        table_box.columnconfigure(0, weight=1)
        table_box.rowconfigure(0, weight=1)
        cols = ("pick", "address", "sys_name", "role", "uptime", "latency", "descr")
        self.tree = ttk.Treeview(table_box, columns=cols, show="headings")
        for key, title, width in (
            ("pick", "选择", 52), ("address", "IP 地址", 120), ("sys_name", "设备名", 130),
            ("role", "识别类型", 76), ("uptime", "运行时长", 96), ("latency", "延迟(ms)", 72), ("descr", "系统描述", 240),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=tk.CENTER if key in {"pick", "role", "uptime", "latency"} else tk.W)
        sb = ttk.Scrollbar(table_box, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("existing", foreground="#94A3B8")
        self.tree.bind("<Button-1>", self._toggle_row)

        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.import_btn = ttk.Button(footer, text="导入选中设备", command=self._import_selected, style="Primary.TButton")
        self.import_btn.pack(side=tk.RIGHT)
        ttk.Button(footer, text="关闭", command=self._on_close, style="Secondary.TButton").pack(side=tk.RIGHT, padx=(0, 8))

    def _parse_addresses(self) -> list[str]:
        addresses: list[str] = []
        seen: set[str] = set()
        for token in self.range_var.get().replace("\n", ",").split(","):
            item = token.strip()
            if not item:
                continue
            for _name, addr in expand_ip_range(item):
                if addr not in seen:
                    seen.add(addr)
                    addresses.append(addr)
        return addresses

    def _start_scan(self) -> None:
        addresses = self._parse_addresses()
        if not addresses:
            messagebox.showerror("输入错误", "请输入至少一个 IP 或网段范围。", parent=self)
            return
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "SNMP 端口必须在 1-65535 之间。", parent=self)
            return
        community = self.community_var.get().strip()
        if not community:
            messagebox.showerror("输入错误", "请填写 Community 字符串。", parent=self)
            return

        self.tree.delete(*self.tree.get_children())
        self._discovered.clear()
        self._selected.clear()
        self._total = len(addresses)
        self._done = 0
        self.progress_var.set(f"正在扫描 0/{self._total}...")
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self._result_queue = queue.Queue()
        self._stop_event = threading.Event()
        threading.Thread(
            target=snmp_check.discover_snmp_devices,
            kwargs={
                "addresses": addresses,
                "port": port,
                "version": self.version_var.get(),
                "community": community,
                "timeout": 1.0,
                "stop_event": self._stop_event,
                "result_queue": self._result_queue,
            },
            name="snmp-discovery-ui",
            daemon=True,
        ).start()
        self.after(120, self._poll)

    def _stop_scan(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        self.stop_btn.configure(state=tk.DISABLED)

    def _poll(self) -> None:
        try:
            while True:
                item = self._result_queue.get_nowait()
                if item.get("done"):
                    self.start_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self.progress_var.set(f"扫描完成：{self._done}/{self._total}，发现 {len(self._discovered)} 台 SNMP 设备")
                    return
                self._done += 1
                device = item.get("device")
                if device:
                    addr = device["address"]
                    self._discovered[addr] = device
                    already = addr in self._existing_addresses
                    self._selected[addr] = not already
                    role_label = DEVICE_ROLE_LABELS.get(device["device_role"], "其他")
                    self.tree.insert(
                        "", tk.END, iid=addr,
                        values=(
                            "已监控" if already else "☑",
                            addr, device["sys_name"], role_label, device["uptime"],
                            f"{device['latency']:.1f}", device["sys_descr"][:120],
                        ),
                        tags=("existing",) if already else (),
                    )
                self.progress_var.set(f"正在扫描 {self._done}/{self._total}，已发现 {len(self._discovered)} 台...")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(120, self._poll)

    def _toggle_row(self, event: tk.Event[tk.Misc]) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid or iid in self._existing_addresses:
            return
        self._selected[iid] = not self._selected.get(iid, False)
        vals = list(self.tree.item(iid, "values"))
        vals[0] = "☑" if self._selected[iid] else "☐"
        self.tree.item(iid, values=vals)

    def _import_selected(self) -> None:
        picked = [self._discovered[addr] for addr, ok in self._selected.items() if ok and addr in self._discovered]
        if not picked:
            messagebox.showinfo("提示", "请先勾选至少一台待导入的设备。", parent=self)
            return
        group = self.group_var.get().strip() or DEFAULT_GROUP
        community = self.community_var.get().strip() or "public"
        version = self.version_var.get()
        port = int(self.port_var.get() or 161)
        used_names = set(self._existing_names)
        new_targets: list[dict[str, Any]] = []
        for dev in picked:
            base_name = str(dev.get("sys_name") or dev["address"]).strip()
            name = base_name
            if name in used_names:
                name = f"{base_name} ({dev['address']})"
            used_names.add(name)
            new_targets.append(
                normalize_target({
                    "name": name,
                    "address": dev["address"],
                    "check_type": "snmp",
                    "device_role": dev["device_role"],
                    "group": group,
                    "interval": 60,
                    "alert_enabled": True,
                    "enabled": True,
                    "snmp_port": port,
                    "snmp_version": version,
                    "snmp_community": community,
                })
            )
        self._on_import(new_targets)
        messagebox.showinfo("导入完成", f"已将 {len(new_targets)} 台 SNMP 设备加入监控列表。", parent=self)
        self._on_close()

    def _on_close(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        self.destroy()
