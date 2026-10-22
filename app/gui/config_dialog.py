"""Target and system-settings configuration window."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from app.core.desktop_alert import as_bool

DEVICE_ROLE_LABELS = {"switch": "交换机", "server": "服务器", "other": "其他"}
DEVICE_ROLE_VALUES = {label: value for value, label in DEVICE_ROLE_LABELS.items()}


class ConfigDialog(tk.Toplevel):
    """Edit target definitions and alert settings before saving JSON files."""

    def __init__(
        self,
        parent: tk.Misc,
        targets: list[dict[str, Any]],
        settings: dict[str, Any],
        on_save: Callable[[list[dict[str, Any]], dict[str, Any]], None],
    ) -> None:
        super().__init__(parent)
        self.title("配置监控目标")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self.targets = [item.copy() for item in targets]
        self.settings = settings.copy()
        self.on_save = on_save
        self._build_widgets()
        self._refresh_tree()

    def _build_widgets(self) -> None:
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        targets_box = ttk.LabelFrame(body, text="监控目标", padding=8)
        targets_box.grid(row=0, column=0, sticky="nsew")
        targets_box.columnconfigure(0, weight=1)
        targets_box.rowconfigure(0, weight=1)
        columns = ("name", "address", "check_type", "device_role", "interval", "alert_enabled")
        self.tree = ttk.Treeview(targets_box, columns=columns, show="headings", height=9)
        for key, title, width in (
            ("name", "名称", 150), ("address", "目标地址", 210), ("check_type", "类型", 70),
            ("device_role", "设备类型", 80), ("interval", "间隔(秒)", 90), ("alert_enabled", "启用告警", 90),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=tk.CENTER if key != "address" else tk.W)
        self.tree.grid(row=0, column=0, sticky="nsew")
        buttons = ttk.Frame(targets_box)
        buttons.grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Button(buttons, text="添加", command=lambda: self._edit_target()).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(buttons, text="编辑", command=self._edit_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="删除", command=self._delete_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="批量导入", command=self._batch_import).pack(side=tk.LEFT, padx=(12, 0))

        settings_box = ttk.LabelFrame(body, text="系统设置", padding=8)
        settings_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        settings_box.columnconfigure(1, weight=1)
        self.webhook_var = tk.StringVar(value=str(self.settings.get("wechat_webhook", "")))
        self.threshold_var = tk.StringVar(value=str(self.settings.get("failure_threshold", 3)))
        self.cooldown_var = tk.StringVar(value=str(self.settings.get("cooldown_minutes", 5)))
        self.recovery_var = tk.BooleanVar(value=bool(self.settings.get("send_recovery_notification", True)))
        self.desktop_alert_var = tk.BooleanVar(value=as_bool(self.settings.get("desktop_alert_enabled", True)))
        self.desktop_sound_var = tk.BooleanVar(value=as_bool(self.settings.get("desktop_alert_sound_enabled", True)))
        self.desktop_flash_var = tk.BooleanVar(value=as_bool(self.settings.get("desktop_alert_flash_enabled", True)))
        self.log_level_var = tk.StringVar(value=str(self.settings.get("log_level", "INFO")).upper())
        self.ping_count_var = tk.StringVar(value=str(self.settings.get("ping_count", 1)))
        self.ping_timeout_var = tk.StringVar(value=str(self.settings.get("ping_timeout_ms", 2000)))
        self._webhook_visible_var = tk.BooleanVar(value=False)
        self.daily_report_var = tk.BooleanVar(value=as_bool(self.settings.get("daily_report_enabled", False)))
        self.daily_report_time_var = tk.StringVar(value=str(self.settings.get("daily_report_time", "18:00")))
        self.slow_threshold_var = tk.StringVar(value=str(self.settings.get("slow_warning_threshold_ms", 0)))
        self.correlation_threshold_var = tk.StringVar(value=str(self.settings.get("correlation_alert_threshold", 3)))
        self._webhook_entry = self._field(settings_box, 0, "企业微信 Webhook", self.webhook_var, show="*")
        ttk.Checkbutton(
            settings_box, text="显示", variable=self._webhook_visible_var, command=self._toggle_webhook_visibility,
        ).grid(row=0, column=2, sticky=tk.W, padx=(6, 0), pady=3)
        self._field(settings_box, 1, "连续失败阈值", self.threshold_var)
        self._field(settings_box, 2, "告警冷却(分钟)", self.cooldown_var)
        ttk.Checkbutton(settings_box, text="恢复正常时发送通知", variable=self.recovery_var).grid(row=3, column=1, sticky=tk.W, pady=3)
        ttk.Checkbutton(settings_box, text="启用本机声光告警", variable=self.desktop_alert_var).grid(row=4, column=1, sticky=tk.W, pady=3)
        ttk.Checkbutton(settings_box, text="播放系统告警声音", variable=self.desktop_sound_var).grid(row=5, column=1, sticky=tk.W, pady=3)
        ttk.Checkbutton(settings_box, text="窗口闪烁和系统托盘提醒", variable=self.desktop_flash_var).grid(row=6, column=1, sticky=tk.W, pady=3)
        ttk.Label(settings_box, text="日志级别").grid(row=7, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Combobox(settings_box, textvariable=self.log_level_var, values=("DEBUG", "INFO", "WARNING", "ERROR"), state="readonly", width=12).grid(row=7, column=1, sticky=tk.W, pady=3)
        self._field(settings_box, 8, "Ping 包数量(1-10)", self.ping_count_var)
        self._field(settings_box, 9, "Ping 超时(毫秒,100-10000)", self.ping_timeout_var)

        advanced_box = ttk.LabelFrame(settings_box, text="高级告警设置", padding=8)
        advanced_box.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        advanced_box.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            advanced_box, text="每日定时发送巡检报告到企业微信", variable=self.daily_report_var,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=3)
        self._field(advanced_box, 1, "报告发送时间(HH:MM)", self.daily_report_time_var)
        self._field(advanced_box, 2, "慢响应阈值(毫秒,0=关闭)", self.slow_threshold_var)
        self._field(advanced_box, 3, "关联告警阈值(同时失败数,0=关闭)", self.correlation_threshold_var)

        actions = ttk.Frame(body)
        actions.grid(row=2, column=0, sticky=tk.E, pady=(12, 0))
        ttk.Button(actions, text="取消", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(actions, text="保存", command=self._save).pack(side=tk.RIGHT, padx=(0, 8))

    @staticmethod
    def _field(parent: tk.Misc, row: int, label: str, variable: tk.StringVar, show: str | None = None) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        entry = ttk.Entry(parent, textvariable=variable, width=55, show=show or "")
        entry.grid(row=row, column=1, sticky=tk.EW, pady=3)
        return entry

    def _batch_import(self) -> None:
        """Open a textarea dialog and parse addresses into new targets."""
        dialog = tk.Toplevel(self)
        dialog.title("批量导入监控目标")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(
            frame,
            text="每行一条：直接填 IP/地址（自动命名），或 名称,地址,类型，\n支持范围如 192.168.1.1-254",
            foreground="#555555",
        ).grid(row=0, column=0, sticky=tk.W, pady=(0, 6))
        text = tk.Text(frame, width=60, height=14, wrap=tk.NONE)
        text.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        sb.grid(row=1, column=1, sticky="ns")
        text.configure(yscrollcommand=sb.set)
        footer = ttk.Frame(frame)
        footer.grid(row=2, column=0, columnspan=2, sticky=tk.E, pady=(10, 0))
        ttk.Button(footer, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(footer, text="导入", command=lambda: self._do_batch_import(text.get("1.0", tk.END), dialog)).pack(side=tk.RIGHT, padx=(0, 8))

    def _do_batch_import(self, raw: str, dialog: tk.Toplevel) -> None:
        added, skipped = 0, []
        existing_names = {t["name"] for t in self.targets}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                name, address, check_type = parts[0], parts[1], parts[2].lower()
                entries = [(name, address)]
            elif len(parts) == 2:
                name, address = parts[0], parts[1]
                check_type = "ping"
                entries = [(name, address)]
            else:
                address = parts[0]
                check_type = "ping"
                entries = list(self._expand_range(address))
            for entry_name, entry_addr in entries:
                if entry_name in existing_names:
                    skipped.append(entry_name)
                    continue
                self.targets.append({
                    "name": entry_name, "address": entry_addr, "check_type": check_type,
                    "interval": 60, "alert_enabled": True,
                    "device_role": "other", "snmp_version": "v2c", "snmp_port": 161,
                    "snmp_community": "public", "http_expected_text": "", "http_timeout_seconds": 5,
                    "http_verify_tls": True,
                })
                existing_names.add(entry_name)
                added += 1
        self._refresh_tree()
        dialog.destroy()
        msg = f"已导入 {added} 条目标。"
        if skipped:
            msg += f"\n以下名称重复已跳过：{', '.join(skipped[:10])}"
            if len(skipped) > 10:
                msg += f" 等 {len(skipped)} 条"
        messagebox.showinfo("批量导入完成", msg, parent=self)

    @staticmethod
    def _expand_range(address: str) -> list[tuple[str, str]]:
        """Expand '192.168.1.1-254' into (name, addr) pairs; passthrough otherwise."""
        if "-" in address:
            parts = address.rsplit(".", 1)
            if len(parts) == 2:
                prefix, last = parts
                if "-" in last:
                    try:
                        start_s, end_s = last.split("-", 1)
                        start, end = int(start_s), int(end_s)
                        if 0 <= start <= end <= 255:
                            return [(f"{prefix}.{i}", f"{prefix}.{i}") for i in range(start, end + 1)]
                    except ValueError:
                        pass
        return [(address, address)]

    def _toggle_webhook_visibility(self) -> None:
        self._webhook_entry.configure(show="" if self._webhook_visible_var.get() else "*")

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, target in enumerate(self.targets):
            role_label = DEVICE_ROLE_LABELS.get(str(target.get("device_role", "other")), DEVICE_ROLE_LABELS["other"])
            self.tree.insert("", tk.END, iid=str(index), values=(
                target.get("name", ""), target.get("address", ""), target.get("check_type", "ping"), role_label,
                target.get("interval", 60), "是" if target.get("alert_enabled", True) else "否",
            ))

    def _edit_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个监控目标。", parent=self)
            return
        self._edit_target(int(selection[0]))

    def _edit_target(self, index: int | None = None) -> None:
        existing = self.targets[index] if index is not None else {}
        dialog = tk.Toplevel(self)
        dialog.title("编辑监控目标" if index is not None else "添加监控目标")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        name = tk.StringVar(value=str(existing.get("name", "")))
        address = tk.StringVar(value=str(existing.get("address", "")))
        check_type = tk.StringVar(value=str(existing.get("check_type", "ping")))
        role_label = DEVICE_ROLE_LABELS.get(str(existing.get("device_role", "other")), DEVICE_ROLE_LABELS["other"])
        device_role = tk.StringVar(value=role_label)
        interval = tk.StringVar(value=str(existing.get("interval", 60)))
        alert_enabled = tk.BooleanVar(value=bool(existing.get("alert_enabled", True)))
        snmp_version = tk.StringVar(value=str(existing.get("snmp_version", "v2c")))
        snmp_port = tk.StringVar(value=str(existing.get("snmp_port", 161)))
        snmp_community = tk.StringVar(value=str(existing.get("snmp_community", "public")))
        snmp_community_visible = tk.BooleanVar(value=False)
        http_expected_text = tk.StringVar(value=str(existing.get("http_expected_text", "")))
        http_timeout_seconds = tk.StringVar(value=str(existing.get("http_timeout_seconds", 5)))
        http_verify_tls = tk.BooleanVar(value=bool(existing.get("http_verify_tls", True)))

        self._field(frame, 0, "名称", name)
        self._field(frame, 1, "目标地址", address)
        ttk.Label(frame, text="检测类型").grid(row=2, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Combobox(frame, textvariable=check_type, values=("ping", "tcp", "http", "snmp"), state="readonly", width=15).grid(row=2, column=1, sticky=tk.W, pady=3)
        ttk.Label(frame, text="设备类型").grid(row=3, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Combobox(frame, textvariable=device_role, values=tuple(DEVICE_ROLE_LABELS.values()), state="readonly", width=15).grid(row=3, column=1, sticky=tk.W, pady=3)
        self._field(frame, 4, "检测间隔(秒)", interval)
        ttk.Checkbutton(frame, text="启用告警（企业微信 / 本机）", variable=alert_enabled).grid(row=5, column=1, sticky=tk.W, pady=3)

        snmp_frame = ttk.LabelFrame(frame, text="SNMP 参数", padding=8)
        snmp_frame.columnconfigure(1, weight=1)
        ttk.Label(snmp_frame, text="版本").grid(row=0, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Combobox(snmp_frame, textvariable=snmp_version, values=("v1", "v2c"), state="readonly", width=12).grid(row=0, column=1, sticky=tk.W, pady=3)
        ttk.Label(snmp_frame, text="端口").grid(row=1, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Entry(snmp_frame, textvariable=snmp_port, width=12).grid(row=1, column=1, sticky=tk.W, pady=3)
        community_entry = self._field(snmp_frame, 2, "Community 字符串", snmp_community, show="*")
        ttk.Checkbutton(
            snmp_frame, text="显示", variable=snmp_community_visible,
            command=lambda: community_entry.configure(show="" if snmp_community_visible.get() else "*"),
        ).grid(row=2, column=2, sticky=tk.W, padx=(6, 0), pady=3)

        http_frame = ttk.LabelFrame(frame, text="网页巡检参数", padding=8)
        http_frame.columnconfigure(1, weight=1)
        self._field(http_frame, 0, "页面应包含的文字(可选)", http_expected_text)
        self._field(http_frame, 1, "超时(秒,1-60)", http_timeout_seconds)
        ttk.Checkbutton(
            http_frame,
            text="验证 HTTPS 证书（自签名安全设备可取消勾选）",
            variable=http_verify_tls,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=3)

        hint = ttk.Label(frame, foreground="#666666")
        hint.grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        def sync_visibility(*_args: object) -> None:
            if check_type.get() == "snmp":
                snmp_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))
                http_frame.grid_remove()
                hint.configure(text="SNMP 请在“目标地址”填设备管理 IP，端口/版本/Community 在上方单独设置")
            elif check_type.get() == "http":
                snmp_frame.grid_remove()
                http_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))
                hint.configure(text="网页巡检会请求 URL；可用页面标题、登录页标识等文字确认访问到正确页面")
            else:
                snmp_frame.grid_remove()
                http_frame.grid_remove()
                hint.configure(text="TCP 请填写 主机:端口；HTTP 可填写完整 URL")

        check_type.trace_add("write", sync_visibility)
        sync_visibility()

        def commit() -> None:
            try:
                value = {
                    "name": name.get().strip(), "address": address.get().strip(), "check_type": check_type.get(),
                    "device_role": DEVICE_ROLE_VALUES.get(device_role.get(), "other"),
                    "interval": int(interval.get()), "alert_enabled": alert_enabled.get(),
                    "snmp_version": snmp_version.get(), "snmp_community": snmp_community.get().strip(),
                    "http_expected_text": http_expected_text.get().strip(),
                    "http_verify_tls": http_verify_tls.get(),
                }
                if not value["name"] or not value["address"]:
                    raise ValueError("名称和目标地址不能为空。")
                if value["interval"] < 1:
                    raise ValueError("检测间隔必须至少为 1 秒。")
                if any(item["name"] == value["name"] for position, item in enumerate(self.targets) if position != index):
                    raise ValueError("监控目标名称不能重复。")
                if value["check_type"] == "snmp":
                    try:
                        port_value = int(snmp_port.get())
                    except ValueError:
                        raise ValueError("SNMP 端口必须是数字。") from None
                    if not 1 <= port_value <= 65535:
                        raise ValueError("SNMP 端口必须在 1-65535 范围内。")
                    if not value["snmp_community"]:
                        raise ValueError("SNMP Community 字符串不能为空。")
                else:
                    try:
                        port_value = int(snmp_port.get())
                    except ValueError:
                        port_value = 161
                value["snmp_port"] = port_value
                if value["check_type"] == "http":
                    try:
                        timeout_value = float(http_timeout_seconds.get())
                    except ValueError:
                        raise ValueError("HTTP 超时必须是数字。") from None
                    if not 1 <= timeout_value <= 60:
                        raise ValueError("HTTP 超时必须在 1-60 秒范围内。")
                    value["http_timeout_seconds"] = timeout_value
                else:
                    value["http_timeout_seconds"] = 5
            except ValueError as error:
                messagebox.showerror("输入错误", str(error), parent=dialog)
                return
            if index is None:
                self.targets.append(value)
            else:
                self.targets[index] = value
            self._refresh_tree()
            dialog.destroy()

        footer = ttk.Frame(frame)
        footer.grid(row=8, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        ttk.Button(footer, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(footer, text="确定", command=commit).pack(side=tk.RIGHT, padx=(0, 8))

    def _delete_selected(self) -> None:
        selection = self.tree.selection()
        if selection and messagebox.askyesno("确认删除", "确定删除所选监控目标吗？", parent=self):
            del self.targets[int(selection[0])]
            self._refresh_tree()

    def _save(self) -> None:
        try:
            ping_count = int(self.ping_count_var.get())
            ping_timeout_ms = int(self.ping_timeout_var.get())
            slow_threshold = int(self.slow_threshold_var.get())
            correlation_threshold = int(self.correlation_threshold_var.get())
        except ValueError:
            messagebox.showerror("输入错误", "数值字段必须是整数。", parent=self)
            return
        if not 1 <= ping_count <= 10:
            messagebox.showerror("输入错误", "Ping 包数量必须在 1-10 范围内。", parent=self)
            return
        if not 100 <= ping_timeout_ms <= 10000:
            messagebox.showerror("输入错误", "Ping 超时必须在 100-10000 毫秒范围内。", parent=self)
            return
        if slow_threshold < 0:
            messagebox.showerror("输入错误", "慢响应阈值不能为负数。", parent=self)
            return
        if correlation_threshold < 0:
            messagebox.showerror("输入错误", "关联告警阈值不能为负数。", parent=self)
            return
        report_time = self.daily_report_time_var.get().strip()
        try:
            parts = report_time.split(":")
            if len(parts) != 2 or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "报告时间格式应为 HH:MM。", parent=self)
            return
        try:
            self.settings.update({
                "wechat_webhook": self.webhook_var.get().strip(),
                "failure_threshold": max(1, int(self.threshold_var.get())),
                "cooldown_minutes": max(0, int(self.cooldown_var.get())),
                "send_recovery_notification": self.recovery_var.get(),
                "desktop_alert_enabled": self.desktop_alert_var.get(),
                "desktop_alert_sound_enabled": self.desktop_sound_var.get(),
                "desktop_alert_flash_enabled": self.desktop_flash_var.get(),
                "log_level": self.log_level_var.get(),
                "ping_count": ping_count,
                "ping_timeout_ms": ping_timeout_ms,
                "daily_report_enabled": self.daily_report_var.get(),
                "daily_report_time": self.daily_report_time_var.get().strip(),
                "slow_warning_threshold_ms": max(0, int(self.slow_threshold_var.get())),
                "correlation_alert_threshold": max(0, int(self.correlation_threshold_var.get())),
            })
        except ValueError:
            messagebox.showerror("输入错误", "失败阈值和冷却时间必须是整数。", parent=self)
            return
        self.on_save(self.targets, self.settings)
        self.destroy()
