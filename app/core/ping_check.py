"""Cross-platform ping connectivity check."""
from __future__ import annotations

import platform
import re
import subprocess
import time

# On Windows, ping.exe is itself a console app; without this flag it flashes
# a black console window on every single check even though the GUI app has
# no console (build.spec sets console=False).
_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

_MAX_COUNT = 10
_MIN_TIMEOUT_MS = 100
_MAX_TIMEOUT_MS = 10000

# Windows summary line, e.g. "Average = 12ms" / "平均 = 12ms".
_SUMMARY_AVERAGE_PATTERN = re.compile(r"(?:Average|平均)\s*=\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
# POSIX summary line, e.g. "rtt min/avg/max/mdev = 0.023/0.030/0.041/0.007 ms" — 2nd number is the average.
_POSIX_RTT_PATTERN = re.compile(r"=\s*[\d.]+/([\d.]+)/[\d.]+")
# Per-packet reply line, used as a fallback when no summary block is present.
_PER_PACKET_TIME_PATTERN = re.compile(r"(?:time|时间)[=<]\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
_WINDOWS_LOSS_PATTERN = re.compile(r"(\d+)%\s*(?:loss|丢失)", re.IGNORECASE)
_POSIX_LOSS_PATTERN = re.compile(r"(\d+)%\s*packet loss", re.IGNORECASE)


def check(address: str, *, count: int = 1, timeout_ms: int = 2000) -> dict[str, object]:
    """Ping an address, sending `count` packets with a `timeout_ms` per-packet timeout.

    Windows and POSIX ping both exit 0 only when at least one reply was
    received, so the existing returncode check already gives "reachable if
    any packet got through" semantics even when count > 1 — no extra
    packet-loss threshold logic is needed to decide status.
    """
    count = max(1, min(_MAX_COUNT, int(count)))
    timeout_ms = max(_MIN_TIMEOUT_MS, min(_MAX_TIMEOUT_MS, int(timeout_ms)))
    if platform.system() == "Windows":
        command = ["ping", "-n", str(count), "-w", str(timeout_ms), address]
    else:
        command = ["ping", "-c", str(count), "-W", str(max(1, round(timeout_ms / 1000))), address]
    # Worst case every packet times out back-to-back; add a fixed buffer for process startup/teardown.
    process_timeout = (count * timeout_ms / 1000) + 3
    start = time.perf_counter()
    try:
        # errors="replace": on a codepage/encoding mismatch (e.g. the OS console
        # page differs from Python's assumed text encoding), decoding must not
        # raise inside subprocess's internal reader thread, which would silently
        # drop the captured output instead of surfacing a decode error here.
        completed = subprocess.run(
            command, capture_output=True, text=True, errors="replace", timeout=process_timeout, check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return {"status": "down", "latency": None, "error_msg": "系统未找到 ping 命令"}
    except subprocess.TimeoutExpired:
        return {"status": "down", "latency": None, "error_msg": "Ping 命令执行超时"}

    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        return {"status": "down", "latency": None, "error_msg": output.strip()[-300:] or "Ping 失败"}

    latency = _extract_latency(output, start)
    detail = _extract_loss_detail(output, count) if count > 1 else ""
    return {"status": "up", "latency": latency, "error_msg": "", "detail": detail}


def _extract_latency(output: str, start: float) -> float:
    """Prefer the summary average (covers multi-packet runs); fall back to a
    single reply line, then to wall-clock elapsed time as a last resort."""
    for pattern in (_SUMMARY_AVERAGE_PATTERN, _POSIX_RTT_PATTERN, _PER_PACKET_TIME_PATTERN):
        match = pattern.search(output)
        if match:
            return float(match.group(1))
    return round((time.perf_counter() - start) * 1000, 2)


def _extract_loss_detail(output: str, count: int) -> str:
    match = _WINDOWS_LOSS_PATTERN.search(output) or _POSIX_LOSS_PATTERN.search(output)
    if not match:
        return ""
    return f"发送 {count} 个包，丢包 {match.group(1)}%"
