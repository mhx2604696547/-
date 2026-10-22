"""Application entry point for the Windows desktop network monitor."""
from __future__ import annotations

import queue
import sys
import tkinter as tk
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.alert import AlertEngine
from app.core.monitor import MonitorEngine
from app.gui.main_window import MainWindow
from app.integrations.wechat import WeChatNotifier
from app.utils.config import ConfigManager
from app.utils.database import MonitorDatabase
from app.utils.logger import setup_logging


def run() -> None:
    """Create runtime data, construct the UI, and enter Tk's main loop."""
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
    # Store threshold/cooldown state in SQLite so a restart during an outage
    # does not silently reset the failure count or re-send old alerts.
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
    monitor.start()  # The requested default behavior is to monitor immediately on launch.
    window.sync_monitor_state()
    root.mainloop()


if __name__ == "__main__":
    run()
