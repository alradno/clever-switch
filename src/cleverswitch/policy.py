"""Local policy restricted to one MX Keys and one MX Master 3."""

import threading
import time


class PairPolicy:
    MODELS = {"keyboard": "MX Keys", "mouse": "MX Master 3"}
    # HID++ DEVICE_NAME differs from the short marketing/friendly name on
    # original Unifying devices. Exact allowlist: never trust editable aliases
    # or a substring that would also admit MX Keys S / MX Master 3S.
    DEVICE_NAMES = {
        "keyboard": frozenset({"MX Keys", "MX Keys Wireless Keyboard"}),
        "mouse": frozenset({"MX Master 3", "Wireless Mouse MX Master 3"}),
    }

    def __init__(self):
        self.paused = threading.Event()
        self.closing = threading.Event()
        self.host_change_handler = None
        self.observe_only = False
        self.background_suspended = threading.Event()
        self._background_condition = threading.Condition()
        self._arrival_until = 0.0

    def set_background_suspended(self, suspended):
        with self._background_condition:
            if self.background_suspended.is_set() == suspended:
                return
            if suspended:
                self.background_suspended.set()
            else:
                self.background_suspended.clear()
            self._background_condition.notify_all()

    def expect_arrival(self, seconds):
        """Temporarily accelerate discovery only during an authenticated lease."""
        with self._background_condition:
            self._arrival_until = time.monotonic() + max(0, min(seconds, 30))
            self._background_condition.notify_all()

    def wait_hardware(self, stop_event, interval):
        with self._background_condition:
            if stop_event.is_set() or self.background_suspended.is_set():
                return
            if time.monotonic() < self._arrival_until:
                interval = min(interval, 0.05)
            self._background_condition.wait(interval)

    def wake_background(self):
        with self._background_condition:
            self._background_condition.notify_all()

    def wait_background(self, stop_event):
        with self._background_condition:
            self._background_condition.wait_for(lambda: not self.background_suspended.is_set() or stop_event.is_set())

    def allows(self, device):
        return device.name in self.DEVICE_NAMES.get(device.role, ())

    def targets(self, registry, event):
        if self.paused.is_set() or self.closing.is_set() or event.target_host not in (0, 1, 2):
            return []
        entries = registry.all_entries()
        origin = next((d for d in entries if (d.pid, d.slot) == (event.pid, event.slot)), None)
        if origin is None or origin.role != "keyboard" or not self.allows(origin):
            return []
        eligible = [d for d in entries if self.allows(d) and (d.connected or d is origin)]
        if any(sum(d.role == role for d in eligible) != 1 for role in self.MODELS):
            return []
        return eligible
