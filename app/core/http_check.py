"""HTTP(S) checks used by the daily website-inspection workflow."""
from __future__ import annotations

import time
from typing import Any

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


def check(
    address: str,
    *,
    timeout_seconds: float | int | str = DEFAULT_TIMEOUT_SECONDS,
    expected_text: str = "",
    verify_tls: bool | str = True,
) -> dict[str, object]:
    """Check a web or device-login page without using or storing credentials.

    A successful 2xx/3xx response is considered reachable.  When ``expected_text``
    is configured, its case-insensitive presence in the returned HTML is also
    required; this distinguishes a real login/portal page from a proxy error page.
    """
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
        if not check_tls:
            detail += "；已忽略 HTTPS 证书校验"
        return {"status": "up", "latency": latency, "error_msg": "", "detail": detail}
    except requests.RequestException as error:
        return {"status": "down", "latency": None, "error_msg": str(error)}
