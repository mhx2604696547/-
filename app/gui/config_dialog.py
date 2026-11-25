"""Target editing, batch import, and tabbed system-settings dialogs."""
from __future__ import annotations

import logging
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable
from urllib.parse import urlparse

from app.core import http_check, ping_check, snmp_check, tcp_check
from app.core.desktop_alert import as_bool
from app.integrations.wechat import WeChatNotifier
from app.utils.autostart import set_autostart

DEVICE_ROLE_LABELS = {"switch": "交换机", "server": "服务器", "other": "其他"}
DEVICE_ROLE_VALUES = {label: value for value, label in DEVICE_ROLE_LABELS.items()}
LOG_LEVEL_LABELS = {"DEBUG": "调试", "INFO": "信息", "WARNING": "警告", "ERROR": "错误"}
LOG_LEVEL_VALUES = {label: value for value, label in LOG_LEVEL_LABELS.items()}
STARTUP_VIEW_LABELS = {"wall": "监控大屏", "workbench": "个人运维工作台"}
STARTUP_VIEW_VALUES = {label: value for value, label in STARTUP_VIEW_LABELS.items()}
SUPPORTED_CHECK_TYPES = ("ping", "tcp", "http", "snmp")
DEFAULT_GROUP = "默认"


def extract_host(address: str) -> str:
    """Extract a bare hostname or IP from a URL, host:port, or [IPv6]:port."""
    text = str(address or "").strip()
    if not text:
        return ""
    if "://" in text:
        parsed = urlparse(text)
        return parsed.hostname or text
    if text.startswith("[") and "]" in text:
        return text[1 : text.index("]")]
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        if port.isdigit():
            return host.strip()
    return text


