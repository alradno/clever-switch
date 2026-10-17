"""Bounded readiness snapshots; permission APIs never run on an input callback.

Only a connected, enabled native backend starts the worker. A delayed OS query
cannot keep granting input indefinitely, and no worker survives a disconnect.
"""

import threading
import time


class Readiness:
    def __init__(self, check, clock=time.monotonic):
        self.check, self.clock = check, clock
        self.sample = (False, float("-inf"))
        self.interval, self.max_age = 0.1, 0.5
        self.wake, self.stop = threading.Event(), threading.Event()
        self.worker = None

    def _refresh(self):
        started = self.clock()
        try:
            value = bool(self.check())
        except Exception:
            value = False
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

    def _run(self):
        while not self.stop.is_set():
            self.wake.wait(self.interval)
            self.wake.clear()
            if not self.stop.is_set() and self.clock() - self.sample[1] >= self.interval:
                self._refresh()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=1)
            if self.worker.is_alive():
                raise RuntimeError("La comprobación de entrada no ha terminado")
