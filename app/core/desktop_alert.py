"""Pure state policy for local desktop sound/light alarm transitions.

The policy deliberately knows nothing about Tkinter, Windows sounds, or the
system tray.  Monitor results are delivered to it on Tk's main thread, then
the GUI performs any visible or audible action.  Keeping this part pure makes
threshold and recovery behavior safe to test without a display or speakers.
"""
from __future__ import annotations

from typing import Any


def as_bool(value: object, default: bool = True) -> bool:
    """Accept JSON booleans and common hand-edited true/false spellings."""
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "no", "off", ""}:
            return False
        if normalized in {"true", "1", "yes", "on"}:
            return True
    return bool(value)


class DesktopAlertPolicy:
    """Emit one local outage alert and one recovery event per incident."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self._states: dict[str, dict[str, int | bool]] = {}

    def update_settings(self, settings: dict[str, Any]) -> None:
        """Use newly saved settings without losing state for active targets."""
        self.settings = settings

    def reset(self) -> None:
        """Forget current incidents after the user disables local alerts."""
        self._states.clear()

    def sync_targets(self, targets: list[dict[str, Any]]) -> None:
        """Drop state for deleted or locally disabled targets after a config save."""
        enabled_keys = {self._key(target) for target in targets if self._is_enabled(target)}
        self._states = {key: state for key, state in self._states.items() if key in enabled_keys}

    @staticmethod
    def _key(target: dict[str, Any]) -> str:
        return "|".join(
            str(target.get(field, "")) for field in ("name", "address", "check_type")
        )

    def process(self, target: dict[str, Any], result: dict[str, Any]) -> str | None:
        """Return ``alert`` / ``recovery`` once per incident, otherwise ``None``.

        The existing per-target alert switch controls both the enterprise
        WeCom route and this desktop route.  A hand-edited target may also use
        ``desktop_alert_enabled: false`` to suppress only local prompts.
        """
        key = self._key(target)
        if not self._is_enabled(target):
            self._states.pop(key, None)
            return None

        state = self._states.setdefault(key, {"failures": 0, "outage_open": False})
        status = str(result.get("status", "unknown")).lower()
        if status == "up":
            was_open = bool(state["outage_open"])
            state["failures"] = 0
            state["outage_open"] = False
            return "recovery" if was_open else None

        state["failures"] = int(state["failures"]) + 1
        if bool(state["outage_open"]):
            return None
        if int(state["failures"]) < self._failure_threshold():
            return None

        state["outage_open"] = True
        return "alert"

    def _is_enabled(self, target: dict[str, Any]) -> bool:
        return as_bool(self.settings.get("desktop_alert_enabled", True)) and as_bool(
            target.get("alert_enabled", True)
        ) and as_bool(target.get("desktop_alert_enabled", True))

    def _failure_threshold(self) -> int:
        try:
            return max(1, int(self.settings.get("failure_threshold", 3)))
        except (TypeError, ValueError):
            return 3
