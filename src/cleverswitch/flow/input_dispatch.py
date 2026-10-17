"""Bounded calls on a native input thread; never execute abandoned work later."""

import queue
import threading


class Task:
    def __init__(self, action, rollback):
        self.action, self.rollback = action, rollback
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.cancelled = False
        self.result = self.error = None

    def cancel_unfinished(self):
        # Completion and timeout contend for one lock. An action cannot report
        # completion just before cancellation and leave an abandoned mutation.
        with self.lock:
            if self.done.is_set():
                return False
            self.cancelled = True
            return True


class Dispatch:
    def __init__(self, wake):
        self.wake = wake
        self.tasks = queue.Queue(maxsize=16)
        self.owner = None
        self.closed = False
        self.lock = threading.Lock()

    def call(self, action, timeout=0.5, rollback=None):
        with self.lock:
            if self.closed:
                raise ConnectionError("Trabajador de entrada cerrado")
            direct = self.owner == threading.get_ident()
            if not direct:
                task = Task(action, rollback)
                self.tasks.put_nowait(task)
        if direct:
            return action()
        try:
            self.wake()
        except Exception:
            if task.cancel_unfinished():
                raise
        if not task.done.wait(timeout) and task.cancel_unfinished():
            raise TimeoutError("El trabajador de entrada no responde")
        if task.error is not None:
            raise task.error
        return task.result

    def drain(self):
        for _ in range(16):
            try:
                task = self.tasks.get_nowait()
            except queue.Empty:
                return
            with task.lock:
                if self.closed or task.cancelled:
                    task.error = ConnectionError("Acción de entrada cancelada")
                    task.done.set()
                    continue
            result = error = None
            try:
                result = task.action()
            except Exception as caught:
                error = caught
            with task.lock:
                if not self.closed and not task.cancelled:
                    task.result, task.error = result, error
                    task.done.set()
                    continue
            # A native API call may outlive its caller's timeout. Undo a late
            # capture on this same owner thread, preserving cursor affinity.
            try:
                if task.rollback is not None:
                    task.rollback()
            except Exception as caught:
                error = caught
            with task.lock:
                task.error = error or ConnectionError("Acción de entrada cancelada")
                task.done.set()
        if not self.closed and not self.tasks.empty():
            self.wake()

    def close(self):
        with self.lock:
            self.closed = True
        self.drain()
