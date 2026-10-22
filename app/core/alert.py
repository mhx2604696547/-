"""Failure-threshold and cooldown-based alert logic."""
from __future__ import annotations

import collections
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from app.integrations.wechat import WeChatNotifier

_CORRELATION_WINDOW_SECONDS = 60


class AlertEngine:
    """Tracks each target's health state and sends state-transition alerts."""

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
        """Best-effort durable state; monitoring still works if SQLite is transiently busy."""
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
        with self._lock:
            action, content = self._reserve_notification(target, result)
        if action is None:
            return None

        # HTTP can take seconds. Other targets may update their own state while it runs.
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
                # A newer failure during the slow recovery webhook must not be erased.
                if state["recovery_invalidated"]:
                    # The recovery was already delivered, so immediately correct it
                    # with the newest failure instead of honoring the old cooldown.
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
            # A recovery still clears alert state when recovery messages are disabled.
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
            state["notifying"] = "alert"
            self._persist_state_locked(key, state)
            return "alert", self._failure_message(target, result, state["failures"])
        self._persist_state_locked(key, state)
        return None, ""

    @staticmethod
    def _failure_message(target: dict[str, Any], result: dict[str, Any], failures: int) -> str:
        detail_line = f"> **详情**：{result['detail']}\n" if result.get("detail") else ""
        return (
            "# <font color=\"warning\">网络监控告警</font>\n"
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
            "# <font color=\"info\">网络监控恢复</font>\n"
            f"> **恢复对象**：{target['name']}\n"
            f"> **地址**：{target['address']}\n"
            f"> **检测类型**：{target['check_type'].upper()}\n"
            f"{detail_line}"
            f"> **时间**：{result['check_time']}"
        )
