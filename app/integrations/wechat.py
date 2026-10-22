"""WeCom (Enterprise WeChat) group robot client."""
from __future__ import annotations

import logging
import re

import requests

# Matches the secret "key" query parameter present in every WeCom webhook URL,
# so it can be scrubbed even from error text that doesn't match webhook_url verbatim
# (e.g. a redirected or otherwise rewritten URL echoed back by the HTTP stack).
_KEY_PARAM_PATTERN = re.compile(r"(key=)[^&\s\"']+")


class WeChatNotifier:
    """Sends Markdown notifications to a configured WeCom group robot."""

    def __init__(self, webhook_url: str, logger: logging.Logger) -> None:
        self.webhook_url = webhook_url.strip()
        self.logger = logger

    def send_markdown(self, content: str) -> bool:
        """Return True only when WeCom accepts the notification."""
        if not self.webhook_url:
            self.logger.warning("未配置企业微信 Webhook，跳过告警发送")
            return False
        try:
            response = requests.post(
                self.webhook_url,
                json={"msgtype": "markdown", "markdown": {"content": content}},
                timeout=5,
            )
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("errcode", 0) != 0:
                self.logger.error("企业微信机器人拒绝消息: %s", response_data.get("errmsg", "未知错误"))
                return False
            return True
        except (requests.RequestException, ValueError) as error:
            # Deliberately not logger.exception(): requests embeds the full
            # request URL (including the secret webhook key) in exception text
            # such as HTTPError/ConnectionError messages, and a traceback dump
            # would repeat that same text. Log a redacted, single-line summary.
            self.logger.error(
                "企业微信告警发送失败（%s）：%s",
                type(error).__name__,
                self._redact(str(error)),
            )
            return False

    def _redact(self, message: str) -> str:
        """Strip the webhook URL/secret key out of a message before logging it."""
        if self.webhook_url:
            message = message.replace(self.webhook_url, "<webhook 已隐藏>")
        return _KEY_PARAM_PATTERN.sub(r"\1<已隐藏>", message)
