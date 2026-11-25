"""Failure-threshold, cooldown, correlation suppression, mute, and resource warning alert logic."""
from __future__ import annotations

import collections
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from app.integrations.wechat import WeChatNotifier

_CORRELATION_WINDOW_SECONDS = 60


def is_target_muted(target: dict[str, Any], now: datetime | None = None) -> bool:
    """Return True if a target is currently inside a temporary maintenance mute window."""
    raw = str(target.get("muted_until") or "").strip()
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
        if until.tzinfo is not None:
            until = until.replace(tzinfo=None)
    except ValueError:
        return False
    current = now or datetime.now()
    return current < until


def evaluate_performance_warnings(
    target: dict[str, Any], result: dict[str, Any], settings: dict[str, Any],
) -> list[str]:
    """Check an 'up' result for slow latency, SNMP resource thresholds, or expiring TLS certs."""
    if result.get("status") != "up":
        return []
    warnings: list[str] = []

    slow_ms = max(0, int(settings.get("slow_warning_threshold_ms", 0) or 0))
    latency = result.get("latency")
    if slow_ms > 0 and latency is not None:
        try:
            lat_val = float(latency)
            if lat_val >= slow_ms:
                warnings.append(f"响应缓慢：{lat_val:.0f} ms（阈值 {slow_ms} ms）")
        except (TypeError, ValueError):
            pass

    detail = str(result.get("detail") or "")
    if str(target.get("check_type", "")).lower() == "snmp" and detail:
        cpu_limit = max(0, int(settings.get("snmp_cpu_threshold", 90) or 0))
        mem_limit = max(0, int(settings.get("snmp_mem_threshold", 90) or 0))
        disk_limit = max(0, int(settings.get("snmp_disk_threshold", 90) or 0))
        cpu_m = re.search(r"CPU\s*(\d+(?:\.\d+)?)\s*%", detail)
        if cpu_limit > 0 and cpu_m and float(cpu_m.group(1)) >= cpu_limit:
            warnings.append(f"CPU 使用率过高：{float(cpu_m.group(1)):.0f}%（阈值 {cpu_limit}%）")
        mem_m = re.search(r"内存\s*(\d+(?:\.\d+)?)\s*%", detail)
        if mem_limit > 0 and mem_m and float(mem_m.group(1)) >= mem_limit:
            warnings.append(f"内存使用率过高：{float(mem_m.group(1)):.0f}%（阈值 {mem_limit}%）")
        disk_m = re.search(r"磁盘最高\s*(\d+(?:\.\d+)?)\s*%", detail)
        if disk_limit > 0 and disk_m and float(disk_m.group(1)) >= disk_limit:
            warnings.append(f"磁盘使用率过高：{float(disk_m.group(1)):.0f}%（阈值 {disk_limit}%）")

    tls_warn_days = max(0, int(settings.get("tls_expiry_warn_days", 30) or 0))
    days_left = result.get("tls_days_left")
    if tls_warn_days > 0 and days_left is not None:
        try:
            days_val = int(days_left)
            if days_val <= tls_warn_days:
                if days_val < 0:
                    warnings.append(f"HTTPS 证书已过期 {abs(days_val)} 天")
                else:
                    warnings.append(f"HTTPS 证书即将过期：剩余 {days_val} 天")
        except (TypeError, ValueError):
            pass
    return warnings


