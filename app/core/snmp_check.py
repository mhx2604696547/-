"""SNMP-based deep health checks, device inspection, and subnet discovery."""
from __future__ import annotations

import asyncio
import contextlib
import platform
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
SYS_UPTIME_OID = "1.3.6.1.2.1.1.3.0"
SYS_NAME_OID = "1.3.6.1.2.1.1.5.0"

IF_DESCR_BASE = "1.3.6.1.2.1.2.2.1.2"
IF_SPEED_BASE = "1.3.6.1.2.1.2.2.1.5"
IF_ADMIN_STATUS_BASE = "1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS_BASE = "1.3.6.1.2.1.2.2.1.8"
IF_IN_OCTETS_BASE = "1.3.6.1.2.1.2.2.1.10"
IF_OUT_OCTETS_BASE = "1.3.6.1.2.1.2.2.1.16"

HR_PROCESSOR_LOAD_BASE = "1.3.6.1.2.1.25.3.3.1.2"
HR_STORAGE_TYPE_BASE = "1.3.6.1.2.1.25.2.3.1.2"
HR_STORAGE_DESCR_BASE = "1.3.6.1.2.1.25.2.3.1.3"
HR_STORAGE_UNITS_BASE = "1.3.6.1.2.1.25.2.3.1.4"
HR_STORAGE_SIZE_BASE = "1.3.6.1.2.1.25.2.3.1.5"
HR_STORAGE_USED_BASE = "1.3.6.1.2.1.25.2.3.1.6"
HR_STORAGE_RAM_TYPE = "1.3.6.1.2.1.25.2.1.2"
HR_STORAGE_FIXED_DISK_TYPE = "1.3.6.1.2.1.25.2.1.4"

_MAX_WALK_STEPS = 512
_END_MARKERS = (EndOfMibView, NoSuchInstance, NoSuchObject)
_IF_STATUS_LABELS = {
    1: "UP",
    2: "DOWN",
    3: "TESTING",
    4: "UNKNOWN",
    5: "DORMANT",
    6: "NOT_PRESENT",
    7: "LOWER_DOWN",
}


@contextlib.contextmanager
def _safe_engine_construction():
    """Avoid pysnmp crash when Windows computer name contains non-ASCII chars."""
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


def format_uptime(timeticks: int | None) -> str:
    """Convert SNMP TimeTicks (1/100 s) into a compact Chinese uptime string."""
    if timeticks is None or timeticks < 0:
        return ""
    total_seconds = int(timeticks) // 100
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}天{hours}小时"
    if hours > 0:
        return f"{hours}小时{minutes}分"
    return f"{minutes}分"


def format_speed(bps: int | None) -> str:
    """Format ifSpeed (bits/sec) into a human-readable link speed."""
    if bps is None or bps <= 0:
        return "-"
    if bps >= 1_000_000_000:
        gbps = bps / 1_000_000_000
        return f"{gbps:.0f}G" if gbps.is_integer() else f"{gbps:.1f}G"
    if bps >= 1_000_000:
        return f"{bps // 1_000_000}M"
    if bps >= 1_000:
        return f"{bps // 1_000}K"
    return f"{bps}bps"


def compute_rate_mbps(prev_octets: int | None, curr_octets: int | None, elapsed_seconds: float) -> float | None:
    """Compute interface throughput in Mbps from two 32-bit octet counter samples."""
    if prev_octets is None or curr_octets is None or elapsed_seconds <= 0:
        return None
    delta = curr_octets - prev_octets
    if delta < 0:
        delta += 2 ** 32  # Handle 32-bit counter wraparound
    return round((delta * 8) / (elapsed_seconds * 1_000_000), 2)


def guess_device_role(sys_descr: str, sys_name: str = "") -> str:
    """Heuristically classify a discovered SNMP host as switch, server, or other."""
    haystack = f"{sys_descr} {sys_name}".lower()
    switch_keywords = (
        "switch", "cisco ios", "nx-os", "huawei", "vrp", "h3c", "comware",
        "ruijie", "juniper", "arista", "mikrotik", "fortigate", "firewall", "router",
    )
    if any(word in haystack for word in switch_keywords):
        return "switch"
    server_keywords = (
        "linux", "windows", "ubuntu", "centos", "debian", "red hat", "vmware", "esxi", "freebsd", "synology",
    )
    if any(word in haystack for word in server_keywords):
        return "server"
    return "other"


def _summarize_interfaces(statuses: list[int]) -> str:
    """Summarize an ifOperStatus walk into a concise label."""
    if not statuses:
        return "未获取到端口信息"
    up = sum(1 for status in statuses if status == 1)
    down = sum(1 for status in statuses if status == 2)
    other = len(statuses) - up - down
    text = f"端口 {len(statuses)} 个：{up} up，{down} down"
    return f"{text}，{other} 其他" if other else text


