"""Single-flight setup actions, outside the Windows tray's window procedure."""

import logging
import sys
import threading

log = logging.getLogger(__name__)


class Actions:
    def __init__(self, on_change):
        self.on_change = on_change
        self.lock = threading.Lock()
        self.closed = threading.Event()

    @property
    def busy(self):
        return self.lock.locked()

    def wrap(self, callback):
        def invoke(icon, item):
            if self.closed.is_set() or not self.lock.acquire(blocking=False):
                return

            def execute():
                try:
                    if not self.closed.is_set():
                        callback(icon, item)
                except Exception:
                    log.exception("Setup action failed")
                    if not self.closed.is_set():
                        from .dialogs import message

                        message(
                            "Operación sin completar", "No se pudo completar la operación. Puedes volver a intentarlo."
                        )
                finally:
                    self.lock.release()
                    self.on_change()

            self.on_change()
            if sys.platform == "win32":
                # Fresh thread: IFileDialog initializes its own STA; never join here.
                worker = threading.Thread(target=execute, name="cleverswitch-setup", daemon=True)
                try:
                    worker.start()
                except Exception:
                    self.lock.release()
                    self.on_change()
                    raise
            else:
                # AppKit panels must stay on the main runloop.
                execute()

        return invoke