def normalize_target(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a target dict with all optional fields populated with safe defaults."""
    target = dict(raw)
    target.setdefault("name", "")
    target.setdefault("address", "")
    target["check_type"] = str(target.get("check_type", "ping")).lower() or "ping"
    target.setdefault("device_role", "other")
    group = str(target.get("group") or "").strip()
    target["group"] = group or DEFAULT_GROUP
    target.setdefault("interval", 60)
    target["alert_enabled"] = as_bool(target.get("alert_enabled", True))
    target["enabled"] = as_bool(target.get("enabled", True))
    target.setdefault("snmp_port", 161)
    target.setdefault("snmp_version", "v2c")
    target.setdefault("snmp_community", "public")
    target.setdefault("http_expected_text", "")
    target.setdefault("http_timeout_seconds", 5)
    target["http_verify_tls"] = as_bool(target.get("http_verify_tls", True))
    target["notes"] = str(target.get("notes") or "").strip()
    return target


def expand_ip_range(address: str) -> list[tuple[str, str]]:
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


def parse_batch_targets(
    raw: str,
    existing_names: set[str],
    *,
    default_group: str = DEFAULT_GROUP,
    default_check_type: str = "ping",
    default_role: str = "other",
    default_snmp_community: str = "public",
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Parse multiline batch target input into (created, skipped_duplicates, errors)."""
    created: list[dict[str, Any]] = []
    skipped: list[str] = []
    errors: list[str] = []
    seen = set(existing_names)
    group = default_group.strip() or DEFAULT_GROUP
    fallback_type = default_check_type.lower() if default_check_type.lower() in SUPPORTED_CHECK_TYPES else "ping"
    community = default_snmp_community.strip() or "public"

    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            name, address, check_type = parts[0], parts[1], parts[2].lower() or fallback_type
            entries = [(name, address)]
        elif len(parts) == 2:
            name, address = parts[0], parts[1]
            check_type = fallback_type
            entries = [(name, address)]
        else:
            address = parts[0]
            check_type = fallback_type
            entries = expand_ip_range(address)

        if check_type not in SUPPORTED_CHECK_TYPES:
            errors.append(f"第 {line_no} 行：不支持的检测类型 '{check_type}'")
            continue

        for entry_name, entry_addr in entries:
            if not entry_name or not entry_addr:
                errors.append(f"第 {line_no} 行：名称或地址为空")
                continue
            if entry_name in seen:
                skipped.append(entry_name)
                continue
            created.append(
                normalize_target({
                    "name": entry_name,
                    "address": entry_addr,
                    "check_type": check_type,
                    "device_role": default_role,
                    "group": group,
                    "interval": 60,
                    "alert_enabled": True,
                    "enabled": True,
                    "snmp_port": 161,
                    "snmp_version": "v2c",
                    "snmp_community": community,
                    "http_expected_text": "",
                    "http_timeout_seconds": 5,
                    "http_verify_tls": True,
                })
            )
            seen.add(entry_name)
    return created, skipped, errors


def probe_target_once(target: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one immediate connectivity test for a target definition."""
    cfg = settings or {}
    check_type = str(target.get("check_type", "ping")).lower()
    address = str(target.get("address", "")).strip()
    if not address:
        return {"status": "down", "latency": None, "error_msg": "请先填写目标地址", "detail": ""}
    if check_type == "ping":
        return ping_check.check(
            address,
            count=max(1, min(3, int(cfg.get("ping_count", 1)))),
            timeout_ms=int(cfg.get("ping_timeout_ms", 2000)),
        )
    if check_type == "tcp":
        return tcp_check.check(address)
    if check_type == "http":
        return http_check.check(
            address,
            timeout_seconds=float(target.get("http_timeout_seconds", 5)),
            expected_text=str(target.get("http_expected_text", "")),
            verify_tls=as_bool(target.get("http_verify_tls", True)),
        )
    if check_type == "snmp":
        return snmp_check.check(
            address,
            port=int(target.get("snmp_port", 161)),
            version=str(target.get("snmp_version", "v2c")),
            community=str(target.get("snmp_community", "public")),
            device_role=str(target.get("device_role", "other")),
        )
    return {"status": "down", "latency": None, "error_msg": f"不支持的检测类型：{check_type}", "detail": ""}


def _extract_snmp_sys_name(detail: str) -> str:
    match = re.search(r"设备名：([^，,]+)", str(detail or ""))
    return match.group(1).strip() if match else ""


class TargetEditDialog(tk.Toplevel):
    """Streamlined add/edit dialog for a single target, with inline test-connection."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        existing: dict[str, Any] | None = None,
        existing_names: set[str] | None = None,
        available_groups: list[str] | None = None,
        settings: dict[str, Any] | None = None,
        on_commit: Callable[[dict[str, Any]], None],
        title: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._existing = normalize_target(existing) if existing else normalize_target({})
        self._is_edit = existing is not None
        self._existing_names = set(existing_names or set())
        self._settings = settings or {}
        self._on_commit = on_commit
        self._testing = False
        self._groups = sorted({DEFAULT_GROUP, *(g.strip() for g in (available_groups or []) if g.strip())})

        self.title(title or ("编辑监控目标" if self._is_edit else "添加监控目标"))
        self.resizable(True, False)
        self.transient(parent)
        self.grab_set()
        self.minsize(520, 420)
        self._build_widgets()

    def _build_widgets(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)

        self.name_var = tk.StringVar(value=str(self._existing.get("name", "")))
        self.address_var = tk.StringVar(value=str(self._existing.get("address", "")))
        self.check_type_var = tk.StringVar(value=str(self._existing.get("check_type", "ping")))
        self.device_role_var = tk.StringVar(
            value=DEVICE_ROLE_LABELS.get(str(self._existing.get("device_role", "other")), DEVICE_ROLE_LABELS["other"])
        )
        self.group_var = tk.StringVar(value=str(self._existing.get("group", DEFAULT_GROUP)))
        self.interval_var = tk.StringVar(value=str(self._existing.get("interval", 60)))
        self.enabled_var = tk.BooleanVar(value=as_bool(self._existing.get("enabled", True)))
        self.alert_enabled_var = tk.BooleanVar(value=as_bool(self._existing.get("alert_enabled", True)))

        self.snmp_version_var = tk.StringVar(value=str(self._existing.get("snmp_version", "v2c")))
        self.snmp_port_var = tk.StringVar(value=str(self._existing.get("snmp_port", 161)))
        self.snmp_community_var = tk.StringVar(value=str(self._existing.get("snmp_community", "public")))
        self.snmp_community_visible = tk.BooleanVar(value=False)

        self.http_expected_text_var = tk.StringVar(value=str(self._existing.get("http_expected_text", "")))
        self.http_timeout_var = tk.StringVar(value=str(self._existing.get("http_timeout_seconds", 5)))
        self.http_verify_tls_var = tk.BooleanVar(value=as_bool(self._existing.get("http_verify_tls", True)))
        self.notes_var = tk.StringVar(value=str(self._existing.get("notes", "")))

        self._field(frame, 0, "名称", self.name_var)
        self._field(frame, 1, "目标地址", self.address_var)

        row2 = ttk.Frame(frame)
        row2.grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="检测类型").grid(row=2, column=0, sticky=tk.W, padx=(0, 12), pady=4)
        ttk.Combobox(
            row2, textvariable=self.check_type_var, values=SUPPORTED_CHECK_TYPES, state="readonly", width=12,
        ).pack(side=tk.LEFT)
        ttk.Label(row2, text="设备类型").pack(side=tk.LEFT, padx=(16, 8))
        ttk.Combobox(
            row2, textvariable=self.device_role_var, values=tuple(DEVICE_ROLE_LABELS.values()), state="readonly", width=12,
        ).pack(side=tk.LEFT)

        row3 = ttk.Frame(frame)
        row3.grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="所属分组").grid(row=3, column=0, sticky=tk.W, padx=(0, 12), pady=4)
        ttk.Combobox(row3, textvariable=self.group_var, values=self._groups, width=14).pack(side=tk.LEFT)
        ttk.Label(row3, text="间隔(秒)").pack(side=tk.LEFT, padx=(16, 8))
        ttk.Entry(row3, textvariable=self.interval_var, width=8).pack(side=tk.LEFT)

        switches = ttk.Frame(frame)
        switches.grid(row=4, column=1, sticky="w", pady=(6, 4))
        ttk.Checkbutton(switches, text="启用监控", variable=self.enabled_var).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Checkbutton(switches, text="启用告警（企业微信 / 本机）", variable=self.alert_enabled_var).pack(side=tk.LEFT)
        self._field(frame, 5, "运维备注(可选)", self.notes_var)

        self.snmp_frame = ttk.LabelFrame(frame, text="SNMP 参数", padding=10)
        self.snmp_frame.columnconfigure(1, weight=1)
        snmp_top = ttk.Frame(self.snmp_frame)
        snmp_top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=3)
        ttk.Label(snmp_top, text="版本").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Combobox(snmp_top, textvariable=self.snmp_version_var, values=("v1", "v2c"), state="readonly", width=8).pack(side=tk.LEFT)
        ttk.Label(snmp_top, text="端口").pack(side=tk.LEFT, padx=(16, 8))
        ttk.Entry(snmp_top, textvariable=self.snmp_port_var, width=8).pack(side=tk.LEFT)
        community_entry = self._field(self.snmp_frame, 1, "Community", self.snmp_community_var, show="*")
        ttk.Checkbutton(
            self.snmp_frame, text="显示", variable=self.snmp_community_visible,
            command=lambda: community_entry.configure(show="" if self.snmp_community_visible.get() else "*"),
        ).grid(row=1, column=2, sticky=tk.W, padx=(6, 0), pady=3)

        self.http_frame = ttk.LabelFrame(frame, text="网页巡检参数", padding=10)
        self.http_frame.columnconfigure(1, weight=1)
        self._field(self.http_frame, 0, "页面应包含文字(可选)", self.http_expected_text_var)
        self._field(self.http_frame, 1, "超时(秒,1-60)", self.http_timeout_var)
        ttk.Checkbutton(
            self.http_frame, text="验证 HTTPS 证书（自签名设备可取消勾选）", variable=self.http_verify_tls_var,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=3)

        self.hint_label = ttk.Label(frame, foreground="#68778C")
        self.hint_label.grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        self.test_result_var = tk.StringVar(value="")
        self.test_result_label = tk.Label(
            frame, textvariable=self.test_result_var, anchor="w", justify=tk.LEFT,
            wraplength=480, background="#EEF3F8", foreground="#172033", padx=10, pady=6,
        )

        self.check_type_var.trace_add("write", self._sync_visibility)
        self._sync_visibility()

        footer = ttk.Frame(frame)
        footer.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.test_button = ttk.Button(footer, text="测试连接", command=self._run_test, style="Secondary.TButton")
        self.test_button.pack(side=tk.LEFT)
        ttk.Button(footer, text="取消", command=self.destroy, style="Secondary.TButton").pack(side=tk.RIGHT)
        ttk.Button(footer, text="确定", command=self._commit, style="Primary.TButton").pack(side=tk.RIGHT, padx=(0, 8))

    @staticmethod
    def _field(parent: tk.Misc, row: int, label: str, variable: tk.StringVar, show: str | None = None) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 12), pady=4)
        entry = ttk.Entry(parent, textvariable=variable, width=42, show=show or "")
        entry.grid(row=row, column=1, sticky=tk.EW, pady=4)
        return entry

    def _sync_visibility(self, *_args: object) -> None:
        ctype = self.check_type_var.get()
        if ctype == "snmp":
            self.snmp_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            self.http_frame.grid_remove()
            self.hint_label.configure(text="SNMP 填写设备管理 IP；点“测试连接”可即时验证并自动读取设备名")
        elif ctype == "http":
            self.snmp_frame.grid_remove()
            self.http_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            self.hint_label.configure(text="填写完整 URL，如 https://192.168.1.254/login")
        elif ctype == "tcp":
            self.snmp_frame.grid_remove()
            self.http_frame.grid_remove()
            self.hint_label.configure(text="TCP 格式：主机:端口（IPv6 使用 [地址]:端口）")
        else:
            self.snmp_frame.grid_remove()
            self.http_frame.grid_remove()
            self.hint_label.configure(text="Ping 填写 IP 或域名，如 192.168.1.1")

    def _build_target_dict(self, *, require_name: bool = True) -> dict[str, Any]:
        value = {
            "name": self.name_var.get().strip(),
            "address": self.address_var.get().strip(),
            "check_type": self.check_type_var.get().strip().lower(),
            "device_role": DEVICE_ROLE_VALUES.get(self.device_role_var.get(), "other"),
            "group": self.group_var.get().strip() or DEFAULT_GROUP,
            "interval": int(self.interval_var.get()),
            "enabled": self.enabled_var.get(),
            "alert_enabled": self.alert_enabled_var.get(),
            "snmp_version": self.snmp_version_var.get(),
            "snmp_community": self.snmp_community_var.get().strip(),
            "http_expected_text": self.http_expected_text_var.get().strip(),
            "http_verify_tls": self.http_verify_tls_var.get(),
            "notes": self.notes_var.get().strip(),
        }
        if require_name and not value["name"]:
            raise ValueError("名称不能为空。")
        if not value["address"]:
            raise ValueError("目标地址不能为空。")
        if value["interval"] < 1:
            raise ValueError("检测间隔必须至少为 1 秒。")
        if require_name and value["name"] in self._existing_names:
            raise ValueError("监控目标名称不能重复。")
        if value["check_type"] == "snmp":
            try:
                port_value = int(self.snmp_port_var.get())
            except ValueError:
                raise ValueError("SNMP 端口必须是数字。") from None
            if not 1 <= port_value <= 65535:
                raise ValueError("SNMP 端口必须在 1-65535 范围内。")
            if not value["snmp_community"]:
                raise ValueError("SNMP Community 字符串不能为空。")
            value["snmp_port"] = port_value
        else:
            try:
                value["snmp_port"] = int(self.snmp_port_var.get())
            except ValueError:
                value["snmp_port"] = 161
        if value["check_type"] == "http":
            try:
                timeout_value = float(self.http_timeout_var.get())
            except ValueError:
                raise ValueError("HTTP 超时必须是数字。") from None
            if not 1 <= timeout_value <= 60:
                raise ValueError("HTTP 超时必须在 1-60 秒范围内。")
            value["http_timeout_seconds"] = timeout_value
        else:
            value["http_timeout_seconds"] = 5
        return value

    def _run_test(self) -> None:
        if self._testing:
            return
        try:
            probe_target = self._build_target_dict(require_name=False)
        except ValueError as error:
            messagebox.showerror("输入错误", str(error), parent=self)
            return
        self._testing = True
        self.test_button.configure(state=tk.DISABLED, text="测试中...")
        self.test_result_var.set("正在探测目标连通性，请稍候...")
        self.test_result_label.configure(background="#EEF3F8", foreground="#172033")
        self.test_result_label.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        def worker() -> None:
            result = probe_target_once(probe_target, self._settings)
            try:
                self.after(0, lambda: self._on_test_done(probe_target, result))
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="target-probe", daemon=True).start()

    def _on_test_done(self, probe_target: dict[str, Any], result: dict[str, Any]) -> None:
        self._testing = False
        try:
            self.test_button.configure(state=tk.NORMAL, text="测试连接")
        except tk.TclError:
            return
        if result.get("status") == "up":
            latency = result.get("latency")
            lat_text = f"{float(latency):.1f} ms" if latency is not None else "-"
            detail = str(result.get("detail") or "")
            summary = f"✓ 连接成功（延迟 {lat_text}）" + (f"\n{detail}" if detail else "")
            self.test_result_var.set(summary)
            self.test_result_label.configure(background="#E7F7ED", foreground="#16854B")
            if probe_target.get("check_type") == "snmp" and not self.name_var.get().strip():
                sys_name = _extract_snmp_sys_name(detail)
                if sys_name:
                    self.name_var.set(sys_name)
        else:
            err = str(result.get("error_msg") or "连接失败")
            self.test_result_var.set(f"✗ 连接失败：{err}")
            self.test_result_label.configure(background="#FCE9E9", foreground="#D14343")

    def _commit(self) -> None:
        try:
            value = self._build_target_dict(require_name=True)
        except ValueError as error:
            messagebox.showerror("输入错误", str(error), parent=self)
            return
        self._on_commit(value)
        self.destroy()


