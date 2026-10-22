"""TCP socket connectivity check."""
from __future__ import annotations

import socket
import time


def _parse_address(address: str) -> tuple[str, int]:
    """Parse host:port, including bracketed IPv6 addresses."""
    if address.startswith("["):
        host, separator, port = address[1:].partition("]:")
        if not separator:
            raise ValueError("TCP 地址格式应为 [IPv6]:端口")
    else:
        host, separator, port = address.rpartition(":")
        if not separator:
            raise ValueError("TCP 地址格式应为 主机:端口，例如 192.168.1.1:443")
    return host.strip(), int(port)


def check(address: str) -> dict[str, object]:
    """Attempt a TCP connection with a three-second timeout."""
    try:
        host, port = _parse_address(address)
        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=3):
            latency = round((time.perf_counter() - start) * 1000, 2)
        return {"status": "up", "latency": latency, "error_msg": ""}
    except (ValueError, OSError) as error:
        return {"status": "down", "latency": None, "error_msg": str(error)}
