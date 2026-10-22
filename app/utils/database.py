"""SQLite storage for monitor history, daily inspections, and dashboard data."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


class MonitorDatabase:
    """Small repository around the application's SQLite history database.

    Each operation owns a short-lived SQLite connection. This keeps Tkinter
    and worker threads isolated while WAL mode and a busy timeout make brief
    concurrent reads/writes much less likely to fail on Windows.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS check_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_name TEXT NOT NULL,
                    target_addr TEXT NOT NULL,
                    check_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency REAL,
                    error_msg TEXT,
                    check_time TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_history_time ON check_history(check_time)")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_history_target_latest
                ON check_history(target_name, target_addr, check_type, id DESC)
                """
            )
            # The monitor wall aggregates at most 24 hours for configured
            # targets.  This index keeps that bounded read responsive even
            # when the local history has grown over several weeks.
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_history_target_time
                ON check_history(target_name, target_addr, check_type, check_time)
                """
            )
            # Non-destructive migration: older databases predate the SNMP
            # detail column, so retain their history and add it in place.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(check_history)")}
            if "detail" not in columns:
                connection.execute("ALTER TABLE check_history ADD COLUMN detail TEXT")

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS inspection_runs (
                    run_id TEXT PRIMARY KEY,
                    inspection_type TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    target_count INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS inspection_items (
                    run_id TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    target_addr TEXT NOT NULL,
                    check_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency REAL,
                    error_msg TEXT,
                    detail TEXT,
                    check_time TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (run_id, target_key),
                    FOREIGN KEY (run_id) REFERENCES inspection_runs(run_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_inspection_started ON inspection_runs(inspection_type, started_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_inspection_items_status ON inspection_items(run_id, status)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_states (
                    target_key TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL,
                    alerted INTEGER NOT NULL,
                    last_alert TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Small durable key-value bag for cross-restart flags such as
            # "the daily report for YYYY-MM-DD was already sent".
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def target_key(target: dict[str, Any]) -> str:
        """Return the stable identity shared by the monitor and inspection data."""
        return f"{target['name']}|{target['address']}|{target['check_type']}"

    @staticmethod
    def _timestamp(value: datetime | str | None = None) -> str:
        if value is None:
            return datetime.now().isoformat(timespec="seconds")
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        return value

    def load_alert_states(self) -> dict[str, dict[str, Any]]:
        """Load the durable portion of alert state after an application restart."""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT target_key, failures, alerted, last_alert FROM alert_states"
            ).fetchall()
            return {
                str(row[0]): {"failures": int(row[1]), "alerted": bool(row[2]), "last_alert": row[3]}
                for row in rows
            }
        finally:
            connection.close()

    def save_alert_state(self, target_key: str, state: dict[str, Any]) -> None:
        """Persist only durable alert values; in-flight webhook flags stay memory-only."""
        last_alert = state.get("last_alert")
        if isinstance(last_alert, datetime):
            last_alert = last_alert.isoformat(timespec="seconds")
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO alert_states (target_key, failures, alerted, last_alert, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    failures = excluded.failures,
                    alerted = excluded.alerted,
                    last_alert = excluded.last_alert,
                    updated_at = excluded.updated_at""",
                (
                    target_key, max(0, int(state.get("failures", 0))), int(bool(state.get("alerted", False))),
                    last_alert, self._timestamp(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def load_app_state(self) -> dict[str, str]:
        """Return the whole key-value bag used for cross-restart flags."""
        connection = self._connect()
        try:
            rows = connection.execute("SELECT key, value FROM app_state").fetchall()
            return {str(row[0]): str(row[1]) for row in rows}
        finally:
            connection.close()

    def save_app_state(self, key: str, value: str) -> None:
        """Upsert one cross-restart flag."""
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO app_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at""",
                (key, value, self._timestamp()),
            )
            connection.commit()
        finally:
            connection.close()

    def save_result(self, target: dict[str, Any], result: dict[str, Any]) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO check_history
                (target_name, target_addr, check_type, status, latency, error_msg, check_time, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    target["name"], target["address"], target["check_type"], result["status"],
                    result.get("latency"), result.get("error_msg", ""), result["check_time"], result.get("detail", ""),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def create_inspection_run(
        self,
        targets: list[dict[str, Any]],
        inspection_type: str = "website",
        started_at: datetime | str | None = None,
    ) -> str:
        """Create a persisted checklist before its background checks are queued."""
        run_id = uuid.uuid4().hex
        timestamp = self._timestamp(started_at)
        status = "finished" if not targets else "running"
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO inspection_runs
                (run_id, inspection_type, started_at, finished_at, status, target_count)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, inspection_type, timestamp, timestamp if not targets else None, status, len(targets)),
            )
            connection.executemany(
                """INSERT INTO inspection_items
                (run_id, target_key, target_name, target_addr, check_type, status)
                VALUES (?, ?, ?, ?, ?, 'pending')""",
                [
                    (run_id, self.target_key(target), target["name"], target["address"], target["check_type"])
                    for target in targets
                ],
            )
            connection.commit()
            return run_id
        finally:
            connection.close()

    def record_inspection_result(self, run_id: str, target: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        """Save one checklist outcome and return the recalculated run summary."""
        connection = self._connect()
        try:
            cursor = connection.execute(
                """UPDATE inspection_items
                SET status = ?, latency = ?, error_msg = ?, detail = ?, check_time = ?, attempts = attempts + 1
                WHERE run_id = ? AND target_key = ?""",
                (
                    result["status"], result.get("latency"), result.get("error_msg", ""), result.get("detail", ""),
                    result["check_time"], run_id, self.target_key(target),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("巡检批次中找不到该目标")
            summary = self._recalculate_inspection_run(connection, run_id)
            connection.commit()
            return summary
        finally:
            connection.close()

    def _recalculate_inspection_run(self, connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
        run = connection.execute(
            """SELECT inspection_type, started_at, target_count
            FROM inspection_runs WHERE run_id = ?""", (run_id,),
        ).fetchone()
        if run is None:
            raise ValueError("巡检批次不存在")
        counts = connection.execute(
            """SELECT
                SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END),
                SUM(CASE WHEN status = 'down' THEN 1 ELSE 0 END),
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END)
            FROM inspection_items WHERE run_id = ?""", (run_id,),
        ).fetchone()
        up, down, pending = (int(value or 0) for value in counts)
        total = int(run[2])
        completed = up + down
        status = "finished" if completed >= total else "running"
        finished_at = self._timestamp() if status == "finished" else None
        connection.execute(
            "UPDATE inspection_runs SET status = ?, finished_at = ? WHERE run_id = ?",
            (status, finished_at, run_id),
        )
        return {
            "run_id": run_id,
            "inspection_type": run[0],
            "started_at": run[1],
            "finished_at": finished_at,
            "status": status,
            "total": total,
            "completed": completed,
            "up": up,
            "down": down,
            "pending": pending,
        }

    def get_inspection_run(self, run_id: str) -> dict[str, Any] | None:
        """Read a run summary without mutating it."""
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT r.run_id, r.inspection_type, r.started_at, r.finished_at, r.status, r.target_count,
                    SUM(CASE WHEN i.status = 'up' THEN 1 ELSE 0 END) AS up_count,
                    SUM(CASE WHEN i.status = 'down' THEN 1 ELSE 0 END) AS down_count,
                    SUM(CASE WHEN i.status = 'pending' THEN 1 ELSE 0 END) AS pending_count
                FROM inspection_runs AS r
                LEFT JOIN inspection_items AS i ON i.run_id = r.run_id
                WHERE r.run_id = ?
                GROUP BY r.run_id""",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            total, up, down, pending = int(row[5]), int(row[6] or 0), int(row[7] or 0), int(row[8] or 0)
            return {
                "run_id": row[0], "inspection_type": row[1], "started_at": row[2], "finished_at": row[3],
                "status": row[4], "total": total, "completed": up + down, "up": up, "down": down, "pending": pending,
            }
        finally:
            connection.close()

    def get_latest_inspection_run(self, on_date: date | None = None) -> dict[str, Any] | None:
        """Return today's most recently started website checklist, if any."""
        connection = self._connect()
        try:
            query = "SELECT run_id FROM inspection_runs WHERE inspection_type = ?"
            parameters: list[Any] = ["website"]
            if on_date is not None:
                start = datetime.combine(on_date, datetime.min.time()).isoformat(timespec="seconds")
                end = datetime.combine(on_date + timedelta(days=1), datetime.min.time()).isoformat(timespec="seconds")
                query += " AND started_at >= ? AND started_at < ?"
                parameters.extend([start, end])
            query += " ORDER BY started_at DESC LIMIT 1"
            row = connection.execute(query, parameters).fetchone()
        finally:
            connection.close()
        return self.get_inspection_run(str(row[0])) if row else None

    def get_inspection_failures(self, run_id: str) -> list[dict[str, Any]]:
        """Return the targets currently failing in a persisted checklist."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT target_key, target_name, target_addr, check_type, error_msg, detail, check_time, attempts
                FROM inspection_items
                WHERE run_id = ? AND status = 'down'
                ORDER BY check_time ASC, target_name ASC""",
                (run_id,),
            ).fetchall()
            return [
                {
                    "target_key": row[0], "target_name": row[1], "target_addr": row[2], "check_type": row[3],
                    "error_msg": row[4] or "", "detail": row[5] or "", "check_time": row[6] or "", "attempts": int(row[7]),
                }
                for row in rows
            ]
        finally:
            connection.close()

    def prepare_inspection_retry(self, run_id: str, target_keys: list[str]) -> int:
        """Put only the failed checklist items back into the pending state."""
        if not target_keys:
            return 0
        placeholders = ", ".join("?" for _ in target_keys)
        connection = self._connect()
        try:
            cursor = connection.execute(
                f"""UPDATE inspection_items
                SET status = 'pending', latency = NULL, error_msg = '', detail = '', check_time = NULL
                WHERE run_id = ? AND status = 'down' AND target_key IN ({placeholders})""",
                [run_id, *target_keys],
            )
            retried = cursor.rowcount
            if retried:
                connection.execute("UPDATE inspection_runs SET status = 'running', finished_at = NULL WHERE run_id = ?", (run_id,))
                self._recalculate_inspection_run(connection, run_id)
            connection.commit()
            return retried
        finally:
            connection.close()

    def inspection_export_rows(self, run_id: str) -> list[tuple[Any, ...]]:
        """Return one row per checklist item for the current daily report export."""
        connection = self._connect()
        try:
            return connection.execute(
                """SELECT target_name, target_addr, check_type, status, latency, error_msg, check_time, detail, attempts
                FROM inspection_items WHERE run_id = ? ORDER BY target_name ASC""",
                (run_id,),
            ).fetchall()
        finally:
            connection.close()

    def get_dashboard_snapshot(self, targets: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
        """Build the dashboard view from saved results without new network I/O."""
        current_time = now or datetime.now()
        target_by_key = {self.target_key(target): target for target in targets}
        connection = self._connect()
        try:
            if target_by_key:
                target_filter = " OR ".join(
                    "(target_name = ? AND target_addr = ? AND check_type = ?)" for _target in target_by_key.values()
                )
                target_parameters = [
                    value
                    for target in target_by_key.values()
                    for value in (target["name"], target["address"], target["check_type"])
                ]
                latest_rows = connection.execute(
                    f"""WITH latest AS (
                        SELECT target_name, target_addr, check_type, MAX(id) AS id
                        FROM check_history
                        WHERE {target_filter}
                        GROUP BY target_name, target_addr, check_type
                    )
                    SELECT history.target_name, history.target_addr, history.check_type, history.status,
                        history.latency, history.error_msg, history.check_time, history.detail
                    FROM check_history AS history
                    JOIN latest ON latest.id = history.id""",
                    target_parameters,
                ).fetchall()
                history_by_key = {
                    f"{row[0]}|{row[1]}|{row[2]}": {
                        "status": row[3], "latency": row[4], "error_msg": row[5] or "", "check_time": row[6],
                        "detail": row[7] or "",
                    }
                    for row in latest_rows
                }
                heatmap_start = (
                    current_time.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
                )
                heatmap_end = heatmap_start + timedelta(hours=24)
                # One bounded aggregate powers both the original 24-hour
                # success-rate table and the wall's hourly history. This
                # avoids adding a second SQLite read to each GUI refresh.
                hourly_rows = connection.execute(
                    f"""SELECT target_name, target_addr, check_type,
                        substr(check_time, 1, 13) || ':00:00',
                        SUM(CASE WHEN status = 'up' THEN 1 ELSE 0 END),
                        SUM(CASE WHEN status = 'down' THEN 1 ELSE 0 END),
                        SUM(CASE WHEN status NOT IN ('up', 'down') THEN 1 ELSE 0 END),
                        COUNT(*), SUM(latency), COUNT(latency), MAX(check_time)
                    FROM check_history
                    WHERE check_time >= ? AND check_time < ? AND ({target_filter})
                    GROUP BY target_name, target_addr, check_type, substr(check_time, 1, 13)
                    ORDER BY MAX(check_time) DESC""",
                    [
                        heatmap_start.isoformat(timespec="seconds"),
                        heatmap_end.isoformat(timespec="seconds"),
                        *target_parameters,
                    ],
                ).fetchall()
            else:
                history_by_key = {}
                heatmap_start = current_time.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
                hourly_rows = []
        finally:
            connection.close()

        status_counts = {"total": len(targets), "up": 0, "down": 0, "unknown": 0}
        target_rows: list[dict[str, Any]] = []
        exceptions: list[dict[str, Any]] = []
        for key, target in target_by_key.items():
            history = history_by_key.get(key)
            stale = history is None or self.is_result_stale(target, history["check_time"], current_time)
            display_status = "unknown" if stale else str(history["status"])
            if display_status not in {"up", "down"}:
                display_status = "unknown"
            status_counts[display_status] += 1
            row = {
                "key": key, "name": target["name"], "address": target["address"], "check_type": target["check_type"],
                "status": display_status, "stale": stale,
                "latency": history.get("latency") if history else None,
                "error_msg": history.get("error_msg", "") if history else "",
                "check_time": history.get("check_time", "") if history else "",
                "detail": history.get("detail", "") if history else "",
            }
            target_rows.append(row)
            if display_status == "down":
                exceptions.append(row)

        trend_totals: dict[str, dict[str, Any]] = {}
        hourly_buckets: list[dict[str, Any]] = []
        for row in hourly_rows:
            key = f"{row[0]}|{row[1]}|{row[2]}"
            if key not in target_by_key:
                continue
            up_count, down_count, other_count, total_count = (int(value or 0) for value in row[4:8])
            latency_sum, latency_count = row[8], int(row[9] or 0)
            last_check_time = str(row[10] or "")
            hourly_buckets.append({
                "key": key, "hour_start": str(row[3]), "up": up_count, "down": down_count,
                "other": other_count, "total": total_count, "last_check_time": last_check_time,
            })
            aggregate = trend_totals.setdefault(
                key,
                {
                    "key": key, "name": row[0], "address": row[1], "check_type": row[2],
                    "up": 0, "total": 0, "latency_sum": 0.0, "latency_count": 0, "last_check_time": "",
                },
            )
            aggregate["up"] += up_count
            aggregate["total"] += total_count
            if latency_sum is not None:
                aggregate["latency_sum"] += float(latency_sum)
            aggregate["latency_count"] += latency_count
            aggregate["last_check_time"] = max(str(aggregate["last_check_time"]), last_check_time)
        trends = [
            {
                "key": value["key"], "name": value["name"], "address": value["address"],
                "check_type": value["check_type"], "up": value["up"], "total": value["total"],
                "avg_latency": (
                    value["latency_sum"] / value["latency_count"] if value["latency_count"] else None
                ),
                "last_check_time": value["last_check_time"],
            }
            for value in sorted(
                trend_totals.values(), key=lambda item: str(item["last_check_time"]), reverse=True,
            )
        ]
        hourly_hours = [heatmap_start + timedelta(hours=offset) for offset in range(24)]
        hourly_history = {
            "bucket_minutes": 60,
            "window_start": heatmap_start.isoformat(timespec="seconds"),
            "window_end_exclusive": (heatmap_start + timedelta(hours=24)).isoformat(timespec="seconds"),
            "hours": [hour.isoformat(timespec="seconds") for hour in hourly_hours],
            "buckets": hourly_buckets,
        }
        latest_run = self.get_latest_inspection_run(on_date=current_time.date())
        if latest_run is None:
            expected = sum(
                1 for target in targets
                if str(target.get("check_type", "")).lower() == "http" and target.get("enabled", True)
            )
            latest_run = {
                "run_id": None, "inspection_type": "website", "started_at": "", "finished_at": None,
                "status": "not_started", "total": expected, "completed": 0, "up": 0, "down": 0, "pending": expected,
            }
        return {
            "generated_at": current_time.isoformat(timespec="seconds"), "status_counts": status_counts,
            "targets": target_rows, "exceptions": sorted(exceptions, key=lambda item: item["check_time"], reverse=True),
            "trends": trends, "hourly_history": hourly_history, "today_inspection": latest_run,
        }

    @staticmethod
    def is_result_stale(target: dict[str, Any], check_time: str, now: datetime) -> bool:
        """Return whether a result is too old to represent a target's current state."""
        try:
            result_time = datetime.fromisoformat(check_time)
            if result_time.tzinfo is not None:
                result_time = result_time.replace(tzinfo=None)
        except (TypeError, ValueError):
            return True
        interval_seconds = max(1, int(target.get("interval", 60)))
        return now - result_time > timedelta(seconds=max(120, interval_seconds * 2))

    # Keep the former private spelling available to any third-party local
    # automation while the GUI uses the explicit public helper above.
    _is_stale_result = is_result_stale

    def get_first_failures(self, on_date: date | None = None) -> dict[str, str]:
        """Return target_key → first failure ISO timestamp for each target that failed on the given date."""
        target_date = on_date or date.today()
        start = datetime.combine(target_date, datetime.min.time()).isoformat(timespec="seconds")
        end = datetime.combine(target_date + timedelta(days=1), datetime.min.time()).isoformat(timespec="seconds")
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT target_name, target_addr, check_type, MIN(check_time)
                FROM check_history
                WHERE status = 'down' AND check_time >= ? AND check_time < ?
                GROUP BY target_name, target_addr, check_type""",
                (start, end),
            ).fetchall()
            return {f"{row[0]}|{row[1]}|{row[2]}": str(row[3]) for row in rows}
        finally:
            connection.close()

    def cleanup_history(self, retention_days: int = 30) -> int:
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
        connection = self._connect()
        try:
            cursor = connection.execute("DELETE FROM check_history WHERE check_time < ?", (cutoff,))
            old_run_ids = [
                str(row[0]) for row in connection.execute(
                    "SELECT run_id FROM inspection_runs WHERE started_at < ?", (cutoff,)
                ).fetchall()
            ]
            removed_runs = 0
            if old_run_ids:
                placeholders = ", ".join("?" for _ in old_run_ids)
                connection.execute(f"DELETE FROM inspection_items WHERE run_id IN ({placeholders})", old_run_ids)
                removed_runs = connection.execute(
                    f"DELETE FROM inspection_runs WHERE run_id IN ({placeholders})", old_run_ids
                ).rowcount
            connection.commit()
            return cursor.rowcount + removed_runs
        finally:
            connection.close()

    def export_rows(self) -> list[tuple[Any, ...]]:
        connection = self._connect()
        try:
            return connection.execute(
                """SELECT target_name, target_addr, check_type, status, latency, error_msg, check_time, detail
                FROM check_history ORDER BY id DESC"""
            ).fetchall()
        finally:
            connection.close()
