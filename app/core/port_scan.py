"""On-demand TCP port connectivity scanning for the manual diagnostic tool."""
from __future__ import annotations

import queue
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

MAX_PORTS = 1000


def parse_ports(spec: str) -> list[int]:
    """Parse "80,443,8000-8010" into a sorted list of unique ports."""
    ports: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, separator, end_text = chunk.partition("-")
            if not separator:
                raise ValueError(f"端口范围格式错误：{chunk}")
            try:
                start, end = int(start_text.strip()), int(end_text.strip())
            except ValueError:
                raise ValueError(f"端口范围必须是数字：{chunk}") from None
            if start > end:
                start, end = end, start
            ports.update(range(start, end + 1))
        else:
            try:
                ports.add(int(chunk))
            except ValueError:
                raise ValueError(f"端口必须是数字：{chunk}") from None
    if not ports:
        raise ValueError("请至少输入一个端口")
    if any(port < 1 or port > 65535 for port in ports):
        raise ValueError("端口必须在 1-65535 范围内")
    if len(ports) > MAX_PORTS:
        raise ValueError(f"单次最多测试 {MAX_PORTS} 个端口")
    return sorted(ports)


def check_port(host: str, port: int, timeout: float = 1.5) -> dict[str, Any]:
    """Attempt a single TCP three-way handshake against host:port."""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = round((time.perf_counter() - start) * 1000, 2)
        return {"port": port, "status": "open", "latency": latency, "error_msg": ""}
    except OSError as error:
        return {"port": port, "status": "closed", "latency": None, "error_msg": str(error)}


def scan_ports(
    host: str,
    ports: list[int],
    timeout: float,
    stop_event: threading.Event,
    result_queue: "queue.Queue[dict[str, Any]]",
    max_workers: int = 20,
) -> None:
    """Concurrently check each port, streaming results, then signal completion."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_port, host, port, timeout): port for port in ports}
        try:
            for future in as_completed(futures):
                if stop_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                result_queue.put(future.result())
        finally:
            result_queue.put({"done": True})
