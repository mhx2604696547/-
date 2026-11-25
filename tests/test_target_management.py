"""Unit tests for streamlined target management, batch import, and host helpers."""
from __future__ import annotations

import unittest

from app.gui.config_dialog import (
    DEFAULT_GROUP,
    extract_host,
    normalize_target,
    parse_batch_targets,
)


class HostExtractionTests(unittest.TestCase):
    def test_extracts_host_from_various_address_formats(self) -> None:
        self.assertEqual(extract_host("192.168.1.1"), "192.168.1.1")
        self.assertEqual(extract_host("192.168.1.10:443"), "192.168.1.10")
        self.assertEqual(extract_host("[2001:db8::1]:443"), "2001:db8::1")
        self.assertEqual(extract_host("https://portal.example.com:8443/login"), "portal.example.com")
        self.assertEqual(extract_host("http://10.0.0.1/status"), "10.0.0.1")
        self.assertEqual(extract_host(""), "")


class NormalizeTargetTests(unittest.TestCase):
    def test_fills_missing_group_and_enabled_defaults(self) -> None:
        legacy = {"name": "核心交换机", "address": "10.0.0.1", "check_type": "SNMP"}
        normalized = normalize_target(legacy)
        self.assertEqual(normalized["group"], DEFAULT_GROUP)
        self.assertTrue(normalized["enabled"])
        self.assertEqual(normalized["check_type"], "snmp")
        self.assertEqual(normalized["snmp_port"], 161)


class BatchImportParserTests(unittest.TestCase):
    def test_parses_mixed_lines_ranges_duplicates_and_errors(self) -> None:
        raw = "\n".join([
            "# comment line",
            "核心交换机,192.168.1.1,snmp",
            "文件服务器,192.168.1.10,tcp",
            "门户网站,https://portal.example.com,http",
            "备用网关,192.168.1.2",
            "192.168.1.20-22",
            "已存在目标,10.0.0.5,ping",
            "非法类型,1.2.3.4,ftp",
            "缺地址,,ping",
        ])
        created, skipped, errors = parse_batch_targets(
            raw,
            existing_names={"已存在目标"},
            default_group="核心机房",
            default_check_type="ping",
        )
        self.assertEqual(len(created), 7)
        self.assertEqual(created[0]["check_type"], "snmp")
        self.assertEqual(created[0]["group"], "核心机房")
        self.assertEqual(created[3]["check_type"], "ping")
        self.assertEqual([t["address"] for t in created[4:]], ["192.168.1.20", "192.168.1.21", "192.168.1.22"])
        self.assertEqual(skipped, ["已存在目标"])
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()
