"""
core/hotkey_engine.py
Global hotkey registration for JARVIS.
"""

import threading
from typing import Callable, Dict

try:
    from pynput.keyboard import GlobalHotKeys
except Exception:
    GlobalHotKeys = None


class HotkeyEngine:
    def __init__(self):
        self.listener = None
        self._thread = None

    def start(self, bindings: Dict[str, Callable[[], None]]) -> str:
        if GlobalHotKeys is None:
            return "Hotkeys unavailable: install pynput."
        if self.listener is not None:
            return "Hotkeys already running."
        self.listener = GlobalHotKeys(bindings)
        self._thread = threading.Thread(target=self.listener.run, daemon=True)
        self._thread.start()
        return "Global hotkeys started."

    def stop(self) -> str:
        if self.listener is None:
            return "Hotkeys not running."
        try:
            self.listener.stop()
        finally:
            self.listener = None
        return "Global hotkeys stopped."


hotkey_engine = HotkeyEngine()