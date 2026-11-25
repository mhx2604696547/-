"""Application entry point for the Windows desktop network monitor."""
from __future__ import annotations

import ctypes
import queue
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.alert import AlertEngine
from app.core.monitor import MonitorEngine
from app.gui.main_window import MainWindow
from app.integrations.wechat import WeChatNotifier
from app.utils.autostart import SingleInstanceGuard, parse_launch_args
from app.utils.config import ConfigManager
from app.utils.database import MonitorDatabase
from app.utils.logger import setup_logging


def _enable_high_dpi() -> None:
    """Declare Windows DPI awareness so Tkinter and Canvas render sharply on 125%/150% displays."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def run(argv: list[str] | None = None) -> None:
    """Create runtime data, construct the UI, and enter Tk's main loop."""
    _enable_high_dpi()
    launch_flags = parse_launch_args(argv)
    guard = SingleInstanceGuard()
    if not guard.acquire():
        temp_root = tk.Tk()
        temp_root.withdraw()
        messagebox.showinfo("网哨已在运行", "网哨已经在后台运行，请在系统托盘图标中打开窗口。", parent=temp_root)
        temp_root.destroy()
        return

    try:
        config = ConfigManager()
        config.ensure_files()
        settings = config.load_settings()
        logger = setup_logging(config.root / "logs", str(settings.get("log_level", "INFO")))
        database = MonitorDatabase(config.root / "data" / "monitor.db")
        removed = database.cleanup_history(int(settings.get("history_retention_days", 30)))
        if removed:
            logger.info("已清理 %d 条过期检测历史", removed)

        targets = config.load_targets()
        event_queue: queue.Queue[dict] = queue.Queue()
        notifier = WeChatNotifier(str(settings.get("wechat_webhook", "")), logger)
        alert_engine = AlertEngine(settings, notifier, logger, state_store=database)
        monitor = MonitorEngine(targets, database, alert_engine, event_queue, logger)

        def save_config(new_targets: list[dict], new_settings: dict) -> None:
            config.save_targets(new_targets)
            config.save_settings(new_settings)
            monitor.update_targets(new_targets)
            alert_engine.settings = new_settings
            notifier.webhook_url = str(new_settings.get("wechat_webhook", "")).strip()
            logger.setLevel(str(new_settings.get("log_level", "INFO")).upper())
            logger.info("配置已更新")

        root = tk.Tk()
        window = MainWindow(root, targets, settings, database, monitor, event_queue, save_config, logger)
        monitor.start()
        window.sync_monitor_state()
        if launch_flags["autostart"]:
            window.minimize_to_tray()
            logger.info("开机自启动模式：已静默最小化到系统托盘")
        root.mainloop()
    finally:
        guard.release()


if __name__ == "__main__":
    run()
