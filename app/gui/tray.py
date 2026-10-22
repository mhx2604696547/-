"""Optional system-tray integration backed by pystray."""
from __future__ import annotations

import logging
import threading
from typing import Callable


class TrayIcon:
    """Runs a tray icon in a daemon thread, degrading safely if unavailable."""

    def __init__(self, show_window: Callable[[], None], quit_app: Callable[[], None], logger: logging.Logger) -> None:
        self.show_window = show_window
        self.quit_app = quit_app
        self.logger = logger
        self.icon = None

    def start(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
            image = Image.new("RGB", (64, 64), "#1f6aa5")
            draw = ImageDraw.Draw(image)
            draw.ellipse((16, 16, 48, 48), fill="#ffffff")
            self.icon = pystray.Icon(
                "network_monitor", image, "网络监控",
                menu=pystray.Menu(
                    pystray.MenuItem("显示窗口", lambda _icon, _item: self.show_window()),
                    pystray.MenuItem("退出", lambda _icon, _item: self.quit_app()),
                ),
            )
            threading.Thread(target=self.icon.run, name="tray-icon", daemon=True).start()
        except ImportError:
            self.logger.warning("未安装 pystray/Pillow，系统托盘功能不可用")
        except Exception:
            self.logger.exception("启动系统托盘失败")

    def stop(self) -> None:
        if self.icon is not None:
            self.icon.stop()

    def notify(self, title: str, message: str) -> None:
        """Show a best-effort native notification through the tray backend.

        pystray delegates notifications to the operating-system backend and not
        every backend implements them.  Alerts must never be allowed to affect
        monitoring, so an unavailable tray or a backend failure is deliberately
        reduced to a safe diagnostic log entry.  Do not log ``title`` or
        ``message`` here: either value may describe a private internal target.
        """
        icon = self.icon
        if icon is None:
            self.logger.debug("系统托盘未运行，跳过本机通知")
            return

        notifier = getattr(icon, "notify", None)
        if not callable(notifier):
            self.logger.debug("当前系统托盘后端不支持本机通知")
            return

        try:
            # pystray's public API is notify(message, title=None).
            notifier(str(message), str(title))
        except NotImplementedError:
            self.logger.debug("当前系统托盘后端未实现本机通知")
        except Exception:
            # Avoid ``logger.exception``: a backend exception could echo the
            # notification body and inadvertently expose target details.
            self.logger.warning("发送本机托盘通知失败，已安全跳过")
