"""Mouse-first handoff with expiring destination lease and fresh arrival proofs.

No distributed protocol can guarantee recovery after both apps/devices fail.
Before commit, a destination lease and restart journal attempt a physical return.
After commit, a lost acknowledgement is resolved by querying transaction state.
"""

import math
import secrets
import threading
import time

from .identity import canonical, read_file, write_private
from .version import EDITION, PROTOCOL

LEASE_SECONDS = 30.0
TERMINAL = frozenset(("committed", "aborted"))
REASONS = {
    "suspended": "el otro equipo tiene el cambio suspendido",
    "unknown_devices": "el otro equipo aún no reconoce ambos modelos",
    "session": "revisa la sesión y Accesibilidad del otro equipo",
    "recovering": "el otro equipo está recuperando un traslado anterior",
    "busy": "hay otro traslado en curso",
    "update_required": "actualiza CleverSwitch en ambos equipos",
}


def transaction_id(value):
    if not isinstance(value, str) or len(value) != 32:
        raise ValueError("Transacción inválida")
    bytes.fromhex(value)
    return value


class Destination:
    def __init__(self, hardware, cursor, host, journal, clock=time.monotonic):
        self.hardware, self.cursor, self.host, self.journal, self.clock = hardware, cursor, host, journal, clock
        self.lock = threading.RLock()
        # One gate for outgoing work AND the entire lifetime of an incoming lease.
        self.gate = threading.Lock()
        self.owns_gate = False
        self.quiet_until = 0
        self.state = None
        self.deadline = 0
        self.arrived = lambda: None
        self.recovering = False
        if journal and journal.exists():
            saved = read_file(journal)
            transaction_id(saved["tx"])
            if saved.get("phase") not in TERMINAL | {"prepared", "mouse", "keyboard"}:
                raise ValueError("Estado de recuperación inválido")
            self.state = saved
            if saved.get("phase") not in TERMINAL:
                self.gate.acquire()
                self.owns_gate = True
                self.recovering = True
                self.deadline = clock() + 8  # Allow fresh HID discovery after waking/restarting.

    def _record(self, phase):
        updated = {**self.state, "phase": phase}
        if self.journal:
            write_private(self.journal, canonical(updated))
        self.state = updated

    def ready(self):
        return self.blocker() is None

    def blocker(self):
        if not self.hardware.allowed():
            return "suspended"
        if not self.hardware.known():
            return "unknown_devices"
        if not self.cursor.session_ready():
            return "session"
        if self.recovering:
            return "recovering"
        if self.gate.locked() and not self.owns_gate:
            return "busy"
        return None

    def _settled(self):
        self.quiet_until = self.clock() + 3
        if self.owns_gate:
            self.owns_gate = False
            self.gate.release()
        self.arrived()

    def _return(self):
        # Cleanup is permitted even if paused/locked: do not strand a half-switch.
        for role in ("keyboard", "mouse"):
            if self.hardware.probe(role):
                self.hardware.switch(role, 1 - self.host)

    def _abort(self):
        try:
            self._return()
        finally:
            self.recovering = False
            try:
                self._record("aborted")
            finally:
                # A broken disk/journal must not keep the radio awake forever.
                # The previous on-disk phase permits one fresh attempt on restart.
                self.state = {**self.state, "phase": "aborted"}
                self._settled()

    def expire(self):
        with self.lock:
            if self.state and self.state["phase"] not in TERMINAL and self.clock() >= self.deadline:
                # Keep restart recovery pending until discovery knows the pair.
                if self.recovering and not self.hardware.known():
                    self._abort()
                    return
                self._abort()

    def handle(self, op, args):
        with self.lock:
            self.expire()
            if op == "ready":
                if args:
                    raise ValueError("Parámetros inesperados")
                reason = self.blocker()
                return {"ready": reason is None, "reason": reason or "ready", "protocol": PROTOCOL, "edition": EDITION}
            if op == "return":
                if args:
                    raise ValueError("Parámetros inesperados")
                if self.state and self.state["phase"] not in TERMINAL:
                    self._abort()
                else:
                    if not self.gate.acquire(blocking=False):
                        return {"returned": False}
                    try:
                        self._return()
                    finally:
                        self.quiet_until = self.clock() + 3
                        self.gate.release()
                return {"returned": True}  # Command attempted; source still must prove arrival.
            if op == "prepare":
                if "protocol" not in args or args["protocol"] != PROTOCOL:
                    return {"accepted": False, "reason": "update_required"}
                if set(args) != {"tx", "kind", "y", "protocol"} or args["kind"] not in ("edge", "keyboard"):
                    raise ValueError("Preparación inválida")
                tx = transaction_id(args["tx"])
                y = args["y"]
                if isinstance(y, bool) or not isinstance(y, (int, float)) or not math.isfinite(y) or not 0 <= y <= 1:
                    raise ValueError("Posición inválida")
                # Retrying a lost prepare acknowledgement must not create a second lease.
                if self.state and self.state["tx"] == tx and self.state["phase"] not in TERMINAL:
                    if (self.state["kind"], self.state["y"]) == (args["kind"], y):
                        reason = self.blocker()
                        if reason:
                            return {"accepted": False, "reason": reason}
                        return {"accepted": True}
                    raise ValueError("Transacción reutilizada con otros parámetros")
                reason = self.blocker()
                if reason or (self.state and self.state["phase"] not in TERMINAL):
                    return {"accepted": False, "reason": reason or "busy"}
                # Disallow reusing terminal IDs; late messages cannot affect a new transaction.
                if self.state and self.state["tx"] == tx:
                    return {"accepted": False}
                if not self.gate.acquire(blocking=False):
                    return {"accepted": False, "reason": "busy"}
                self.owns_gate = True
                old = self.state
                try:
                    self.state = {"tx": tx, "phase": "prepared", "kind": args["kind"], "y": y}
                    self._record("prepared")
                except Exception:
                    self.state = old
                    self._settled()
                    raise
                self.deadline = self.clock() + LEASE_SECONDS
                return {"accepted": True}
            if set(args) != {"tx"}:
                raise ValueError("Parámetros inesperados")
            tx = transaction_id(args["tx"])
            if not self.state or tx != self.state["tx"]:
                return {"phase": "unknown"}
            if op == "status":
                return {"phase": self.state["phase"]}
            if op == "abort":
                if self.state["phase"] not in TERMINAL:
                    self._abort()
                return {"phase": self.state["phase"]}
            if self.state["phase"] in TERMINAL:
                return {"phase": self.state["phase"], "present": False}
            if not self.ready():
                self._abort()
                return {"phase": "aborted", "present": False}
            if op == "mouse":
                if self.state["phase"] not in ("prepared", "mouse"):
                    return {"present": False}
                present = self.hardware.probe("mouse", timeout=1)
                if present:
                    self._record("mouse")
                    self.deadline = self.clock() + LEASE_SECONDS
                return {"present": present}
            if op == "keyboard":
                if self.state["phase"] not in ("mouse", "keyboard"):
                    return {"present": False}
                present = self.hardware.probe("keyboard", timeout=1)
                if present:
                    self._record("keyboard")
                    self.deadline = self.clock() + LEASE_SECONDS
                return {"present": present}
            if op == "commit":
                if self.state["phase"] != "keyboard":
                    return {"phase": self.state["phase"]}
                if not self.hardware.probe("mouse") or not self.hardware.probe("keyboard"):
                    # A sleeping radio or one missed ping is not proof of departure.
                    # Keep the same finite lease; the source retries commit freshly.
                    return {"phase": "keyboard", "pending": True}
                # Cursor placement is part of readiness and can still fail on a locked desktop.
                if self.state["kind"] == "edge":
                    try:
                        self.cursor.enter("left" if self.host == 1 else "right", self.state["y"])
                    except OSError:
                        self._abort()
                        return {"phase": "aborted"}
                self._record("committed")
                self._settled()
                return {"phase": "committed"}
            raise ValueError("Operación inválida")

    def close(self):
        with self.lock:
            if self.state and self.state["phase"] not in TERMINAL:
                self._abort()


