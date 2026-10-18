"""Bounded readiness snapshots; permission APIs never run on an input callback.

Only a connected, enabled native backend starts the worker. A delayed OS query
cannot keep granting input indefinitely, and no worker survives a disconnect.
"""

import threading
import time

from .input_timing import Duration


class Readiness:
    def __init__(self, check, clock=time.monotonic):
        self.check, self.clock = check, clock
        self.sample = (False, float("-inf"))
        self.interval, self.idle_interval, self.max_age = 0.1, 0.4, 0.5
        self.active = threading.Event()
        self.wake, self.stop = threading.Event(), threading.Event()
        self.worker = None
        self.duration = Duration()

    def _refresh(self):
        started = self.clock()
        try:
            value = bool(self.check())
        except Exception:
            value = False
        self.duration.observe(self.clock() - started)
        # Age starts before the OS call: a late result is not fresh evidence.
        self.sample = value, started

    def ready(self):
        value, checked = self.sample
        age = self.clock() - checked
        if self.worker is not None and self.worker.is_alive():
            if age >= self.interval:
                self.wake.set()
            return not self.stop.is_set() and value and age < self.max_age
        if age >= self.interval:
            self._refresh()  # Before capture starts, outside the input path.
        value, checked = self.sample
        return value and self.clock() - checked < self.max_age

    def start(self):
        if self.worker is not None and self.worker.is_alive():
            raise RuntimeError("La comprobación de entrada anterior sigue activa")
        self.stop.clear()
        self.wake.clear()
        self.worker = threading.Thread(target=self._run, name="flow-input-health", daemon=True)
        self.worker.start()

    def set_active(self, active):
        if self.active.is_set() != bool(active):
            self.active.set() if active else self.active.clear()
            self.wake.set()

    def _run(self):
        while not self.stop.is_set():
            # Keep the same 500 ms trust limit in both modes. An explicit
            # ready() demand wakes this early, without waiting in a native hook.
            interval = self.interval if self.active.is_set() else self.idle_interval
            delay = max(0, interval - (self.clock() - self.sample[1]))
            self.wake.wait(delay)
            self.wake.clear()
            if not self.stop.is_set() and self.clock() - self.sample[1] >= self.interval:
                self._refresh()

    def close(self):
        self.stop.set()
        self.active.clear()
        self.wake.set()
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=1)
            if self.worker.is_alive():
                raise RuntimeError("La comprobación de entrada no ha terminado")
