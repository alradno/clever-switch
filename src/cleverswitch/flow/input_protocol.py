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

    def encode(self, sequence):
        if type(sequence) is not int or not 0 < sequence < 2**64:
            raise ValueError("Secuencia de entrada inválida")
        payload = FORMATS[self.kind].pack(*self.values)
        return HEADER.pack(MAGIC, VERSION, self.kind, len(payload), sequence, self.epoch) + payload


class Decoder:
    def __init__(self):
        self.buffer = bytearray()
        self.sequence = 0

    def feed(self, data):
        # The caller supplies bounded socket reads. Reject oversized direct input
        # before allocating; the parser never accumulates an unbounded frame.
        if len(data) > 65536:
            raise ValueError("Lectura de entrada demasiado grande")
        self.buffer.extend(data)
        packets = []
        while len(self.buffer) >= HEADER.size:
            magic, version, code, size, sequence, epoch = HEADER.unpack_from(self.buffer)
            try:
                kind = Kind(code)
            except ValueError:
                raise ValueError("Tipo de entrada inválido") from None
            if magic != MAGIC or version != VERSION or size > LIMIT or size != FORMATS[kind].size:
                raise ValueError("Formato de entrada inválido")
            if sequence != self.sequence + 1:
                raise ValueError("Secuencia de entrada inválida")
            total = HEADER.size + size
            if len(self.buffer) < total:
                break
            values = FORMATS[kind].unpack_from(self.buffer, HEADER.size)
            packets.append(Packet(kind, epoch, values))
            del self.buffer[:total]
            self.sequence = sequence
        return packets

    def clear(self):
        self.buffer.clear()


class Outbox:
    """Nonblocking producer; bounded reliable actions, adjacent motion coalesced.

    Coalescing across a click, key, wheel or focus boundary would change its
    meaning, so only the last unsent motion of the same focus can be replaced.
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
        if not isinstance(packet, Packet):
            raise ValueError("Evento de entrada inválido")
        with self.lock:
            if self.closed:
                raise ConnectionError("Canal de entrada cerrado")
            empty = not self.items
            if (
                packet.kind == Kind.MOVE
                and self.items
                and self.items[-1].kind == Kind.MOVE
                and self.items[-1].epoch == packet.epoch
            ):
                self.items[-1] = packet
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

    def close(self):
        with self.lock:
            self.closed = True
            self.items.clear()
