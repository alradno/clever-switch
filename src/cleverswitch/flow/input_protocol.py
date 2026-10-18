"""Versioned input frames. Never log or format their transient payloads.

Only use this protocol inside an already authenticated, explicitly enabled TLS
session. USB HID keyboard usages, not characters, cross the wire. Motion uses
absolute desktop coordinates so coalescing cannot lose relative displacement.
"""

import collections
import dataclasses
import enum
import math
import struct
import threading

VERSION = 1
HEADER = struct.Struct("!4sBBHQQ")
MAGIC = b"CSFI"
LIMIT = 128
ROUTED = 14  # Negotiated extension; legacy channels never accept or send it.
ROUTE = struct.Struct("!BQQdd")
TIMED, ROUTED_TIMED = 15, 16
STAMP = struct.Struct("!Q")
TIMING_CAPABILITY = "click-timing-v1"


def supports_timing(capabilities):
    return type(capabilities) is dict and type(capabilities.get(TIMING_CAPABILITY)) is int and (
        capabilities[TIMING_CAPABILITY] == 1
    )


class Kind(enum.IntEnum):
    ENTER = 1
    ACCEPT = 2
    REJECT = 3
    LEAVE = 4
    LEFT = 5
    MOVE = 6
    KEY = 7
    BUTTON = 8
    WHEEL = 9
    RESET = 10
    PING = 11
    PONG = 12
    LOCKS = 13


FORMATS = {kind: struct.Struct("") for kind in Kind}
FORMATS.update(
    {
        Kind.ENTER: struct.Struct("!dd"),
        Kind.MOVE: struct.Struct("!dd"),
        Kind.KEY: struct.Struct("!HB"),
        Kind.BUTTON: struct.Struct("!BB"),
        Kind.WHEEL: struct.Struct("!dd"),
        Kind.LOCKS: struct.Struct("!BB"),
    }
)


@dataclasses.dataclass(frozen=True, repr=False)
class Packet:
    kind: Kind
    epoch: int
    values: tuple = ()
    stamp: int | None = None

    def __post_init__(self):
        if type(self.kind) is not Kind or type(self.epoch) is not int or not 0 < self.epoch < 2**64:
            raise ValueError("Evento de entrada inválido")
        if type(self.values) is not tuple:
            raise ValueError("Evento de entrada inválido")
        if self.kind in (Kind.MOVE, Kind.ENTER, Kind.WHEEL):
            bound = 1_000_000 if self.kind != Kind.WHEEL else 1000
            valid = len(self.values) == 2 and all(
                type(v) in (int, float) and math.isfinite(v) and abs(v) <= bound for v in self.values
            )
        elif self.kind == Kind.LOCKS:
            valid = len(self.values) == 2 and all(type(v) is int and 0 <= v <= 7 for v in self.values)
        elif self.kind in (Kind.KEY, Kind.BUTTON):
            valid = len(self.values) == 2 and all(type(v) is int for v in self.values)
            if valid:
                code, down = self.values
                valid = down in (0, 1) and (
                    (4 <= code <= 0xA4 or 0xE0 <= code <= 0xE7) if self.kind == Kind.KEY else 0 <= code <= 4
                )
        else:
            valid = not self.values
        if not valid:
            raise ValueError("Evento de entrada inválido")
        if self.stamp is not None and (
            self.kind not in (Kind.BUTTON, Kind.ACCEPT)
            or type(self.stamp) is not int or not 0 <= self.stamp < 2**64
        ):
            raise ValueError("Tiempo de entrada inválido")

    def encode(self, sequence):
        if type(sequence) is not int or not 0 < sequence < 2**64:
            raise ValueError("Secuencia de entrada inválida")
        payload = FORMATS[self.kind].pack(*self.values)
        kind = self.kind
        if self.stamp is not None:
            payload = bytes((kind,)) + STAMP.pack(self.stamp) + payload
            kind = TIMED
        return HEADER.pack(MAGIC, VERSION, kind, len(payload), sequence, self.epoch) + payload


@dataclasses.dataclass(frozen=True, repr=False)
class Routed:
    packet: Packet
    action: int
    serial: int
    position: tuple

    def __post_init__(self):
        if not isinstance(self.packet, Packet) or self.kind not in (
            Kind.MOVE, Kind.KEY, Kind.BUTTON, Kind.WHEEL, Kind.LOCKS
        ):
            raise ValueError("Acción de transporte inválida")
        if (
            type(self.action) is not int
            or not (0 if self.kind == Kind.MOVE else 1) <= self.action < 2**64
            or type(self.serial) is not int
            or not 0 <= self.serial < 2**64
        ):
            raise ValueError("Secuencia de transporte inválida")
        Packet(Kind.MOVE, self.epoch, self.position)
        if self.kind == Kind.MOVE and self.packet.values != self.position:
            raise ValueError("Posición de transporte inválida")

    @property
    def kind(self):
        return self.packet.kind

    @property
    def epoch(self):
        return self.packet.epoch

    def encode(self, sequence):
        if type(sequence) is not int or not 0 < sequence < 2**64:
            raise ValueError("Secuencia de entrada inválida")
        payload = ROUTE.pack(self.kind, self.action, self.serial, *self.position)
        if self.kind != Kind.MOVE:
            payload += FORMATS[self.kind].pack(*self.packet.values)
        code = ROUTED
        if self.packet.stamp is not None:
            payload += STAMP.pack(self.packet.stamp)
            code = ROUTED_TIMED
        return HEADER.pack(MAGIC, VERSION, code, len(payload), sequence, self.epoch) + payload

    @classmethod
    def decode(cls, epoch, payload, *, timed=False):
        if len(payload) < ROUTE.size:
            raise ValueError("Formato de transporte inválido")
        code, action, serial, x, y = ROUTE.unpack_from(payload)
        kind = Kind(code)
        expected = 0 if kind == Kind.MOVE else FORMATS[kind].size
        if len(payload) != ROUTE.size + expected + (STAMP.size if timed else 0):
            raise ValueError("Formato de transporte inválido")
        values = (x, y) if kind == Kind.MOVE else FORMATS[kind].unpack_from(payload, ROUTE.size)
        stamp = STAMP.unpack_from(payload, ROUTE.size + expected)[0] if timed else None
        return cls(Packet(kind, epoch, values, stamp), action, serial, (x, y))