def _format_host_resources(
    cpu_percent: float | None, mem_percent: float | None, disk_percent: float | None = None,
) -> str:
    cpu_text = f"CPU {cpu_percent:.0f}%" if cpu_percent is not None else "CPU 使用率不可用"
    mem_text = f"内存 {mem_percent:.0f}%" if mem_percent is not None else "内存使用率不可用"
    base = f"{cpu_text}，{mem_text}"
    if disk_percent is not None:
        return f"{base}，磁盘最高 {disk_percent:.0f}%"
    return base


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
            if parsed >= 0:
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


async def _fetch_disks(engine: Any, auth: Any, transport: Any, context: Any) -> list[dict[str, Any]]:
    """Fetch fixed-disk storage partitions via HOST-RESOURCES-MIB."""
    disk_indices: list[int] = []
    for oid_tuple, value in await _walk_column(engine, auth, transport, context, HR_STORAGE_TYPE_BASE):
        if value.prettyPrint() == HR_STORAGE_FIXED_DISK_TYPE:
            disk_indices.append(oid_tuple[-1])
    disks: list[dict[str, Any]] = []
    for idx in disk_indices[:16]:
        descr = await _get_one(engine, auth, transport, context, f"{HR_STORAGE_DESCR_BASE}.{idx}")
        units = await _get_one(engine, auth, transport, context, f"{HR_STORAGE_UNITS_BASE}.{idx}")
        size = await _get_one(engine, auth, transport, context, f"{HR_STORAGE_SIZE_BASE}.{idx}")
        used = await _get_one(engine, auth, transport, context, f"{HR_STORAGE_USED_BASE}.{idx}")
        if size is None or used is None:
            continue
        with contextlib.suppress(ValueError, TypeError):
            size_v, used_v = int(size), int(used)
            unit_v = int(units) if units is not None else 0
            if size_v <= 0:
                continue
            pct = round(used_v / size_v * 100, 1)
            total_gb = round(size_v * unit_v / (1024 ** 3), 1) if unit_v > 0 else None
            used_gb = round(used_v * unit_v / (1024 ** 3), 1) if unit_v > 0 else None
            disks.append({
                "name": descr.prettyPrint() if descr is not None else f"Disk-{idx}",
                "percent": pct,
                "used_gb": used_gb,
                "total_gb": total_gb,
            })
    return disks


