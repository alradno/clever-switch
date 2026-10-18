"""Exclusive input focus, bounded handoff and release of this session's input.

The backend is injected: importing or constructing this state machine does not
register hooks or touch OS input. All state is transient, never journalled.
"""

import collections
import secrets
import threading
import time

from .input_protocol import Kind, Outbox, Packet

INPUT_KINDS = frozenset((Kind.MOVE, Kind.KEY, Kind.BUTTON, Kind.WHEEL, Kind.LOCKS))
LOCK_KEYS = {57: 1, 83: 2, 71: 4}


class Session:
    def __init__(
        self, role, desktop, backend, send, on_active=None, clock=time.monotonic, token=None, can_receive=None
    ):
        if role not in ("windows", "mac"):
            raise ValueError("Equipo inválido")
        self.role, self.desktop, self.backend, self.send = role, desktop, backend, send
        self.clock = clock
        self.token = token or (lambda: secrets.randbelow(2**64 - 1) + 1)
        self.on_active = on_active or (lambda active: None)
        self.can_receive = can_receive or (lambda: True)
        self.lock = threading.RLock()
        self.phase = "local"
        self.epoch = 0
        self.local_generation = 0
        self.pending = Outbox()
        self.pending_actions = False
        self.deadline = None
        self.keys, self.buttons = set(), set()
        self.unreleased = set()
        self.retired = collections.deque(maxlen=128)
        self.closed = False
        self.failure = None
        self.outbound_locks = self.pending_locks = None
        self.sent_lock_keys = set()
        self.pending_capture = False

    def _phase(self, value):
        if value == "local":
            self.local_generation += 1
        self.phase = value
        self.on_active(value != "local")

    def _emit(self, packet):
        try:
            self.send(packet)
        except Exception:
            self._reset("connection")
            raise ConnectionError("No se pudo enviar la entrada") from None

    def enter(self, x, y):
        with self.lock:
            if self.closed or self.phase != "local" or not self.backend.ready() or not self.retry_releases():
                return False
            packet = Packet(Kind.ENTER, self.token(), (x, y))
            self.epoch = packet.epoch
            self.deadline = self.clock() + 0.4
            try:
                self.outbound_locks = getattr(self.backend, "lock_state", lambda: None)()
                self.backend.capture(True)
                self._phase("requesting")
                self._emit(packet)
                if self.outbound_locks is not None:
                    self.pending.put(Packet(Kind.LOCKS, self.epoch, self.outbound_locks))
                self.failure = None
            except Exception:
                self._reset("capture")
                return False
            return True

    def input(self, kind, *values):
        """Return whether physical input belongs to the remote focus."""
        with self.lock:
            if self.closed or self.phase not in ("requesting", "sending"):
                return False
            if kind not in INPUT_KINDS:
                raise ValueError("Tipo de entrada inválido")
            packet = Packet(kind, self.epoch, values)
            if kind == Kind.KEY and values[0] in LOCK_KEYS and self.outbound_locks is not None:
                code, down = values
                state, mask = self.outbound_locks
                if down and code not in self.sent_lock_keys:
                    self.outbound_locks = state ^ (LOCK_KEYS[code] & mask), mask
                self.sent_lock_keys.add(code) if down else self.sent_lock_keys.discard(code)
            try:
                # Pointer motion can follow ENTER without an extra round trip.
                # Actions wait for acceptance so a cancelled, unaccepted cross
                # cannot replay a click later. Subsequent motion waits too,
                # preserving the position and order of every queued action.
                if self.phase == "requesting" and (kind != Kind.MOVE or self.pending_actions):
                    self.pending.put(packet)
                    self.pending_actions = True
                else:
                    self._emit(packet)
            except Exception:
                self._reset("backpressure")
                return False
            return True

    def leave(self):
        with self.lock:
            if self.phase not in ("requesting", "sending"):
                return False
            try:
                self._emit(Packet(Kind.LEAVE, self.epoch))
            finally:
                # Local recovery never waits for a remote acknowledgement.
                self._reset()
            return True

    def receive(self, packet):
        with self.lock:
            if self.closed:
                return
            if packet.kind == Kind.ENTER:
                self._enter_received(packet)
                return
            if packet.epoch != self.epoch:
                return  # A delayed response must never reset a newer focus.
            if packet.kind == Kind.ACCEPT and self.phase == "requesting":
                self.deadline = None
                self._phase("sending")
                while (item := self.pending.pop()) is not None:
                    self._emit(item)
                self.pending_actions = False
            elif packet.kind in (Kind.REJECT, Kind.RESET):
                self._reset("peer")
            elif packet.kind == Kind.LEAVE and self.phase == "receiving":
                epoch = self.epoch
                self._reset()
                self._emit(Packet(Kind.LEFT, epoch))
            elif packet.kind in INPUT_KINDS and self.phase == "receiving":
                self._inject(packet)

    def _enter_received(self, packet):
        if self.phase == "receiving" and self.epoch == packet.epoch:
            self._emit(Packet(Kind.ACCEPT, packet.epoch))
            return
        # Resolve simultaneous physical sources consistently, without two owners.
        collision_lost = self.phase == "requesting" and self.role == "mac"
        point = self.desktop.clamp(*packet.values)
        if (
            (self.phase != "local" and not collision_lost)
            or packet.epoch in self.retired
            or not self.backend.ready()
            or not self.can_receive()
            or point != packet.values
            or not self.retry_releases()
        ):
            self._emit(Packet(Kind.REJECT, packet.epoch))
            return
        if collision_lost:
            self._reset()
            if self.unreleased or self.pending_locks is not None or self.pending_capture:
                self._emit(Packet(Kind.REJECT, packet.epoch))
                return
        self.epoch = packet.epoch
        try:
            self.backend.inject(Packet(Kind.MOVE, self.epoch, point))
            self._phase("receiving")
            self._emit(Packet(Kind.ACCEPT, self.epoch))
            self.failure = None
        except Exception:
            self._reset("injection")
            raise ConnectionError("No se pudo preparar la entrada") from None

    def _inject(self, packet):
        if not self.backend.ready():
            self.cancel("permission")
            return
        if packet.kind == Kind.MOVE and self.desktop.clamp(*packet.values) != packet.values:
            self.cancel("display")
            return
        if packet.kind == Kind.LOCKS:
            for code in self.backend.lock_keys(*packet.values):
                self._inject(Packet(Kind.KEY, self.epoch, (code, 1)))
                if self.phase != "receiving":
                    return
                self._inject(Packet(Kind.KEY, self.epoch, (code, 0)))
            return
        # Record before calling the OS: a backend may succeed partially and fail.
        held = self.keys if packet.kind == Kind.KEY else self.buttons if packet.kind == Kind.BUTTON else None
        if held is not None:
            code, down = packet.values
            if down:
                held.add(code)
            elif code not in held:
                return  # Never release input this session did not press.
        try:
            self.backend.inject(packet)
        except Exception:
            self.cancel("injection")
            raise ConnectionError("No se pudo entregar la entrada") from None
        if held is not None and not packet.values[1]:
            held.discard(packet.values[0])

    def cancel(self, reason="local"):
        with self.lock:
            try:
                if self.epoch and self.phase != "local":
                    self._emit(Packet(Kind.RESET, self.epoch))
            finally:
                self._reset(reason)

    def tick(self):
        # A healthy focus has no deadline. Avoid taking its lock on every
        # transport wakeup, especially in a 1000 Hz native mouse callback path.
        if self.deadline is None or self.clock() < self.deadline:
            return
        with self.lock:
            if self.deadline is not None and self.clock() >= self.deadline:
                self.cancel("timeout")

    def _reset(self, reason=None):
        # Do not retain native errors: they may contain input payloads. Try every
        # release even if one OS call fails. No retry loop can monopolize a CPU.
        self.failure = reason
        if self.outbound_locks is not None:
            self.pending_locks, self.outbound_locks = self.outbound_locks, None
        self.sent_lock_keys.clear()
        for kind, held in ((Kind.KEY, self.keys), (Kind.BUTTON, self.buttons)):
            for code in sorted(held):
                try:
                    self.backend.inject(Packet(kind, self.epoch, (code, 0)))
                except Exception:
                    self.unreleased.add((kind, code))
                    self.failure = "release"
            held.clear()
        self._restore_locks()
        try:
            self.backend.capture(False)
            self.pending_capture = False
        except Exception:
            self.pending_capture = True
            self.failure = "release"
        self.pending.close()
        self.pending = Outbox()
        self.pending_actions = False
        self.deadline = None
        if self.epoch:
            self.retired.append(self.epoch)
        self.epoch = 0
        self._phase("local")

    def _restore_locks(self):
        if self.pending_locks is None:
            return
        try:
            for code in self.backend.lock_keys(*self.pending_locks):
                # A partially accepted toggle still owns its release. Use the
                # same finite recovery ledger as ordinary remote keys.
                self.unreleased.add((Kind.KEY, code))
                try:
                    self.backend.inject(Packet(Kind.KEY, self.epoch or 1, (code, 1)))
                finally:
                    self.backend.inject(Packet(Kind.KEY, self.epoch or 1, (code, 0)))
                    self.unreleased.discard((Kind.KEY, code))
            self.pending_locks = None
        except Exception:
            self.failure = "locks"

    def retry_releases(self):
        """One finite retry on a new availability event, never a polling loop.

        Keep failed releases in memory until the OS permits them. A new focus
        cannot start while an older focus still owns an injected down state.
        """
        with self.lock:
            for kind, code in tuple(self.unreleased):
                try:
                    self.backend.inject(Packet(kind, self.epoch or 1, (code, 0)))
                except Exception:
                    continue
                self.unreleased.discard((kind, code))
            if not self.unreleased:
                self._restore_locks()
            if self.pending_capture:
                try:
                    self.backend.capture(False)
                    self.pending_capture = False
                except Exception:
                    pass
            return not self.unreleased and self.pending_locks is None and not self.pending_capture

    def close(self):
        with self.lock:
            self.closed = True
            self._reset("closed")
