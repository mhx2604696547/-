"""JSON configuration management with safe first-run defaults."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_TARGETS = [
    {
        "name": "内网网关示例",
        "address": "192.168.1.1",
        "check_type": "ping",
        "device_role": "other",
        "interval": 60,
        "alert_enabled": True,
        "enabled": True,
        "snmp_port": 161,
        "snmp_version": "v2c",
        "snmp_community": "public",
        "http_expected_text": "",
        "http_timeout_seconds": 5,
        "http_verify_tls": True,
    },
    {
        "name": "公共 DNS 示例",
        "address": "8.8.8.8",
        "check_type": "ping",
        "device_role": "other",
        "interval": 60,
        "alert_enabled": True,
        "enabled": True,
        "snmp_port": 161,
        "snmp_version": "v2c",
        "snmp_community": "public",
        "http_expected_text": "",
        "http_timeout_seconds": 5,
        "http_verify_tls": True,
    },
]

DEFAULT_SETTINGS = {
    "wechat_webhook": "",
    "failure_threshold": 3,
    "cooldown_minutes": 5,
    "send_recovery_notification": True,
    # Local alerts stay useful even when the outbound webhook is unavailable.
    # Each active outage plays at most one sound and one visual prompt.
    "desktop_alert_enabled": True,
    "desktop_alert_sound_enabled": True,
    "desktop_alert_flash_enabled": True,
    "log_level": "INFO",
    "history_retention_days": 30,
    "ping_count": 1,
    "ping_timeout_ms": 2000,
    # Daily WeCom inspection report (C08)
    "daily_report_enabled": False,
    "daily_report_time": "18:00",
    # Slow-response / degraded warning (C07); 0 = disabled
    "slow_warning_threshold_ms": 0,
    "slow_warning_consecutive": 3,
    # Multi-target correlation hint threshold (C02); 0 = disabled
    "correlation_alert_threshold": 3,
    # One-time flag so the shortcut offer dialog is shown only once (C14)
    "shortcut_offered": False,
}


def application_root() -> Path:
    """Return the writable folder next to the script or packaged executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


class ConfigManager:
    """Loads and saves target/settings JSON files in the application folder."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or application_root()
        self.config_dir = self.root / "config"
        self.targets_path = self.config_dir / "targets.json"
        self.settings_path = self.config_dir / "settings.json"

    def ensure_files(self) -> None:
        """Create all runtime folders and default configuration files if absent."""
        for folder in (self.config_dir, self.root / "data", self.root / "logs"):
            folder.mkdir(parents=True, exist_ok=True)
        if not self.targets_path.exists():
            self.save_targets(deepcopy(DEFAULT_TARGETS))
        if not self.settings_path.exists():
            self.save_settings(deepcopy(DEFAULT_SETTINGS))

    def load_targets(self) -> list[dict[str, Any]]:
        self.ensure_files()
        value = self._read_json(self.targets_path, DEFAULT_TARGETS)
        return value if isinstance(value, list) else deepcopy(DEFAULT_TARGETS)

    def save_targets(self, targets: list[dict[str, Any]]) -> None:
        self._write_json(self.targets_path, targets)

    def load_settings(self) -> dict[str, Any]:
        self.ensure_files()
        saved = self._read_json(self.settings_path, {})
        settings = deepcopy(DEFAULT_SETTINGS)
        if isinstance(saved, dict):
            settings.update(saved)
        return settings

    def save_settings(self, settings: dict[str, Any]) -> None:
        merged = deepcopy(DEFAULT_SETTINGS)
        merged.update(settings)
        self._write_json(self.settings_path, merged)

    @staticmethod
    def _read_json(path: Path, fallback: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except json.JSONDecodeError:
            ConfigManager._backup_corrupt_file(path)
            return deepcopy(fallback)
        except OSError:
            return deepcopy(fallback)

    @staticmethod
    def _write_json(path: Path, content: Any) -> None:
        """Atomically replace JSON only after a recoverable backup is made."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp", delete=False,
            ) as file:
                temporary_path = Path(file.name)
                json.dump(content, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            if path.exists():
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _backup_corrupt_file(path: Path) -> None:
        """Leave a timestamped copy behind instead of erasing hand-edited JSON."""
        if not path.exists():
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.broken-{stamp}{path.suffix}")
        try:
            shutil.copy2(path, backup)
        except OSError:
            # The caller still receives defaults so the desktop program can
            # start; a later save will retain the last valid .bak copy.
            pass