async def _fetch_interface_table(
    engine: Any, auth: Any, transport: Any, context: Any, *, sample_bandwidth: bool = False,
) -> list[dict[str, Any]]:
    """Walk interface names, speeds, admin/oper statuses, and optional live bandwidth."""
    descrs = {oid[-1]: val.prettyPrint() for oid, val in await _walk_column(engine, auth, transport, context, IF_DESCR_BASE)}
    oper_raw = await _walk_column(engine, auth, transport, context, IF_OPER_STATUS_BASE)
    admin_raw = {oid[-1]: val for oid, val in await _walk_column(engine, auth, transport, context, IF_ADMIN_STATUS_BASE)}
    speed_raw = {oid[-1]: val for oid, val in await _walk_column(engine, auth, transport, context, IF_SPEED_BASE)}

    in_rates: dict[int, float | None] = {}
    out_rates: dict[int, float | None] = {}
    if sample_bandwidth and oper_raw:
        t1 = time.perf_counter()
        in1 = {oid[-1]: int(val) for oid, val in await _walk_column(engine, auth, transport, context, IF_IN_OCTETS_BASE) if str(val).isdigit()}
        out1 = {oid[-1]: int(val) for oid, val in await _walk_column(engine, auth, transport, context, IF_OUT_OCTETS_BASE) if str(val).isdigit()}
        if in1 or out1:
            await asyncio.sleep(1.5)
            elapsed = time.perf_counter() - t1
            in2 = {oid[-1]: int(val) for oid, val in await _walk_column(engine, auth, transport, context, IF_IN_OCTETS_BASE) if str(val).isdigit()}
            out2 = {oid[-1]: int(val) for oid, val in await _walk_column(engine, auth, transport, context, IF_OUT_OCTETS_BASE) if str(val).isdigit()}
            for idx in set(in1) & set(in2):
                in_rates[idx] = compute_rate_mbps(in1[idx], in2[idx], elapsed)
            for idx in set(out1) & set(out2):
                out_rates[idx] = compute_rate_mbps(out1[idx], out2[idx], elapsed)

    interfaces: list[dict[str, Any]] = []
    for oid_tuple, oper_val in oper_raw:
        idx = oid_tuple[-1]
        oper_int = 4
        with contextlib.suppress(ValueError, TypeError):
            oper_int = int(oper_val)
        admin_int = 1
        if idx in admin_raw:
            with contextlib.suppress(ValueError, TypeError):
                admin_int = int(admin_raw[idx])
        speed_int: int | None = None
        if idx in speed_raw:
            with contextlib.suppress(ValueError, TypeError):
                speed_int = int(speed_raw[idx])
        interfaces.append({
            "index": idx,
            "name": descrs.get(idx, f"if-{idx}"),
            "admin_status": _IF_STATUS_LABELS.get(admin_int, str(admin_int)),
            "oper_status": _IF_STATUS_LABELS.get(oper_int, str(oper_int)),
            "oper_code": oper_int,
            "speed": format_speed(speed_int),
            "in_mbps": in_rates.get(idx),
            "out_mbps": out_rates.get(idx),
        })
    return interfaces


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
            ObjectType(ObjectIdentity(SYS_DESCR_OID)),
            ObjectType(ObjectIdentity(SYS_NAME_OID)),
            ObjectType(ObjectIdentity(SYS_UPTIME_OID)),
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
        uptime_text = ""
        if len(var_binds) > 2 and not isinstance(var_binds[2][1], _END_MARKERS):
            with contextlib.suppress(ValueError, TypeError):
                uptime_text = format_uptime(int(var_binds[2][1]))

        detail_parts = [f"设备名：{sys_name}"] if sys_name else []
        if uptime_text:
            detail_parts.append(f"运行 {uptime_text}")

        if device_role == "switch":
            statuses: list[int] = []
            for _oid_tuple, value in await _walk_column(engine, auth, transport, context, IF_OPER_STATUS_BASE):
                with contextlib.suppress(ValueError, TypeError):
                    statuses.append(int(value))
            detail_parts.append(_summarize_interfaces(statuses))
        elif device_role == "server":
            cpu_percent = await _fetch_cpu_percent(engine, auth, transport, context)
            mem_percent = await _fetch_memory_percent(engine, auth, transport, context)
            disks = await _fetch_disks(engine, auth, transport, context)
            max_disk = max((float(d["percent"]) for d in disks), default=None)
            detail_parts.append(_format_host_resources(cpu_percent, mem_percent, max_disk))

        return {"status": "up", "latency": latency, "error_msg": "", "detail": "，".join(detail_parts)}
    except Exception as error:
        return {"status": "down", "latency": None, "error_msg": _redact(str(error), community), "detail": ""}
    finally:
        engine.closeDispatcher()


async def _inspect_device_async(
    address: str, port: int, version: str, community: str, device_role: str, timeout: float,
) -> dict[str, Any]:
    """Perform a rich on-demand SNMP inspection returning structured device state."""
    with _safe_engine_construction():
        engine = SnmpEngine()
    try:
        auth = CommunityData(community, mpModel=0 if version.lower() == "v1" else 1)
        transport = UdpTransportTarget((address, port), timeout=timeout, retries=1)
        context = ContextData()

        start = time.perf_counter()
        error_indication, error_status, _error_index, var_binds = await getCmd(
            engine, auth, transport, context,
            ObjectType(ObjectIdentity(SYS_DESCR_OID)),
            ObjectType(ObjectIdentity(SYS_NAME_OID)),
            ObjectType(ObjectIdentity(SYS_UPTIME_OID)),
            lookupMib=False,
        )
        if error_indication:
            return {"ok": False, "error_msg": _redact(str(error_indication), community)}
        if error_status:
            return {"ok": False, "error_msg": _redact(error_status.prettyPrint(), community)}
        latency = round((time.perf_counter() - start) * 1000, 2)

        sys_descr = var_binds[0][1].prettyPrint() if len(var_binds) > 0 and not isinstance(var_binds[0][1], _END_MARKERS) else ""
        sys_name = var_binds[1][1].prettyPrint() if len(var_binds) > 1 and not isinstance(var_binds[1][1], _END_MARKERS) else ""
        uptime_text = ""
        if len(var_binds) > 2 and not isinstance(var_binds[2][1], _END_MARKERS):
            with contextlib.suppress(ValueError, TypeError):
                uptime_text = format_uptime(int(var_binds[2][1]))

        data: dict[str, Any] = {
            "ok": True,
            "address": address,
            "latency": latency,
            "sys_name": sys_name,
            "sys_descr": sys_descr,
            "uptime": uptime_text or "-",
            "device_role": device_role,
            "interfaces": [],
            "cpu_percent": None,
            "mem_percent": None,
            "disks": [],
        }
        if device_role in ("switch", "other"):
            data["interfaces"] = await _fetch_interface_table(engine, auth, transport, context, sample_bandwidth=True)
        if device_role in ("server", "other"):
            data["cpu_percent"] = await _fetch_cpu_percent(engine, auth, transport, context)
            data["mem_percent"] = await _fetch_memory_percent(engine, auth, transport, context)
            data["disks"] = await _fetch_disks(engine, auth, transport, context)
        return data
    except Exception as error:
        return {"ok": False, "error_msg": _redact(str(error), community)}
    finally:
        engine.closeDispatcher()