class AlertEngine:
    """Tracks each target's health state and sends state-transition and resource alerts."""

    def __init__(
        self,
        settings: dict[str, Any],
        notifier: WeChatNotifier,
        logger: logging.Logger,
        state_store: Any | None = None,
    ) -> None:
        self.settings = settings
        self.notifier = notifier
        self.logger = logger
        self._state_store = state_store
        self._states = self._load_states()
        self._warning_cooldowns: dict[str, datetime] = {}
        self._recent_failures: collections.deque[tuple[float, str, str]] = collections.deque()
        self._last_correlation_alert: datetime | None = None
        self._lock = threading.Lock()

    def _load_states(self) -> dict[str, dict[str, Any]]:
        """Restore failures/cooldowns without restoring a half-sent webhook flag."""
        if self._state_store is None:
            return {}
        try:
            stored = self._state_store.load_alert_states()
        except Exception:
            self.logger.exception("读取持久化告警状态失败，将从当前检测结果重新累计")
            return {}
        states: dict[str, dict[str, Any]] = {}
        for key, value in stored.items():
            if not isinstance(value, dict):
                continue
            last_alert = value.get("last_alert")
            if isinstance(last_alert, str):
                try:
                    last_alert = datetime.fromisoformat(last_alert)
                except ValueError:
                    last_alert = None
            if not isinstance(last_alert, datetime):
                last_alert = None
            states[str(key)] = {
                "failures": max(0, int(value.get("failures", 0))),
                "alerted": bool(value.get("alerted", False)),
                "last_alert": last_alert,
                "notifying": None,
                "recovery_invalidated": False,
                "last_failure_result": None,
            }
        return states

    def _persist_state_locked(self, key: str, state: dict[str, Any]) -> None:
        if self._state_store is None:
            return
        try:
            self._state_store.save_alert_state(key, state)
        except Exception:
            self.logger.exception("保存告警状态失败")

    @staticmethod
    def _key(target: dict[str, Any]) -> str:
        return f"{target['name']}|{target['address']}|{target['check_type']}"

    def process(self, target: dict[str, Any], result: dict[str, Any]) -> str | None:
        """Process one result, without holding state locks during webhook I/O."""
        if is_target_muted(target):
            return None

        with self._lock:
            action, content = self._reserve_notification(target, result)

        if action is None:
            # Check performance / SNMP resource / TLS expiry warnings on healthy checks.
            if target.get("alert_enabled", True) and result.get("status") == "up":
                return self._maybe_send_warning(target, result)
            return None

        if action == "correlation":
            delivered = self.notifier.send_markdown(content)
            return "correlation" if delivered else None

        delivered = self.notifier.send_markdown(content)
        correction_content = ""
        with self._lock:
            state = self._states[self._key(target)]
            if state.get("notifying") != action:
                return None
            state["notifying"] = None
            if delivered and action == "alert":
                state["alerted"] = True
                state["last_alert"] = datetime.now()
            elif delivered and action == "recovery":
                if state["recovery_invalidated"]:
                    state["notifying"] = "alert"
                    correction_content = self._failure_message(
                        target, state["last_failure_result"], state["failures"]
                    )
                else:
                    state.update({"failures": 0, "alerted": False, "last_alert": None})
            state["recovery_invalidated"] = False
            self._persist_state_locked(self._key(target), state)
        if correction_content:
            corrected = self.notifier.send_markdown(correction_content)
            with self._lock:
                state = self._states[self._key(target)]
                if state.get("notifying") == "alert":
                    state["notifying"] = None
                    if corrected:
                        state["alerted"] = True
                        state["last_alert"] = datetime.now()
                    self._persist_state_locked(self._key(target), state)
            return "alert" if corrected else "recovery"
        return action if delivered else None

    def _maybe_send_warning(self, target: dict[str, Any], result: dict[str, Any]) -> str | None:
        warnings = evaluate_performance_warnings(target, result, self.settings)
        if not warnings:
            return None
        key = self._key(target)
        now = datetime.now()
        cooldown = timedelta(minutes=max(5, int(self.settings.get("cooldown_minutes", 5))))
        with self._lock:
            last = self._warning_cooldowns.get(key)
            if last is not None and now - last < cooldown:
                return None
            self._warning_cooldowns[key] = now
        content = self._warning_message(target, result, warnings)
        delivered = self.notifier.send_markdown(content)
        if not delivered:
            with self._lock:
                self._warning_cooldowns.pop(key, None)
            return None
        return "warning"

    def _reserve_notification(self, target: dict[str, Any], result: dict[str, Any]) -> tuple[str | None, str]:
        """Atomically update health state and reserve one notification, if due."""
        if not target.get("alert_enabled", True):
            return None, ""
        key = self._key(target)
        state = self._states.setdefault(
            key, {
                "failures": 0, "alerted": False, "last_alert": None,
                "notifying": None, "recovery_invalidated": False, "last_failure_result": None,
            }
        )
        if result["status"] == "up":
            if state["alerted"] and self.settings.get("send_recovery_notification", True):
                if not state["notifying"]:
                    state["notifying"] = "recovery"
                    self._persist_state_locked(key, state)
                    return "recovery", self._recovery_message(target, result)
                return None, ""
            if not state["notifying"]:
                state.update({"failures": 0, "alerted": False, "last_alert": None})
                self._persist_state_locked(key, state)
            return None, ""

        state["failures"] += 1
        state["last_failure_result"] = result.copy()
        if state["notifying"] == "recovery":
            state["recovery_invalidated"] = True
        threshold = max(1, int(self.settings.get("failure_threshold", 3)))
        cooldown = timedelta(minutes=max(0, int(self.settings.get("cooldown_minutes", 5))))
        now = datetime.now()
        last_alert = state["last_alert"]
        due = last_alert is None or now - last_alert >= cooldown
        if state["failures"] >= threshold and due and not state["notifying"]:
            corr_threshold = max(0, int(self.settings.get("correlation_alert_threshold", 0) or 0))
            if corr_threshold >= 2:
                mono_now = time.monotonic()
                while self._recent_failures and mono_now - self._recent_failures[0][0] > _CORRELATION_WINDOW_SECONDS:
                    self._recent_failures.popleft()
                self._recent_failures.append((mono_now, key, str(target.get("name", ""))))
                distinct_names = list(dict.fromkeys(item[2] for item in self._recent_failures))
                if len(distinct_names) >= corr_threshold:
                    state["alerted"] = True
                    state["last_alert"] = now
                    self._persist_state_locked(key, state)
                    if self._last_correlation_alert is None or now - self._last_correlation_alert >= cooldown:
                        self._last_correlation_alert = now
                        return "correlation", self._correlation_message(distinct_names, result.get("check_time", ""))
                    # Suppress individual storm alert within active correlation cooldown.
                    return None, ""

            state["notifying"] = "alert"
            self._persist_state_locked(key, state)
            return "alert", self._failure_message(target, result, state["failures"])
        self._persist_state_locked(key, state)
        return None, ""

    @staticmethod
    def _failure_message(target: dict[str, Any], result: dict[str, Any], failures: int) -> str:
        detail_line = f"> **详情**：{result['detail']}\n" if result.get("detail") else ""
        return (
            "# <font color=\"warning\">网哨告警</font>\n"
            f"> **故障对象**：{target['name']}\n"
            f"> **地址**：{target['address']}\n"
            f"> **检测类型**：{target['check_type'].upper()}\n"
            f"> **连续失败**：{failures} 次\n"
            f"> **错误信息**：{result.get('error_msg') or '连接失败'}\n"
            f"{detail_line}"
            f"> **时间**：{result['check_time']}"
        )

    @staticmethod
    def _recovery_message(target: dict[str, Any], result: dict[str, Any]) -> str:
        detail_line = f"> **详情**：{result['detail']}\n" if result.get("detail") else ""
        return (
            "# <font color=\"info\">网哨恢复</font>\n"
            f"> **恢复对象**：{target['name']}\n"
            f"> **地址**：{target['address']}\n"
            f"> **检测类型**：{target['check_type'].upper()}\n"
            f"{detail_line}"
            f"> **时间**：{result['check_time']}"
        )

    @staticmethod
    def _correlation_message(names: list[str], check_time: str) -> str:
        listed = "、".join(names[:10]) + (f" 等 {len(names)} 台" if len(names) > 10 else "")
        return (
            "# <font color=\"warning\">网哨关联告警（防刷屏合并）</font>\n"
            f"> **现象**：60 秒内共有 **{len(names)}** 个目标同时失联\n"
            f"> **疑似原因**：上游核心设备故障或本机网络中断\n"
            f"> **涉及目标**：{listed}\n"
            f"> **时间**：{check_time or datetime.now().isoformat(timespec='seconds')}"
        )

    @staticmethod
    def _warning_message(target: dict[str, Any], result: dict[str, Any], warnings: list[str]) -> str:
        joined = "；".join(warnings)
        return (
            "# <font color=\"warning\">网哨资源 / 性能预警</font>\n"
            f"> **对象**：{target['name']}（{target['address']}）\n"
            f"> **预警内容**：{joined}\n"
            f"> **时间**：{result.get('check_time') or datetime.now().isoformat(timespec='seconds')}"
        )