class Decoder:
    def __init__(self, *, routed=False, timed=False):
        self.buffer = bytearray()
        self.sequence = 0
        self.routed = routed
        self.timed = timed

    def feed(self, data):
        # The caller supplies bounded socket reads. Reject oversized direct input
        # before allocating; the parser never accumulates an unbounded frame.
        if len(data) > 65536:
            raise ValueError("Lectura de entrada demasiado grande")
        self.buffer.extend(data)
        packets = []
        while len(self.buffer) >= HEADER.size:
            magic, version, code, size, sequence, epoch = HEADER.unpack_from(self.buffer)
            extended = self.routed and (code == ROUTED or self.timed and code == ROUTED_TIMED)
            timed = self.timed and code == TIMED
            if not extended and not timed:
                try:
                    kind = Kind(code)
                except ValueError:
                    raise ValueError("Tipo de entrada inválido") from None
            if extended:
                valid_size = ROUTE.size <= size <= ROUTE.size + 16 + STAMP.size
            elif timed:
                valid_size = 1 + STAMP.size <= size <= 1 + STAMP.size + 2
            else:
                valid_size = size == FORMATS[kind].size
            if magic != MAGIC or version != VERSION or size > LIMIT or not valid_size:
                raise ValueError("Formato de entrada inválido")
            if sequence != self.sequence + 1:
                raise ValueError("Secuencia de entrada inválida")
            total = HEADER.size + size
            if len(self.buffer) < total:
                break
            if extended:
                packets.append(Routed.decode(epoch, self.buffer[HEADER.size:total], timed=code == ROUTED_TIMED))
            elif timed:
                kind = Kind(self.buffer[HEADER.size])
                if kind not in (Kind.BUTTON, Kind.ACCEPT) or size != 1 + STAMP.size + FORMATS[kind].size:
                    raise ValueError("Formato de tiempo inválido")
                stamp = STAMP.unpack_from(self.buffer, HEADER.size + 1)[0]
                values = FORMATS[kind].unpack_from(self.buffer, HEADER.size + 1 + STAMP.size)
                packets.append(Packet(kind, epoch, values, stamp))
            else:
                values = FORMATS[kind].unpack_from(self.buffer, HEADER.size)
                packets.append(Packet(kind, epoch, values))
            del self.buffer[:total]
            self.sequence = sequence
        return packets

    def clear(self):
        self.buffer.clear()


def replace_motion(items, packet):
    """Replace unsent motion across heartbeats, never across an input action.

    PING/PONG do not observe a pointer position or change focus. Keeping an old
    move merely because a heartbeat follows it replays that position after a
    scheduler/network stall. Preserve every heartbeat and its relative order.
    The caller owns this bounded list/deque or holds its queue lock.
    """
    if packet.kind != Kind.MOVE:
        return False
    for index in range(len(items) - 1, -1, -1):
        previous = items[index]
        if previous.kind in (Kind.PING, Kind.PONG):
            continue
        if (
            previous.kind == Kind.MOVE
            and previous.epoch == packet.epoch
            and type(previous) is type(packet)
            and (not isinstance(packet, Routed) or previous.action == packet.action)
        ):
            if not isinstance(packet, Routed) or packet.serial >= previous.serial:
                items[index] = packet
            return True
        break
    return False


class Outbox:
    """Nonblocking producer; bounded reliable actions, obsolete motion coalesced.

    Coalescing across a click, key, wheel or focus boundary would change its
    meaning. Link heartbeats preserve those boundaries without retaining stale
    motion of the same focus.
    """

    def __init__(self, capacity=256):
        if type(capacity) is not int or not 2 <= capacity <= 4096:
            raise ValueError("Capacidad inválida")
        self.capacity = capacity
        self.items = collections.deque()
        self.lock = threading.Lock()
        self.closed = False
        self.coalesced = 0

    def put(self, packet):
        if not isinstance(packet, (Packet, Routed)):
            raise ValueError("Evento de entrada inválido")
        with self.lock:
            if self.closed:
                raise ConnectionError("Canal de entrada cerrado")
            empty = not self.items
            if replace_motion(self.items, packet):
                self.coalesced += 1
            elif len(self.items) < self.capacity:
                self.items.append(packet)
            else:
                raise BufferError("Cola de entrada saturada")
            return empty

    def pop(self):
        with self.lock:
            return self.items.popleft() if self.items else None

    def take(self, limit=64):
        # Drain only work that already exists; batching adds no waiting period.
        with self.lock:
            return [self.items.popleft() for _ in range(min(limit, len(self.items)))]

    def pending(self):
        with self.lock:
            return bool(self.items)

    def close(self):
        with self.lock:
            self.closed = True
            self.items.clear()
