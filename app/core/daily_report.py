"""Build the daily WeCom inspection summary report."""
from __future__ import annotations

from datetime import datetime
from typing import Any

_MAX_EXCEPTIONS = 20
_WECOM_LIMIT = 4000


def build_daily_report(
    snapshot: dict[str, Any],
    first_failures: dict[str, str],
    settings: dict[str, Any],
    now: datetime | None = None,
) -> str:
    """Return a WeCom Markdown string summarising the day's monitoring state.

    Args:
        snapshot: result of database.get_dashboard_snapshot(targets)
        first_failures: map of target_key → ISO timestamp of first failure today
        settings: current application settings
        now: report generation time (defaults to datetime.now())
    """
    ts = now or datetime.now()
    date_str = ts.strftime("%Y-%m-%d")
    time_str = ts.strftime("%H:%M")

    counts = snapshot.get("status_counts", {})
    total = int(counts.get("total", 0))
    up = int(counts.get("up", 0))
    down = int(counts.get("down", 0))
    unknown = int(counts.get("unknown", 0))

    inspection = snapshot.get("today_inspection") or {}
    insp_total = int(inspection.get("total", 0))
    insp_up = int(inspection.get("up", 0))
    insp_down = int(inspection.get("down", 0))
    insp_status = inspection.get("status", "not_started")

    exceptions = snapshot.get("exceptions", [])

    # Header
    if down > 0:
        title_color = "warning"
        title_text = "每日巡检报告（有异常）"
    else:
        title_color = "info"
        title_text = "每日巡检报告（全部正常）"

    lines: list[str] = [
        f'# <font color="{title_color}">{title_text}</font>',
        f"> **日期**：{date_str}｜**统计截至**：{time_str}",
        f"> **监控目标**：{total} 个｜**正常**：{up}｜**异常**：{down}｜**未知/过期**：{unknown}",
    ]

    # Inspection summary
    if insp_status == "not_started":
        lines.append("> **网页巡检**：今日未执行")
    else:
        insp_label = "进行中" if insp_status == "running" else "已完成"
        lines.append(
            f"> **网页巡检**：{insp_label}｜应检 {insp_total}｜正常 {insp_up}｜异常 {insp_down}"
        )

    # Exception list
    if exceptions:
        lines.append("")
        lines.append("## 当前异常目标")
        shown = exceptions[:_MAX_EXCEPTIONS]
        for exc in shown:
            key = exc.get("key", "")
            name = exc.get("name", exc.get("target_name", ""))
            addr = exc.get("address", exc.get("target_addr", ""))
            ctype = str(exc.get("check_type", "")).upper()
            error = exc.get("error_msg", "") or ""
            first = first_failures.get(key, "")
            first_label = ""
            if first:
                try:
                    first_dt = datetime.fromisoformat(first)
                    first_label = f"｜首次发现：{first_dt.strftime('%H:%M')}"
                except ValueError:
                    pass
            lines.append(f"> ⚠ **{name}** ({ctype}) {addr}{first_label}")
            if error:
                lines.append(f"> 　错误：{error[:120]}")
        if len(exceptions) > _MAX_EXCEPTIONS:
            lines.append(f"> … 共 {len(exceptions)} 个异常，仅显示前 {_MAX_EXCEPTIONS} 个")
    else:
        lines.append("")
        lines.append("> ✅ 当前无异常目标")

    lines.append("")
    lines.append(f"*由网络监控自动生成于 {date_str} {time_str}*")

    report = "\n".join(lines)
    # Hard truncate to stay within WeCom's message size limit
    if len(report.encode("utf-8")) > _WECOM_LIMIT:
        report = report.encode("utf-8")[:_WECOM_LIMIT].decode("utf-8", errors="ignore")
        report += "\n…（内容已截断）"
    return report