class BatchImportDialog(tk.Toplevel):
    """Bulk-add targets from pasted lines or IP ranges with preview feedback."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        existing_names: set[str],
        available_groups: list[str] | None = None,
        on_import: Callable[[list[dict[str, Any]]], None],
    ) -> None:
        super().__init__(parent)
        self._existing_names = set(existing_names)
        self._on_import = on_import
        self._groups = sorted({DEFAULT_GROUP, *(g.strip() for g in (available_groups or []) if g.strip())})

        self.title("批量导入监控目标")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self.minsize(560, 440)
        self._build_widgets()

    def _build_widgets(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        opts = ttk.Frame(frame)
        opts.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.default_group_var = tk.StringVar(value=DEFAULT_GROUP)
        self.default_type_var = tk.StringVar(value="ping")
        self.default_role_var = tk.StringVar(value=DEVICE_ROLE_LABELS["other"])
        self.default_comm_var = tk.StringVar(value="public")
        ttk.Label(opts, text="默认分组").pack(side=tk.LEFT)
        ttk.Combobox(opts, textvariable=self.default_group_var, values=self._groups, width=10).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(opts, text="默认类型").pack(side=tk.LEFT)
        ttk.Combobox(opts, textvariable=self.default_type_var, values=SUPPORTED_CHECK_TYPES, state="readonly", width=7).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(opts, text="设备类型").pack(side=tk.LEFT)
        ttk.Combobox(opts, textvariable=self.default_role_var, values=tuple(DEVICE_ROLE_LABELS.values()), state="readonly", width=8).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(opts, text="SNMP口令").pack(side=tk.LEFT)
        ttk.Entry(opts, textvariable=self.default_comm_var, width=10).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(
            frame,
            text="每行一条：直接填 IP/范围（如 192.168.1.1-20），或 名称,地址,类型（支持 ping/tcp/http/snmp）",
            foreground="#68778C",
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 6))

        self.text = tk.Text(frame, width=64, height=14, wrap=tk.NONE)
        self.text.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.text.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self.text.configure(yscrollcommand=sb.set)

        footer = ttk.Frame(frame)
        footer.grid(row=3, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        ttk.Button(footer, text="取消", command=self.destroy, style="Secondary.TButton").pack(side=tk.RIGHT)
        ttk.Button(footer, text="确认导入", command=self._do_import, style="Primary.TButton").pack(side=tk.RIGHT, padx=(0, 8))

    def _do_import(self) -> None:
        created, skipped, errors = parse_batch_targets(
            self.text.get("1.0", tk.END),
            self._existing_names,
            default_group=self.default_group_var.get(),
            default_check_type=self.default_type_var.get(),
            default_role=DEVICE_ROLE_VALUES.get(self.default_role_var.get(), "other"),
            default_snmp_community=self.default_comm_var.get(),
        )
        if not created and not errors:
            messagebox.showinfo("批量导入", "没有可导入的新目标（可能为空或名称已存在）。", parent=self)
            return
        if created:
            self._on_import(created)
        lines = [f"已导入 {len(created)} 条目标。"]
        if skipped:
            preview = ", ".join(skipped[:8]) + (f" 等 {len(skipped)} 条" if len(skipped) > 8 else "")
            lines.append(f"跳过重复名称：{preview}")
        if errors:
            preview = "\n".join(errors[:5]) + (f"\n…等 {len(errors)} 条错误" if len(errors) > 5 else "")
            lines.append(f"以下行未导入：\n{preview}")
        messagebox.showinfo("批量导入完成", "\n".join(lines), parent=self)
        if created:
            self.destroy()


class SettingsDialog(tk.Toplevel):
    """Clean tabbed dialog for global application settings (decoupled from targets)."""

    def __init__(
        self,
        parent: tk.Misc,
        settings: dict[str, Any],
        on_save: Callable[[dict[str, Any]], None],
    ) -> None:
        super().__init__(parent)
        self.title("系统设置")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.minsize(560, 440)
        self.settings = settings.copy()
        self.on_save = on_save
        self._build_widgets()

    def _build_widgets(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(body)
        notebook.pack(fill=tk.BOTH, expand=True)

        alert_tab = ttk.Frame(notebook, padding=14)
        probe_tab = ttk.Frame(notebook, padding=14)
        general_tab = ttk.Frame(notebook, padding=14)
        notebook.add(alert_tab, text="告警与通知")
        notebook.add(probe_tab, text="检测参数")
        notebook.add(general_tab, text="报告与启动")

        # --- Variables ---
        self.webhook_var = tk.StringVar(value=str(self.settings.get("wechat_webhook", "")))
        self._webhook_visible_var = tk.BooleanVar(value=False)
        self.threshold_var = tk.StringVar(value=str(self.settings.get("failure_threshold", 3)))
        self.cooldown_var = tk.StringVar(value=str(self.settings.get("cooldown_minutes", 5)))
        self.recovery_var = tk.BooleanVar(value=as_bool(self.settings.get("send_recovery_notification", True)))
        self.desktop_alert_var = tk.BooleanVar(value=as_bool(self.settings.get("desktop_alert_enabled", True)))
        self.desktop_sound_var = tk.BooleanVar(value=as_bool(self.settings.get("desktop_alert_sound_enabled", True)))
        self.desktop_flash_var = tk.BooleanVar(value=as_bool(self.settings.get("desktop_alert_flash_enabled", True)))
        self.slow_threshold_var = tk.StringVar(value=str(self.settings.get("slow_warning_threshold_ms", 0)))
        self.correlation_threshold_var = tk.StringVar(value=str(self.settings.get("correlation_alert_threshold", 3)))
        self.snmp_cpu_var = tk.StringVar(value=str(self.settings.get("snmp_cpu_threshold", 90)))
        self.snmp_mem_var = tk.StringVar(value=str(self.settings.get("snmp_mem_threshold", 90)))
        self.snmp_disk_var = tk.StringVar(value=str(self.settings.get("snmp_disk_threshold", 90)))
        self.tls_warn_days_var = tk.StringVar(value=str(self.settings.get("tls_expiry_warn_days", 30)))

        self.ping_count_var = tk.StringVar(value=str(self.settings.get("ping_count", 1)))
        self.ping_timeout_var = tk.StringVar(value=str(self.settings.get("ping_timeout_ms", 2000)))
        self.retention_days_var = tk.StringVar(value=str(self.settings.get("history_retention_days", 30)))
        self.log_level_var = tk.StringVar(
            value=LOG_LEVEL_LABELS.get(str(self.settings.get("log_level", "INFO")).upper(), "信息")
        )

        self.daily_report_var = tk.BooleanVar(value=as_bool(self.settings.get("daily_report_enabled", True)))
        self.daily_report_time_var = tk.StringVar(value=str(self.settings.get("daily_report_time", "08:00")))
        self.startup_view_var = tk.StringVar(
            value=STARTUP_VIEW_LABELS.get(str(self.settings.get("startup_view", "wall")), STARTUP_VIEW_LABELS["wall"])
        )
        self.autostart_var = tk.BooleanVar(value=as_bool(self.settings.get("autostart_enabled", False)))

        # --- Tab 1: 告警与通知 ---
        alert_tab.columnconfigure(1, weight=1)
        self._webhook_entry = self._field(alert_tab, 0, "企业微信 Webhook", self.webhook_var, show="*")
        webhook_btns = ttk.Frame(alert_tab)
        webhook_btns.grid(row=0, column=2, sticky=tk.W, padx=(6, 0))
        ttk.Checkbutton(
            webhook_btns, text="显示", variable=self._webhook_visible_var, command=self._toggle_webhook_visibility,
        ).pack(side=tk.LEFT)
        self.test_webhook_btn = ttk.Button(webhook_btns, text="测试", command=self._test_webhook, width=5)
        self.test_webhook_btn.pack(side=tk.LEFT, padx=(4, 0))

        self._field(alert_tab, 1, "连续失败阈值(次)", self.threshold_var, width=12)
        self._field(alert_tab, 2, "重复告警冷却(分钟)", self.cooldown_var, width=12)
        self._field(alert_tab, 3, "慢响应阈值(毫秒,0=关闭)", self.slow_threshold_var, width=12)
        self._field(alert_tab, 4, "关联告警阈值(台,0=关闭)", self.correlation_threshold_var, width=12)

        res_row = ttk.Frame(alert_tab)
        res_row.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(6, 2))
        ttk.Label(res_row, text="SNMP 预警阈值(%)  CPU").pack(side=tk.LEFT)
        ttk.Entry(res_row, textvariable=self.snmp_cpu_var, width=5).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(res_row, text="内存").pack(side=tk.LEFT)
        ttk.Entry(res_row, textvariable=self.snmp_mem_var, width=5).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(res_row, text="磁盘").pack(side=tk.LEFT)
        ttk.Entry(res_row, textvariable=self.snmp_disk_var, width=5).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(res_row, text="证书到期(天)").pack(side=tk.LEFT)
        ttk.Entry(res_row, textvariable=self.tls_warn_days_var, width=5).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Separator(alert_tab, orient=tk.HORIZONTAL).grid(row=6, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Checkbutton(alert_tab, text="故障恢复时发送通知", variable=self.recovery_var).grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Checkbutton(alert_tab, text="启用本机声光告警", variable=self.desktop_alert_var).grid(row=8, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Checkbutton(alert_tab, text="播放 Windows 系统告警提示音", variable=self.desktop_sound_var).grid(row=9, column=0, columnspan=2, sticky=tk.W, padx=(20, 0), pady=2)
        ttk.Checkbutton(alert_tab, text="闪烁窗口标题并弹出托盘气泡", variable=self.desktop_flash_var).grid(row=10, column=0, columnspan=2, sticky=tk.W, padx=(20, 0), pady=2)

        # --- Tab 2: 检测参数 ---
        probe_tab.columnconfigure(1, weight=1)
        self._field(probe_tab, 0, "Ping 发包数量(1-10)", self.ping_count_var, width=12)
        self._field(probe_tab, 1, "Ping 单包超时(毫秒,100-10000)", self.ping_timeout_var, width=12)
        self._field(probe_tab, 2, "检测历史保留天数(1-365)", self.retention_days_var, width=12)
        ttk.Label(probe_tab, text="日志记录级别").grid(row=3, column=0, sticky=tk.W, padx=(0, 12), pady=6)
        ttk.Combobox(
            probe_tab, textvariable=self.log_level_var, values=tuple(LOG_LEVEL_LABELS.values()), state="readonly", width=12,
        ).grid(row=3, column=1, sticky=tk.W, pady=6)
        ttk.Label(
            probe_tab, text="提示：TCP/HTTP/SNMP 的单独参数请在目标编辑页中配置。", foreground="#68778C",
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(12, 0))

        # --- Tab 3: 报告与启动 ---
        general_tab.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            general_tab, text="每日定时发送巡检报告到企业微信", variable=self.daily_report_var,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(4, 6))
        self._field(general_tab, 1, "报告发送时间(HH:MM)", self.daily_report_time_var, width=12)
        ttk.Separator(general_tab, orient=tk.HORIZONTAL).grid(row=2, column=0, columnspan=2, sticky="ew", pady=12)
        ttk.Label(general_tab, text="启动时默认进入").grid(row=3, column=0, sticky=tk.W, padx=(0, 12), pady=6)
        ttk.Combobox(
            general_tab, textvariable=self.startup_view_var, values=tuple(STARTUP_VIEW_LABELS.values()), state="readonly", width=18,
        ).grid(row=3, column=1, sticky=tk.W, pady=6)
        ttk.Label(
            general_tab, text="下次启动生效；运行中随时可通过侧边栏或 F11/Esc 切换大屏。", foreground="#68778C",
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        ttk.Checkbutton(
            general_tab, text="开机自动启动（静默运行到系统托盘，仅打包 EXE 生效）", variable=self.autostart_var,
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(12, 0))

        actions = ttk.Frame(body)
        actions.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(actions, text="取消", command=self.destroy, style="Secondary.TButton").pack(side=tk.RIGHT)
        ttk.Button(actions, text="保存设置", command=self._save, style="Primary.TButton").pack(side=tk.RIGHT, padx=(0, 8))

    @staticmethod
    def _field(
        parent: tk.Misc, row: int, label: str, variable: tk.StringVar, *, show: str | None = None, width: int = 34,
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 12), pady=5)
        entry = ttk.Entry(parent, textvariable=variable, width=width, show=show or "")
        entry.grid(row=row, column=1, sticky=tk.EW if width > 15 else tk.W, pady=5)
        return entry

    def _toggle_webhook_visibility(self) -> None:
        self._webhook_entry.configure(show="" if self._webhook_visible_var.get() else "*")

    def _test_webhook(self) -> None:
        url = self.webhook_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先填写企业微信 Webhook 地址。", parent=self)
            return
        self.test_webhook_btn.configure(state=tk.DISABLED)

        def worker() -> None:
            notifier = WeChatNotifier(url, logging.getLogger("webhook-test"))
            ok = notifier.send_markdown("# <font color=\"info\">网哨测试通知</font>\n> 企业微信机器人连接测试成功。")
            try:
                self.after(0, lambda: self._on_webhook_test_done(ok))
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="webhook-test", daemon=True).start()

    def _on_webhook_test_done(self, ok: bool) -> None:
        try:
            self.test_webhook_btn.configure(state=tk.NORMAL)
        except tk.TclError:
            return
        if ok:
            messagebox.showinfo("测试成功", "测试消息已发送到企业微信群。", parent=self)
        else:
            messagebox.showerror("测试失败", "发送失败，请检查 Webhook 地址和网络连通性。", parent=self)

    def _save(self) -> None:
        try:
            ping_count = int(self.ping_count_var.get())
            ping_timeout_ms = int(self.ping_timeout_var.get())
            retention_days = int(self.retention_days_var.get())
            threshold = int(self.threshold_var.get())
            cooldown = int(self.cooldown_var.get())
            slow_threshold = int(self.slow_threshold_var.get())
            correlation_threshold = int(self.correlation_threshold_var.get())
            snmp_cpu = int(self.snmp_cpu_var.get())
            snmp_mem = int(self.snmp_mem_var.get())
            snmp_disk = int(self.snmp_disk_var.get())
            tls_warn_days = int(self.tls_warn_days_var.get())
        except ValueError:
            messagebox.showerror("输入错误", "数值字段必须是整数。", parent=self)
            return
        if not 1 <= ping_count <= 10:
            messagebox.showerror("输入错误", "Ping 包数量必须在 1-10 范围内。", parent=self)
            return
        if not 100 <= ping_timeout_ms <= 10000:
            messagebox.showerror("输入错误", "Ping 超时必须在 100-10000 毫秒范围内。", parent=self)
            return
        if not 1 <= retention_days <= 365:
            messagebox.showerror("输入错误", "历史保留天数必须在 1-365 范围内。", parent=self)
            return
        if slow_threshold < 0 or correlation_threshold < 0 or tls_warn_days < 0:
            messagebox.showerror("输入错误", "阈值不能为负数。", parent=self)
            return
        if not (0 <= snmp_cpu <= 100 and 0 <= snmp_mem <= 100 and 0 <= snmp_disk <= 100):
            messagebox.showerror("输入错误", "SNMP 资源预警阈值必须在 0-100 之间（0 表示关闭）。", parent=self)
            return
        report_time = self.daily_report_time_var.get().strip()
        try:
            parts = report_time.split(":")
            if len(parts) != 2 or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "报告时间格式应为 HH:MM。", parent=self)
            return

        previous_autostart = as_bool(self.settings.get("autostart_enabled", False))
        new_autostart = self.autostart_var.get()
        if new_autostart != previous_autostart:
            ok, msg = set_autostart(new_autostart)
            if not ok:
                messagebox.showwarning("开机自启提示", msg, parent=self)

        self.settings.update({
            "wechat_webhook": self.webhook_var.get().strip(),
            "failure_threshold": max(1, threshold),
            "cooldown_minutes": max(0, cooldown),
            "send_recovery_notification": self.recovery_var.get(),
            "desktop_alert_enabled": self.desktop_alert_var.get(),
            "desktop_alert_sound_enabled": self.desktop_sound_var.get(),
            "desktop_alert_flash_enabled": self.desktop_flash_var.get(),
            "log_level": LOG_LEVEL_VALUES.get(self.log_level_var.get(), "INFO"),
            "history_retention_days": retention_days,
            "ping_count": ping_count,
            "ping_timeout_ms": ping_timeout_ms,
            "daily_report_enabled": self.daily_report_var.get(),
            "daily_report_time": report_time,
            "slow_warning_threshold_ms": max(0, slow_threshold),
            "correlation_alert_threshold": max(0, correlation_threshold),
            "snmp_cpu_threshold": snmp_cpu,
            "snmp_mem_threshold": snmp_mem,
            "snmp_disk_threshold": snmp_disk,
            "tls_expiry_warn_days": tls_warn_days,
            "startup_view": STARTUP_VIEW_VALUES.get(self.startup_view_var.get(), "wall"),
            "autostart_enabled": new_autostart,
        })
        self.on_save(self.settings)
        self.destroy()


class ConfigDialog(SettingsDialog):
    """Backward-compatible alias opening the tabbed system-settings dialog."""

    def __init__(
        self,
        parent: tk.Misc,
        targets: list[dict[str, Any]],
        settings: dict[str, Any],
        on_save: Callable[[list[dict[str, Any]], dict[str, Any]], None],
    ) -> None:
        self._compat_targets = [item.copy() for item in targets]
        super().__init__(parent, settings, lambda new_settings: on_save(self._compat_targets, new_settings))
