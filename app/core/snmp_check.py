"""SNMP-based deep health checks for switches and servers."""
from __future__ import annotations

import asyncio
import contextlib
import platform
import time
from typing import Any

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
    nextCmd,
)
from pysnmp.proto.rfc1905 import EndOfMibView, NoSuchInstance, NoSuchObject

SYS_DESCR_OID = "1.3.6.1.2.1.1.1.0"
SYS_NAME_OID = "1.3.6.1.2.1.1.5.0"

IF_OPER_STATUS_BASE = "1.3.6.1.2.1.2.2.1.8"

HR_PROCESSOR_LOAD_BASE = "1.3.6.1.2.1.25.3.3.1.2"
HR_STORAGE_TYPE_BASE = "1.3.6.1.2.1.25.2.3.1.2"
HR_STORAGE_SIZE_BASE = "1.3.6.1.2.1.25.2.3.1.5"
HR_STORAGE_USED_BASE = "1.3.6.1.2.1.25.2.3.1.6"
HR_STORAGE_RAM_TYPE = "1.3.6.1.2.1.25.2.1.2"

_MAX_WALK_STEPS = 512
_END_MARKERS = (EndOfMibView, NoSuchInstance, NoSuchObject)


@contextlib.contextmanager
def _safe_engine_construction():
    """pysnmp's bundled SNMP-FRAMEWORK-MIB computes a default SnmpEngineID from
    platform.node(); a non-ASCII Windows computer name (e.g. Chinese) makes
    ord(char) > 255 and crashes pyasn1's OctetString encoding the first time
    that MIB module loads, aborting SnmpEngine() entirely. The crash happens
    at MIB module load time, so passing our own snmpEngineID doesn't help --
    platform.node() must be substituted around construction instead."""
    original = platform.node
    platform.node = lambda: "network-monitor"
    try:
        yield
    finally:
        platform.node = original


def _redact(message: str, community: str) -> str:
    """Strip a plaintext community string out of error text before it can reach logs/alerts."""
    if community:
        message = message.replace(community, "<community 已隐藏>")
    return message


def _summarize_interfaces(statuses: list[int]) -> str:
    """Summarize an ifOperStatus walk. RFC1213 defines 7 states; only 1=up and
    2=down are broken out, the rest (testing/unknown/dormant/notPresent/
    lowerLayerDown) are grouped as "其他" rather than folded into down."""
    if not statuses:
        return "未获取到端口信息"
    up = sum(1 for status in statuses if status == 1)
    down = sum(1 for status in statuses if status == 2)
    other = len(statuses) - up - down
    text = f"端口 {len(statuses)} 个：{up} up，{down} down"
    return f"{text}，{other} 其他" if other else text


def _format_host_resources(cpu_percent: float | None, mem_percent: float | None) -> str:
    cpu_text = f"CPU {cpu_percent:.0f}%" if cpu_percent is not None else "CPU 使用率不可用"
    mem_text = f"内存 {mem_percent:.0f}%" if mem_percent is not None else "内存使用率不可用"
    return f"{cpu_text}，{mem_text}"


async def _walk_column(engine: Any, auth: Any, transport: Any, context: Any, base_oid: str) -> list[tuple[tuple[int, ...], Any]]:
    """GETNEXT-walk a single MIB column, returning (oid_tuple, value) pairs within base_oid's subtree."""
    base_tuple = tuple(int(part) for part in base_oid.split("."))
    results: list[tuple[tuple[int, ...], Any]] = []
    current = base_oid
    for _ in range(_MAX_WALK_STEPS):
        error_indication, error_status, _error_index, var_binds = await nextCmd(
            engine, auth, transport, context, ObjectType(ObjectIdentity(current)), lookupMib=False,
        )
        if error_indication or error_status or not var_binds:
            break
        oid, value = var_binds[0]
        oid_tuple = tuple(int(part) for part in oid.getOid())
        if oid_tuple[: len(base_tuple)] != base_tuple or isinstance(value, _END_MARKERS):
            break
        results.append((oid_tuple, value))
        current = ".".join(str(part) for part in oid_tuple)
    return results


async def _get_one(engine: Any, auth: Any, transport: Any, context: Any, oid: str) -> Any | None:
    error_indication, error_status, _error_index, var_binds = await getCmd(
        engine, auth, transport, context, ObjectType(ObjectIdentity(oid)), lookupMib=False,
    )
    if error_indication or error_status or not var_binds:
        return None
    _oid, value = var_binds[0]
    return None if isinstance(value, _END_MARKERS) else value


