"""HTTP(S) checks with optional expected-text matching and TLS certificate expiry detection."""
from __future__ import annotations

import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_TIMEOUT_SECONDS = 5.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 60.0
USER_AGENT = "NetworkMonitor/1.0"


def _timeout_seconds(value: Any) -> float:
    """Return a bounded per-target HTTP timeout without raising on bad JSON."""
    try:
        return min(MAX_TIMEOUT_SECONDS, max(MIN_TIMEOUT_SECONDS, float(value)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def _as_bool(value: Any, default: bool = True) -> bool:
    """Accept native JSON booleans and common hand-edited configuration values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off"}
    if value is None:
        return default
    return bool(value)


def parse_tls_not_after_days(not_after: str, now: datetime | None = None) -> int | None:
    """Parse an OpenSSL 'notAfter' timestamp string into whole days remaining."""
    if not not_after:
        return None
    try:
        expiry_ts = ssl.cert_time_to_seconds(not_after)
        expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
        current = now if now is not None else datetime.now(tz=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return int((expiry_dt - current).total_seconds() // 86400)
    except (ValueError, OSError):
        return None


def probe_tls_days_left(url: str, timeout: float = 3.0) -> int | None:
    """Best-effort TLS certificate expiry probe for HTTPS URLs. Never raises."""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    host = parsed.hostname
    port = parsed.port or 443
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=min(timeout, 3.0)) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
                cert = tls_sock.getpeercert()
        if not cert:
            return None
        return parse_tls_not_after_days(str(cert.get("notAfter") or ""))
    except Exception:
        return None


def check(
    address: str,
    *,
    timeout_seconds: float | int | str = DEFAULT_TIMEOUT_SECONDS,
    expected_text: str = "",
    verify_tls: bool | str = True,
) -> dict[str, object]:
    """Check a web or device-login page without using or storing credentials."""
    url = address if address.startswith(("http://", "https://")) else f"http://{address}"
    timeout = _timeout_seconds(timeout_seconds)
    marker = expected_text.strip()
    check_tls = _as_bool(verify_tls)
    try:
        start = time.perf_counter()
        response = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            verify=check_tls,
            headers={"User-Agent": USER_AGENT},
        )
        latency = round((time.perf_counter() - start) * 1000, 2)
        if not 200 <= response.status_code < 400:
            return {"status": "down", "latency": latency, "error_msg": f"HTTP 状态码 {response.status_code}"}
        if marker and marker.casefold() not in response.text.casefold():
            return {
                "status": "down",
                "latency": latency,
                "error_msg": "预期文本未找到，请检查是否跳转到异常页面或登录页已变更",
            }
        detail = f"HTTP {response.status_code}"
        if marker:
            detail += "；页面标识已匹配"
        days_left: int | None = None
        if not check_tls:
            detail += "；已忽略 HTTPS 证书校验"
        elif url.lower().startswith("https://"):
            days_left = probe_tls_days_left(url, timeout=timeout)
            if days_left is not None:
                detail += f"；证书剩 {days_left} 天"
        result: dict[str, object] = {"status": "up", "latency": latency, "error_msg": "", "detail": detail}
        if days_left is not None:
            result["tls_days_left"] = days_left
        return result
    except requests.RequestException as error:
        return {"status": "down", "latency": None, "error_msg": str(error)}