def check(
    address: str, *, port: int = 161, version: str = "v2c", community: str = "public",
    device_role: str = "other", timeout: float = 3.0,
) -> dict[str, object]:
    """Run one SNMP v1/v2c health check."""
    try:
        return asyncio.run(_check_async(address, port, version, community, device_role, timeout))
    except Exception as error:  # pragma: no cover
        return {"status": "down", "latency": None, "error_msg": _redact(str(error), community), "detail": ""}


def inspect_device(
    address: str, *, port: int = 161, version: str = "v2c", community: str = "public",
    device_role: str = "other", timeout: float = 3.0,
) -> dict[str, Any]:
    """Run an on-demand detailed SNMP inspection for the GUI detail dialog."""
    try:
        return asyncio.run(_inspect_device_async(address, port, version, community, device_role, timeout))
    except Exception as error:  # pragma: no cover
        return {"ok": False, "error_msg": _redact(str(error), community)}


def _probe_discovery_host(
    address: str, port: int, version: str, community: str, timeout: float,
) -> dict[str, Any] | None:
    """Quick single-GET SNMP probe used during subnet discovery."""
    async def _run() -> dict[str, Any] | None:
        with _safe_engine_construction():
            engine = SnmpEngine()
        try:
            auth = CommunityData(community, mpModel=0 if version.lower() == "v1" else 1)
            transport = UdpTransportTarget((address, port), timeout=timeout, retries=0)
            context = ContextData()
            start = time.perf_counter()
            err_ind, err_stat, _idx, var_binds = await getCmd(
                engine, auth, transport, context,
                ObjectType(ObjectIdentity(SYS_DESCR_OID)),
                ObjectType(ObjectIdentity(SYS_NAME_OID)),
                ObjectType(ObjectIdentity(SYS_UPTIME_OID)),
                lookupMib=False,
            )
            if err_ind or err_stat or not var_binds:
                return None
            latency = round((time.perf_counter() - start) * 1000, 1)
            sys_descr = var_binds[0][1].prettyPrint() if len(var_binds) > 0 and not isinstance(var_binds[0][1], _END_MARKERS) else ""
            sys_name = var_binds[1][1].prettyPrint() if len(var_binds) > 1 and not isinstance(var_binds[1][1], _END_MARKERS) else ""
            uptime_text = ""
            if len(var_binds) > 2 and not isinstance(var_binds[2][1], _END_MARKERS):
                with contextlib.suppress(ValueError, TypeError):
                    uptime_text = format_uptime(int(var_binds[2][1]))
            return {
                "address": address,
                "sys_name": sys_name or address,
                "sys_descr": sys_descr,
                "uptime": uptime_text or "-",
                "device_role": guess_device_role(sys_descr, sys_name),
                "latency": latency,
            }
        except Exception:
            return None
        finally:
            engine.closeDispatcher()

    try:
        return asyncio.run(_run())
    except Exception:
        return None


def discover_snmp_devices(
    addresses: list[str],
    *,
    port: int = 161,
    version: str = "v2c",
    community: str = "public",
    timeout: float = 1.0,
    stop_event: threading.Event | None = None,
    result_queue: "queue.Queue[dict[str, Any]] | None" = None,
    max_workers: int = 24,
) -> list[dict[str, Any]]:
    """Concurrently probe candidate IP addresses for SNMP-enabled devices."""
    found: list[dict[str, Any]] = []
    if not addresses:
        if result_queue is not None:
            result_queue.put({"done": True})
        return found

    def worker(addr: str) -> dict[str, Any] | None:
        if stop_event and stop_event.is_set():
            return None
        res = _probe_discovery_host(addr, port, version, community, timeout)
        if result_queue is not None:
            result_queue.put({"probed": addr, "device": res})
        return res

    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(addresses))), thread_name_prefix="snmp-discover") as pool:
        futures = [pool.submit(worker, addr) for addr in addresses]
        for fut in futures:
            if stop_event and stop_event.is_set():
                break
            item = fut.result()
            if item is not None:
                found.append(item)

    if result_queue is not None:
        result_queue.put({"done": True})
    return found
