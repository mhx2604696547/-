"""Desktop shortcut creation for the packaged Windows executable."""
from __future__ import annotations

import base64
import logging
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox


def _create_lnk(exe_path: Path, name: str) -> bool:
    """Create a .lnk on the user's desktop via PowerShell COM. Returns True on success."""
    ps_code = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        "$desktop = $ws.SpecialFolders('Desktop')\n"
        f"$lnk = $ws.CreateShortcut($desktop + '\\{name}.lnk')\n"
        "$lnk.TargetPath = @'\n"
        f"{exe_path}\n"
        "'@.Trim()\n"
        "$lnk.WorkingDirectory = @'\n"
        f"{exe_path.parent}\n"
        "'@.Trim()\n"
        "$lnk.Save()\n"
    )
    encoded = base64.b64encode(ps_code.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except Exception:
        return False


def offer_shortcut(
    root: tk.Tk,
    settings: dict,
    save_fn: object,
    logger: logging.Logger,
) -> None:
    """Show a one-time dialog offering to create a desktop shortcut for the packaged EXE."""
    if settings.get("shortcut_offered"):
        return
    if not getattr(sys, "frozen", False):
        settings["shortcut_offered"] = True
        if callable(save_fn):
            save_fn(settings)
        return

    settings["shortcut_offered"] = True
    if callable(save_fn):
        save_fn(settings)

    if not messagebox.askyesno(
        "创建桌面快捷方式",
        "是否在桌面创建「网哨」快捷方式，方便下次直接打开？",
        parent=root,
    ):
        return

    exe_path = Path(sys.executable)
    if _create_lnk(exe_path, "网哨"):
        messagebox.showinfo("完成", "桌面快捷方式已创建。", parent=root)
        logger.info("已在桌面创建快捷方式")
    else:
        messagebox.showwarning(
            "提示",
            "快捷方式创建失败，请手动将 NetworkMonitor.exe 发送到桌面快捷方式。",
            parent=root,
        )
        logger.warning("桌面快捷方式创建失败")
