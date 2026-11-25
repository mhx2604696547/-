"""Windows autostart registry helper, CLI launch flags, and single-instance mutex."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Sequence

RUN_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "NetworkMonitor"
MUTEX_NAME = "Global\\NetworkMonitor_SingleInstance_Mutex_v1"
ERROR_ALREADY_EXISTS = 183


def parse_launch_args(argv: Sequence[str] | None = None) -> dict[str, bool]:
    """Parse supported startup CLI flags without failing on unknown flags."""
    args = list(sys.argv[1:] if argv is None else argv)
    return {
        "autostart": "--autostart" in args,
    }


def build_autostart_command(executable: str | Path, *, frozen: bool = True) -> str:
    """Build the Windows Run registry command string for silent tray startup."""
    exe = str(Path(executable))
    if frozen:
        return f'"{exe}" --autostart'
    return f'"{exe}" -m app.main --autostart'


def set_autostart(enabled: bool) -> tuple[bool, str]:
    """Enable or disable HKCU Run autostart. Only active for packaged Windows EXE."""
    if sys.platform != "win32":
        return False, "当前系统不支持 Windows 开机自启。"
    if not getattr(sys, "frozen", False):
        return False, "源码运行模式不会写入开机自启，请打包为 NetworkMonitor.exe 后再启用。"
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REGISTRY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = build_autostart_command(sys.executable, frozen=True)
                winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, cmd)
                return True, "已开启开机自启动（静默启动到系统托盘）。"
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
            return True, "已关闭开机自启动。"
    except OSError as error:
        return False, f"写入开机自启注册表失败：{error}"


class SingleInstanceGuard:
    """Prevent running two instances of NetworkMonitor simultaneously on Windows."""

    def __init__(self, mutex_name: str = MUTEX_NAME) -> None:
        self._mutex_name = mutex_name
        self._handle: int | None = None

    def acquire(self) -> bool:
        """Return True if this process owns the singleton mutex, False if another exists."""
        if sys.platform != "win32":
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, False, self._mutex_name)
            if not handle:
                return True
            last_error = kernel32.GetLastError()
            if last_error == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            self._handle = int(handle)
            return True
        except Exception:
            return True

    def release(self) -> None:
        if self._handle is not None and sys.platform == "win32":
            try:
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
