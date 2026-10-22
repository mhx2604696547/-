"""Tests for SNMP-based switch/server health checks."""
from __future__ import annotations

import platform
import unittest
from unittest.mock import AsyncMock, patch

from app.core import snmp_check


class FakeOid:
    """Duck-typed stand-in for a resolved pysnmp ObjectIdentity."""

    def __init__(self, oid_tuple: tuple[int, ...]) -> None:
        self._oid_tuple = oid_tuple

    def getOid(self) -> tuple[int, ...]:
        return self._oid_tuple


class FakeValue:
    """Duck-typed stand-in for a resolved pysnmp value (Integer32/OctetString/...)."""

    def __init__(self, value: object, pretty: str | None = None) -> None:
        self._value = value
        self._pretty = pretty if pretty is not None else str(value)

    def __int__(self) -> int:
        return int(self._value)

    def prettyPrint(self) -> str:
        return self._pretty


class SummarizeInterfacesTests(unittest.TestCase):
    def test_all_up(self) -> None:
        self.assertEqual(snmp_check._summarize_interfaces([1, 1, 1]), "端口 3 个：3 up，0 down")

    def test_mixed_states_group_non_up_down_as_other(self) -> None:
        # 1=up, 2=down, 3=testing, 5=dormant -- only 1/2 get their own bucket.
        text = snmp_check._summarize_interfaces([1, 1, 2, 3, 5])
        self.assertEqual(text, "端口 5 个：2 up，1 down，2 其他")

    def test_empty_list_reports_unavailable(self) -> None:
        self.assertEqual(snmp_check._summarize_interfaces([]), "未获取到端口信息")


class FormatHostResourcesTests(unittest.TestCase):
    def test_both_available(self) -> None:
        self.assertEqual(snmp_check._format_host_resources(12.4, 45.6), "CPU 12%，内存 46%")

    def test_cpu_unavailable(self) -> None:
        self.assertEqual(snmp_check._format_host_resources(None, 50.0), "CPU 使用率不可用，内存 50%")

    def test_memory_unavailable(self) -> None:
        self.assertEqual(snmp_check._format_host_resources(10.0, None), "CPU 10%，内存使用率不可用")


class RedactTests(unittest.TestCase):
    def test_strips_plaintext_community(self) -> None:
        redacted = snmp_check._redact("auth failed for community=super-secret", "super-secret")
        self.assertNotIn("super-secret", redacted)

    def test_empty_community_is_a_no_op(self) -> None:
        self.assertEqual(snmp_check._redact("some error", ""), "some error")


class WalkColumnTests(unittest.IsolatedAsyncioTestCase):
    async def test_walk_stops_at_subtree_boundary(self) -> None:
        base = "1.3.6.1.2.1.2.2.1.8"
        responses = [
            (None, 0, 0, [(FakeOid((1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 1)), FakeValue(1))]),
            (None, 0, 0, [(FakeOid((1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 2)), FakeValue(2))]),
            # Next column entirely -- out of the ifOperStatus subtree, walk must stop here.
            (None, 0, 0, [(FakeOid((1, 3, 6, 1, 2, 1, 2, 2, 1, 9, 1)), FakeValue(42))]),
        ]
        mock_next_cmd = AsyncMock(side_effect=responses)
        with patch.object(snmp_check, "nextCmd", mock_next_cmd):
            results = await snmp_check._walk_column(object(), object(), object(), object(), base)
        self.assertEqual([int(value) for _oid, value in results], [1, 2])
        self.assertEqual(mock_next_cmd.await_count, 3)


class CheckTests(unittest.TestCase):
    def _base_success_response(self, sys_name: str = "SW-CORE-01"):
        return (
            None, 0, 0,
            [
                (FakeOid((1, 3, 6, 1, 2, 1, 1, 1, 0)), FakeValue("Linux switch", pretty="Linux switch")),
                (FakeOid((1, 3, 6, 1, 2, 1, 1, 5, 0)), FakeValue(sys_name, pretty=sys_name)),
            ],
        )

    def test_success_reports_up_with_sys_name_in_detail(self) -> None:
        with patch.object(snmp_check, "getCmd", AsyncMock(return_value=self._base_success_response())):
            result = snmp_check.check("192.0.2.1", device_role="other", community="public")
        self.assertEqual(result["status"], "up")
        self.assertIsNotNone(result["latency"])
        self.assertIn("SW-CORE-01", result["detail"])

    def test_timeout_reports_down(self) -> None:
        with patch.object(snmp_check, "getCmd", AsyncMock(return_value=("No SNMP response received before timeout", 0, 0, []))):
            result = snmp_check.check("192.0.2.1", device_role="other", community="public")
        self.assertEqual(result["status"], "down")
        self.assertIn("timeout", result["error_msg"])

    def test_failure_never_leaks_plaintext_community(self) -> None:
        secret = "s3cr3t-community"
        with patch.object(snmp_check, "getCmd", AsyncMock(side_effect=RuntimeError(f"auth rejected for community={secret}"))):
            result = snmp_check.check("192.0.2.1", device_role="other", community=secret)
        self.assertEqual(result["status"], "down")
        self.assertNotIn(secret, result["error_msg"])

    def test_switch_role_adds_interface_summary_to_detail(self) -> None:
        walk_responses = [
            (None, 0, 0, [(FakeOid((1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 1)), FakeValue(1))]),
            (None, 0, 0, [(FakeOid((1, 3, 6, 1, 2, 1, 2, 2, 1, 8, 2)), FakeValue(2))]),
            (None, 0, 0, [(FakeOid((1, 3, 6, 1, 2, 1, 2, 2, 1, 9, 1)), FakeValue(0))]),  # out of subtree
        ]
        with patch.object(snmp_check, "getCmd", AsyncMock(return_value=self._base_success_response())), \
             patch.object(snmp_check, "nextCmd", AsyncMock(side_effect=walk_responses)):
            result = snmp_check.check("192.0.2.1", device_role="switch", community="public")
        self.assertEqual(result["status"], "up")
        self.assertIn("1 up", result["detail"])
        self.assertIn("1 down", result["detail"])

    def test_server_role_adds_host_resource_summary_to_detail(self) -> None:
        with patch.object(snmp_check, "getCmd", AsyncMock(return_value=self._base_success_response())), \
             patch.object(snmp_check, "_fetch_cpu_percent", AsyncMock(return_value=75.0)), \
             patch.object(snmp_check, "_fetch_memory_percent", AsyncMock(return_value=40.0)):
            result = snmp_check.check("192.0.2.1", device_role="server", community="public")
        self.assertEqual(result["status"], "up")
        self.assertIn("CPU 75%", result["detail"])
        self.assertIn("内存 40%", result["detail"])


class NonAsciiHostnameRegressionTests(unittest.TestCase):
    def test_check_does_not_crash_when_computer_name_is_non_ascii(self) -> None:
        """Regression test: pysnmp's bundled SNMP-FRAMEWORK-MIB builds a default
        SnmpEngineID from platform.node(); an unpatched non-ASCII computer name
        (e.g. Chinese) makes ord(char) > 255 and previously crashed OctetString
        encoding the first time that MIB module loads, aborting SnmpEngine()
        entirely before any network I/O even happened."""
        original_node = platform.node
        platform.node = lambda: "运维监控主机"
        try:
            result = snmp_check.check("192.0.2.1", device_role="other", community="public", timeout=0.5)
        finally:
            platform.node = original_node
        self.assertIn(result["status"], ("up", "down"))  # must not raise


if __name__ == "__main__":
    unittest.main()
