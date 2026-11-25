"""Optional system-tray integration backed by pystray with live status indicator."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable


def _build_tray_image(state: str) -> Any:
    """Render a crisp 64x64 tray icon reflecting health state ('ok' | 'alert' | 'idle')."""
    from PIL import Image, ImageDraw

    bg_color = {
        "ok": "#10B981",
        "alert": "#F43F5E",
        "idle": "#1F6AA5",
    }.get(state, "#1F6AA5")
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=bg_color)
    draw.ellipse((18, 18, 46, 46), fill="#FFFFFF")
    if state == "alert":
        draw.ellipse((26, 26, 38, 38), fill="#F43F5E")
    elif state == "ok":
        draw.ellipse((26, 26, 38, 38), fill="#10B981")
    return image


class TrayIcon:
    """Runs a tray icon in a daemon thread, degrading safely if unavailable."""

    def __init__(self, show_window: Callable[[], None], quit_app: Callable[[], None], logger: logging.Logger) -> None:
        self.show_window = show_window
        self.quit_app = quit_app
        self.logger = logger
        self.icon: Any = None
        self._last_state: tuple[str, int] | None = None

    def start(self) -> None:
        try:
            import pystray

            image = _build_tray_image("idle")
            self.icon = pystray.Icon(
                "network_monitor",
                image,
                "网哨",
                menu=pystray.Menu(
                    pystray.MenuItem("显示窗口", lambda _icon, _item: self.show_window(), default=True),
                    pystray.MenuItem("退出", lambda _icon, _item: self.quit_app()),
                ),
            )
            threading.Thread(target=self.icon.run, name="tray-icon", daemon=True).start()
        except ImportError:
            self.logger.warning("未安装 pystray/Pillow，系统托盘功能不可用")
        except Exception:
            self.logger.exception("启动系统托盘失败")

    def update_status(self, down_count: int) -> None:
        """Update tray icon color and tooltip when alert count changes."""
        icon = self.icon
        if icon is None:
            return
        count = max(0, int(down_count))
        state = "alert" if count > 0 else "ok"
        if self._last_state == (state, count):
            return
        self._last_state = (state, count)
        try:
            icon.icon = _build_tray_image(state)
            icon.title = f"网哨 · 当前 {count} 项异常" if count > 0 else "网哨 · 全部运行正常"
        except Exception:
            self.logger.debug("更新托盘状态图标失败", exc_info=True)

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def notify(self, title: str, message: str) -> None:
        """Show a best-effort native notification through the tray backend."""
        icon = self.icon
        if icon is None:
            self.logger.debug("系统托盘未运行，跳过本机通知")
            return
        notifier = getattr(icon, "notify", None)
        if not callable(notifier):
            self.logger.debug("当前系统托盘后端不支持本机通知")
            return
        try:
            notifier(str(message), str(title))
        except NotImplementedError:
            self.logger.debug("当前系统托盘后端未实现本机通知")
        except Exception:
            self.logger.warning("发送本机托盘通知失败，已安全跳过")