class Coordinator:
    def __init__(self, hardware, cursor, link, destination, clock=time.monotonic):
        self.hardware, self.cursor, self.link, self.destination, self.clock = hardware, cursor, link, destination, clock
        self.host = destination.host
        self.busy = destination.gate
        self.status = "Enlace local listo; cambio por bordes desactivado"
        self.suppressed_until = 0
        self.stop_event = threading.Event()
        self.worker = None
        self.last_error = None
        self.phase = "idle"

    def can_start(self):
        return (
            not self.stop_event.is_set()
            and not self.busy.locked()
            and self.clock() >= max(self.suppressed_until, self.destination.quiet_until)
        )

    def _note(self, phase):
        self.phase = phase
        if self.destination.journal:
            try:
                write_private(
                    self.destination.journal.with_name("last-transfer.json"),
                    canonical({"edition": EDITION, "phase": phase, "reason": self.last_error}),
                )
            except OSError:
                pass  # Diagnostics must never affect transfer or recovery.

    def request(self, kind="edge", y=0.5):
        if not self.can_start() or not self.busy.acquire(blocking=False):
            return False
        self.worker = threading.Thread(target=self._run, args=(kind, y), name="flow-handoff", daemon=True)
        self.worker.start()
        return True

    def _confirmed(self, op, tx):
        deadline = self.clock() + 8
        while self.clock() < deadline and not self.stop_event.is_set() and self.hardware.allowed():
            try:
                result = self.link.call(op, {"tx": tx}, timeout=min(2, max(0.1, deadline - self.clock())))
                if result.get("present") is True:
                    return True
                if result.get("phase") in TERMINAL | {"unknown"}:
                    return False
            except (OSError, ValueError):
                pass  # Retry within the existing deadline, never an unbounded round.
            self.stop_event.wait(0.15)
        return False

    def _present(self, role):
        for attempt in range(3):
            if self.stop_event.is_set() or not self.hardware.allowed():
                return False
            if self.hardware.probe(role):
                return True
            if attempt < 2:
                self.stop_event.wait(0.15)
        return False

    def _prepare(self, tx, kind, y):
        for attempt in range(3):
            try:
                return self.link.call("prepare", {"tx": tx, "kind": kind, "y": y, "protocol": PROTOCOL})
            except (OSError, ValueError):
                if attempt == 2 or self.stop_event.wait(0.15):
                    raise

    def _commit(self, tx):
        deadline = self.clock() + 8
        while self.clock() < deadline and not self.stop_event.is_set():
            try:
                result = self.link.call("commit", {"tx": tx}, timeout=min(2, max(0.1, deadline - self.clock())))
            except (OSError, ValueError):
                try:
                    result = self.link.call("status", {"tx": tx}, timeout=1)
                except (OSError, ValueError):
                    result = {}
            if result.get("phase") in TERMINAL | {"unknown"}:
                return result.get("phase") == "committed"
            self.stop_event.wait(0.15)
        return False

    def _run(self, kind, y):
        tx = secrets.token_hex(16)
        prepared = False
        sent_mouse = False
        committed = False
        attempted = False
        try:
            self.last_error = None
            self._note("preflight")
            self.status = "Comprobando el otro equipo…"
            if not self.hardware.allowed():
                raise RuntimeError("El cambio está suspendido aquí")
            if not self.cursor.session_ready():
                raise RuntimeError("Revisa la sesión y Accesibilidad de este equipo")
            if not self._present("mouse"):
                raise RuntimeError("El ratón no responde aquí")
            if kind == "edge" and not self._present("keyboard"):
                raise RuntimeError("El teclado no está aquí")
            if self.destination.state and self.destination.state["phase"] not in TERMINAL:
                raise RuntimeError("Otro cambio en curso")
            peer = self.link.call("ready")
            if peer.get("protocol") != PROTOCOL:
                raise RuntimeError(REASONS["update_required"])
            if peer.get("ready") is not True:
                raise RuntimeError(REASONS.get(peer.get("reason"), "El otro equipo no está listo"))
            attempted = True
            response = self._prepare(tx, kind, y)
            prepared = response.get("accepted") is True
            if not prepared:
                raise RuntimeError(REASONS.get(response.get("reason"), "El otro equipo no está listo"))
            # Suppress our own HID host notifications, without disabling physical buttons.
            self.suppressed_until = self.clock() + 20
            self.status = "Comprobando la llegada del ratón…"
            self._note("mouse")
            self.hardware.switch("mouse", 1 - self.host)
            sent_mouse = True
            if not self._confirmed("mouse", tx):
                raise RuntimeError("El ratón no ha confirmado su llegada")
            if kind == "edge":
                # Recheck after the asynchronous arrival, before giving up the last local input.
                if not self.hardware.allowed() or not self.cursor.session_ready() or not self._present("keyboard"):
                    raise RuntimeError("La sesión cambió durante el traslado")
                self.hardware.switch("keyboard", 1 - self.host)
            self.status = "Comprobando la llegada del teclado…"
            self._note("keyboard")
            if not self._confirmed("keyboard", tx):
                raise RuntimeError("El teclado no ha confirmado su llegada")
            committed = self._commit(tx)
            if not committed:
                raise RuntimeError("Cambio no confirmado")
            self.status = "Ratón y teclado confirmados en " + ("Mac 2" if self.host == 0 else "Windows 1")
            self._note("committed")
        except Exception as error:
            self.last_error = (
                str(error) if isinstance(error, RuntimeError) else "La comunicación local se ha interrumpido"
            )
            self._note("failed")
            if attempted:
                try:
                    result = self.link.call("abort", {"tx": tx})
                    committed = result.get("phase") == "committed"
                except (OSError, ValueError):
                    pass  # The destination's independent lease attempts return.
            if committed:
                self.status = "Cambio confirmado en el otro equipo"
                self._note("committed")
            elif sent_mouse:
                self.status = "Cambio fallido; intentando regresar al origen…"
                deadline = self.clock() + LEASE_SECONDS + 2
                while self.clock() < deadline and not self.stop_event.is_set():
                    if self.hardware.probe("mouse") and self.hardware.probe("keyboard"):
                        self.status = "Cambio cancelado; ratón y teclado recuperados aquí"
                        break
                    self.stop_event.wait(0.2)
                else:
                    self.status = "No se pudo confirmar el regreso; usa los botones físicos 1/2"
            else:
                self.status = "Cambio bloqueado: " + self.last_error
        finally:
            self.suppressed_until = self.clock() + 3
            self.busy.release()

    def keyboard_event(self, event):
        if not self.hardware.allowed() or self.clock() < self.suppressed_until or self.busy.locked():
            return True
        origin = next(
            (
                d
                for d in self.hardware.context.device_registry.all_entries()
                if (d.pid, d.slot) == (event.pid, event.slot)
            ),
            None,
        )
        if origin is None or origin.role != "keyboard" or not self.hardware.context.policy.allows(origin):
            return True
        if event.target_host == 2:
            # Observe-only: firmware already handles all physical channels.
            return True
        if event.target_host not in (0, 1):
            return True
        if event.target_host != self.host:
            self.request("keyboard")
        return True

    def recover(self):
        if not self.busy.acquire(blocking=False):
            return False

        def run():
            try:
                self.link.call("return")
                deadline = self.clock() + 5
                while self.clock() < deadline and not self.stop_event.is_set():
                    if self.hardware.probe("mouse") and self.hardware.probe("keyboard"):
                        self.status = "Ratón y teclado confirmados aquí"
                        return
                    self.stop_event.wait(0.2)
                self.status = "Regreso sin confirmar; usa los botones físicos 1/2"
            except (OSError, ValueError):
                self.status = "Otro equipo no disponible; usa los botones físicos 1/2"
            finally:
                self.busy.release()

        self.worker = threading.Thread(target=run, name="flow-return", daemon=True)
        self.worker.start()
        return True

    def close(self):
        self.stop_event.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=7)