async def _fetch_cpu_percent(engine: Any, auth: Any, transport: Any, context: Any) -> float | None:
    loads = []
    for _oid_tuple, value in await _walk_column(engine, auth, transport, context, HR_PROCESSOR_LOAD_BASE):
        with contextlib.suppress(ValueError, TypeError):
            parsed = int(value)
            if parsed >= 0:  # hrProcessorLoad may report -1 for "no valid reading"
                loads.append(parsed)
    return sum(loads) / len(loads) if loads else None


async def _fetch_memory_percent(engine: Any, auth: Any, transport: Any, context: Any) -> float | None:
    ram_index = None
    for oid_tuple, value in await _walk_column(engine, auth, transport, context, HR_STORAGE_TYPE_BASE):
        if value.prettyPrint() == HR_STORAGE_RAM_TYPE:
            ram_index = oid_tuple[-1]
            break
    if ram_index is None:
        return None
    size = await _get_one(engine, auth, transport, context, f"{HR_STORAGE_SIZE_BASE}.{ram_index}")
    used = await _get_one(engine, auth, transport, context, f"{HR_STORAGE_USED_BASE}.{ram_index}")
    if size is None or used is None:
        return None
    with contextlib.suppress(ValueError, TypeError):
        size_value, used_value = int(size), int(used)
        if size_value > 0:
            return used_value / size_value * 100
    return None


async def _check_async(
    address: str, port: int, version: str, community: str, device_role: str, timeout: float,
) -> dict[str, object]:
    with _safe_engine_construction():
        engine = SnmpEngine()
    try:
        auth = CommunityData(community, mpModel=0 if version.lower() == "v1" else 1)
        transport = UdpTransportTarget((address, port), timeout=timeout, retries=1)
        context = ContextData()

        start = time.perf_counter()
        error_indication, error_status, _error_index, var_binds = await getCmd(
            engine, auth, transport, context,
            ObjectType(ObjectIdentity(SYS_DESCR_OID)), ObjectType(ObjectIdentity(SYS_NAME_OID)),
            lookupMib=False,
        )
        if error_indication:
            return {"status": "down", "latency": None, "error_msg": _redact(str(error_indication), community), "detail": ""}
        if error_status:
            return {"status": "down", "latency": None, "error_msg": _redact(error_status.prettyPrint(), community), "detail": ""}
        latency = round((time.perf_counter() - start) * 1000, 2)

        sys_name = ""
        if len(var_binds) > 1 and not isinstance(var_binds[1][1], _END_MARKERS):
            sys_name = var_binds[1][1].prettyPrint()
        detail_parts = [f"设备名：{sys_name}"] if sys_name else []

        if device_role == "switch":
            statuses: list[int] = []
            for _oid_tuple, value in await _walk_column(engine, auth, transport, context, IF_OPER_STATUS_BASE):
                with contextlib.suppress(ValueError, TypeError):
                    statuses.append(int(value))
            detail_parts.append(_summarize_interfaces(statuses))
        elif device_role == "server":
            cpu_percent = await _fetch_cpu_percent(engine, auth, transport, context)
            mem_percent = await _fetch_memory_percent(engine, auth, transport, context)
            detail_parts.append(_format_host_resources(cpu_percent, mem_percent))

        return {"status": "up", "latency": latency, "error_msg": "", "detail": "，".join(detail_parts)}
    except Exception as error:  # keep one bad target from ever taking down the scheduler loop
        return {"status": "down", "latency": None, "error_msg": _redact(str(error), community), "detail": ""}
    finally:
        engine.closeDispatcher()


def check(
    address: str, *, port: int = 161, version: str = "v2c", community: str = "public",
    device_role: str = "other", timeout: float = 3.0,
) -> dict[str, object]:
    """Run one SNMP v1/v2c health check. Safe to call from a plain worker
    thread: asyncio.run() opens and tears down its own event loop per call,
    which is fine at check-interval cadence (seconds to minutes apart)."""
    try:
        return asyncio.run(_check_async(address, port, version, community, device_role, timeout))
    except Exception as error:  # pragma: no cover - defense in depth around asyncio.run itself
        return {"status": "down", "latency": None, "error_msg": _redact(str(error), community), "detail": ""}
