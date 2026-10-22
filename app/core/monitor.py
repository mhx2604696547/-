"""Background monitoring scheduler, daily inspection workflow, and GUI events."""
from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from app.core import http_check, ping_check, snmp_check, tcp_check
from app.core.alert import AlertEngine
from app.utils.database import MonitorDatabase


CHECKERS: dict[str, Callable[[str], dict[str, object]]] = {
    "tcp": tcp_check.check,
    "http": http_check.check,
}

CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
_INSPECTION_RUN_KEY = "_inspection_run_id"


class MonitorEngine:
    """Schedules independent target checks in a bounded worker pool."""

    def __init__(
        self,
        targets: list[dict[str, Any]],
        database: MonitorDatabase,
        alert_engine: AlertEngine,
        event_queue: queue.Queue[dict[str, Any]],
        logger: logging.Logger,
    ) -> None:
        self.targets = targets
        self.database = database
        self.alert_engine = alert_engine
        self.event_queue = event_queue
        self.logger = logger
        self._stop_event: threading.Event | None = None
        self._wake_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._next_run: dict[str, float] = {}
        self._next_cleanup: float = 0.0
        self._inflight: set[str] = set()
        self._last_inspection_run_id: str | None = None
        # A bounded pool keeps slow HTTP checks from delaying unrelated targets.
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="monitor-check")

    def start(self) -> bool:
        """Start the scheduler, never reviving an older still-stopping thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self._stop_event and self._stop_event.is_set():
                    self.logger.warning("上一轮监控尚未退出，拒绝重复启动")
                    return False
                return True
            self._stop_event = threading.Event()
            self._next_run.clear()
            # main.py already ran a cleanup pass at process startup; the next
            # one is due a full interval from now, not immediately again.
            self._next_cleanup = time.monotonic() + CLEANUP_INTERVAL_SECONDS
            stop_event = self._stop_event
            self._wake_event.set()
            self._thread = threading.Thread(target=self._run, args=(stop_event,), name="monitor-scheduler", daemon=True)
            self._thread.start()
        self.logger.info("监控引擎已启动")
        return True

    def stop(self) -> None:
        """Request scheduling shutdown without blocking the Tkinter event loop."""
        with self._lock:
            stop_event = self._stop_event
        if stop_event is None:
            return
        stop_event.set()
        self._wake_event.set()
        self.logger.info("已请求停止监控引擎")

    def shutdown(self) -> None:
        """Stop scheduling and cancel queued checks during application exit."""
        self.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._stop_event and not self._stop_event.is_set())

    @property
    def last_inspection_run_id(self) -> str | None:
        """Expose the latest persisted web checklist to the GUI safely."""
        with self._lock:
            return self._last_inspection_run_id

    def update_targets(self, targets: list[dict[str, Any]]) -> None:
        """Replace the scheduler's targets after GUI configuration is saved."""
        with self._lock:
            self.targets = targets
            self._next_run.clear()
        self._wake_event.set()

    def check_now(self, target_name: str | None = None, check_type: str | None = None) -> int:
        """Queue matching immediate checks without blocking the Tkinter event loop.

        A target already being checked is deliberately skipped. This prevents a
        repeated button click or a scheduled run from piling up duplicate HTTP
        requests for the same device.
        """
        with self._lock:
            candidates = [
                item.copy()
                for item in self.targets
                if item.get("enabled", True)
                and (target_name is None or item.get("name") == target_name)
                and (check_type is None or str(item.get("check_type", "")).lower() == check_type.lower())
            ]
        return len(self._queue_checks(candidates, None))

    def check_websites_now(self) -> int:
        """Start a persisted daily checklist for all enabled HTTP targets."""
        with self._lock:
            candidates = [
                item.copy()
                for item in self.targets
                if str(item.get("check_type", "")).lower() == "http" and item.get("enabled", True)
            ]
        reserved = self._reserve_targets(candidates)
        if not reserved:
            return 0

        run_id: str | None = None
        create_run = getattr(self.database, "create_inspection_run", None)
        if callable(create_run):
            try:
                run_id = str(create_run(reserved, inspection_type="website"))
                with self._lock:
                    self._last_inspection_run_id = run_id
                self.event_queue.put({
                    "event": "inspection_started", "run_id": run_id, "total": len(reserved), "retry": False,
                })
            except Exception as error:
                self.logger.exception("创建每日网页巡检批次失败，仍继续执行本次检测")
                self.event_queue.put({"event": "inspection_error", "message": f"无法保存巡检批次：{error}"})

        return len(self._submit_reserved_checks(reserved, None, run_id))

    def retry_inspection_failures(self, run_id: str | None = None) -> int:
        """Recheck only the targets currently failed in the latest daily run."""
        run_id = run_id or self.last_inspection_run_id
        if not run_id:
            return 0
        try:
            failures = self.database.get_inspection_failures(run_id)
        except Exception as error:
            self.logger.exception("读取网页巡检失败项失败")
            self.event_queue.put({"event": "inspection_error", "message": f"无法读取失败项：{error}"})
            return 0
        if not failures:
            return 0

        with self._lock:
            target_by_key = {MonitorDatabase.target_key(target): target.copy() for target in self.targets}
        candidates = [target_by_key[item["target_key"]] for item in failures if item["target_key"] in target_by_key]
        reserved = self._reserve_targets(candidates)
        if not reserved:
            return 0

        try:
            retried = self.database.prepare_inspection_retry(
                run_id, [MonitorDatabase.target_key(target) for target in reserved]
            )
            if retried != len(reserved):
                self.logger.warning("巡检失败项重测准备数异常：预留 %d，实际 %d", len(reserved), retried)
            if not retried:
                self._release_targets(reserved)
                return 0
            summary = self.database.get_inspection_run(run_id)
        except Exception as error:
            self._release_targets(reserved)
            self.logger.exception("准备网页巡检失败项重测失败")
            self.event_queue.put({"event": "inspection_error", "message": f"无法准备失败项重测：{error}"})
            return 0

        with self._lock:
            self._last_inspection_run_id = run_id
        self.event_queue.put({
            "event": "inspection_started", "run_id": run_id,
            "total": summary["total"] if summary else len(reserved), "retry": True,
        })
        return len(self._submit_reserved_checks(reserved, None, run_id))

    def _reserve_targets(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Atomically claim non-running targets before creating task futures."""
        reserved: list[dict[str, Any]] = []
        with self._lock:
            for target in candidates:
                key = MonitorDatabase.target_key(target)
                if key in self._inflight:
                    self.logger.debug("跳过已在检测中的目标：%s", key)
                    continue
                self._inflight.add(key)
                reserved.append(target)
        return reserved

    def _release_targets(self, targets: list[dict[str, Any]]) -> None:
        with self._lock:
            for target in targets:
                self._inflight.discard(MonitorDatabase.target_key(target))

    def _queue_checks(
        self, candidates: list[dict[str, Any]], stop_event: threading.Event | None, inspection_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._submit_reserved_checks(self._reserve_targets(candidates), stop_event, inspection_run_id)

    def _submit_reserved_checks(
        self, targets: list[dict[str, Any]], stop_event: threading.Event | None, inspection_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        submitted: list[dict[str, Any]] = []
        for target in targets:
            task_target = target.copy()
            if inspection_run_id:
                task_target[_INSPECTION_RUN_KEY] = inspection_run_id
            try:
                future = self._executor.submit(self._check_target, task_target, stop_event)
            except Exception:
                self._release_targets([target])
                self.logger.exception("提交检测任务失败：%s", target.get("name", "未知"))
                continue

            callback = getattr(future, "add_done_callback", None)
            if callable(callback):
                callback(lambda _future, item=target: self._release_targets([item]))
            submitted.append(target)
        return submitted

    def _run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            now = time.monotonic()
            if now >= self._next_cleanup:
                self._next_cleanup = now + CLEANUP_INTERVAL_SECONDS
                self._executor.submit(self._run_cleanup)
            with self._lock:
                due_targets = []
                for target in self.targets:
                    if not target.get("enabled", True):
                        continue
                    key = AlertEngine._key(target)
                    if now >= self._next_run.get(key, 0):
                        due_targets.append(target.copy())
                        self._next_run[key] = now + max(1, int(target.get("interval", 60)))
            self._queue_checks(due_targets, stop_event)
            self._wake_event.wait(timeout=0.3)
            self._wake_event.clear()

    def _run_cleanup(self) -> None:
        """Run the daily history retention sweep off the scheduler thread."""
        try:
            retention_days = int(self.alert_engine.settings.get("history_retention_days", 30))
            removed = self.database.cleanup_history(retention_days)
            if removed:
                self.logger.info("周期清理已删除 %d 条过期检测历史", removed)
        except Exception:
            self.logger.exception("周期性历史清理失败")

    def _check_target(self, task_target: dict[str, Any], stop_event: threading.Event | None) -> None:
        """Execute one network check; internal persistence failures never change it to down."""
        target = task_target.copy()
        inspection_run_id = str(target.pop(_INSPECTION_RUN_KEY, "") or "") or None
        if stop_event and stop_event.is_set():
            return

        try:
            check_type = str(target.get("check_type", "")).lower()
            if check_type == "snmp":
                result: dict[str, Any] = snmp_check.check(
                    str(target["address"]),
                    port=int(target.get("snmp_port", 161)),
                    version=str(target.get("snmp_version", "v2c")),
                    community=str(target.get("snmp_community", "public")),
                    device_role=str(target.get("device_role", "other")),
                )
            elif check_type == "ping":
                settings = self.alert_engine.settings
                result = ping_check.check(
                    str(target["address"]),
                    count=int(settings.get("ping_count", 1)),
                    timeout_ms=int(settings.get("ping_timeout_ms", 2000)),
                )
            elif check_type == "http":
                result = http_check.check(
                    str(target["address"]),
                    timeout_seconds=target.get("http_timeout_seconds", 5),
                    expected_text=str(target.get("http_expected_text", "")),
                    verify_tls=target.get("http_verify_tls", True),
                )
            else:
                checker = CHECKERS.get(check_type)
                result = (
                    {"status": "down", "latency": None, "error_msg": "不支持的检测类型"}
                    if checker is None else checker(str(target["address"]))
                )
        except Exception as error:  # A malformed target or checker failure is a real failed check.
            self.logger.exception("检测目标 %s 时发生异常", target.get("name", "未知"))
            result = {"status": "down", "latency": None, "error_msg": str(error)}

        if stop_event and stop_event.is_set():
            return
        result = dict(result)
        result["check_time"] = datetime.now().isoformat(timespec="seconds")
        storage_errors: list[str] = []
        try:
            self.database.save_result(target, result)
        except Exception as error:
            # The check did run. Keep its real up/down state visible and make
            # the data-store issue explicit instead of producing a false alarm.
            self.logger.exception("保存检测结果失败：%s", target.get("name", "未知"))
            storage_errors.append(f"检测结果未保存：{error}")

        # Let the Tk main loop reflect a real outage immediately.  A WeCom
        # webhook can be slow or unavailable exactly when the network is
        # broken; local sound/light alerts must not wait for that I/O.
        event: dict[str, Any] = {"event": "result", "target": target, "result": result}
        if storage_errors:
            event["storage_error"] = "；".join(storage_errors)
        self.event_queue.put(event)

        try:
            self.alert_engine.process(target, result)
        except Exception:
            self.logger.exception("处理目标告警状态失败：%s", target.get("name", "未知"))

        inspection_summary: dict[str, Any] | None = None
        if inspection_run_id:
            try:
                inspection_summary = self.database.record_inspection_result(inspection_run_id, target, result)
            except Exception as error:
                self.logger.exception("保存巡检批次结果失败：%s", target.get("name", "未知"))
                self.event_queue.put({"event": "inspection_error", "message": f"巡检批次未更新：{error}"})

        if inspection_summary is not None:
            progress_event = {"event": "inspection_progress", "run_id": inspection_run_id, "summary": inspection_summary}
            self.event_queue.put(progress_event)
            if inspection_summary["status"] == "finished":
                self.event_queue.put({"event": "inspection_finished", "run_id": inspection_run_id, "summary": inspection_summary})
