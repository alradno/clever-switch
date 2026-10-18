"""Encrypted motion and ordered action barriers for a negotiated input channel.

Called by one transport owner. No sockets, native input, timers or threads are
created here. Keys belong to one pinned TLS connection and are retired on close.
"""

import dataclasses
import secrets
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .input_protocol import Kind, Packet

HEADER = struct.Struct("!4sB16sQ")
MOTION = struct.Struct("!QQQdd")
ACK = struct.Struct("!QQ")
MAX_COUNTER = 2**64 - 1
ACTIONS = frozenset((Kind.KEY, Kind.BUTTON, Kind.WHEEL, Kind.LOCKS))


def counter(value, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= MAX_COUNTER:
        raise ValueError("Invalid transport counter")


@dataclasses.dataclass(frozen=True, repr=False)
class Motion:
    epoch: int
    serial: int
    after_action: int
    position: tuple

    def __post_init__(self):
        counter(self.serial)
        counter(self.after_action, zero=True)
        Packet(Kind.MOVE, self.epoch, self.position)


@dataclasses.dataclass(frozen=True, repr=False)
class Receipt:
    epoch: int
    serial: int

    def __post_init__(self):
        counter(self.epoch)
        counter(self.serial)


@dataclasses.dataclass(frozen=True, repr=False)
class Probe:
    response: bool
    challenge: bytes

    def __post_init__(self):
        if type(self.response) is not bool or type(self.challenge) is not bytes or len(self.challenge) != 16:
            raise ValueError("Invalid path challenge")


@dataclasses.dataclass(frozen=True, repr=False)
class Reliable:
    kind: Kind
    epoch: int
    action: int
    serial: int
    position: tuple
    values: tuple = ()

    def __post_init__(self):
        counter(self.action, zero=self.kind == Kind.MOVE)
        counter(self.serial, zero=True)
        Packet(Kind.MOVE, self.epoch, self.position)
        if self.kind == Kind.MOVE:
            if self.values:
                raise ValueError("Unexpected snapshot payload")
        elif self.kind not in ACTIONS:
            raise ValueError("Invalid reliable input kind")
        else:
            Packet(self.kind, self.epoch, self.values)


class Codec:
    """One instance per role per TLS connection; never reset its nonce counter.

    Use a newly generated session secret on every reconnect, not a pairing key.
    Header and payload are authenticated; roles have different derived keys.
    Motion and receipts are the only datagrams. Actions never use this codec.
    """

    def __init__(self, secret, session_id, role):
        if type(secret) is not bytes or len(secret) != 32 or type(session_id) is not bytes or len(session_id) != 16:
            raise ValueError("Invalid ephemeral session material")
        if role not in ("windows", "mac"):
            raise ValueError("Invalid role")

        def cipher(sender):
            key = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=session_id,
                info=b"CleverSwitch/motion-lab/1/" + sender.encode(),
            ).derive(secret)
            return ChaCha20Poly1305(key)

        self.tx = cipher(role)
        self.rx = cipher("mac" if role == "windows" else "windows")
        self.session_id = session_id
        self.sent = self.received = 0

    def seal(self, message):
        if self.tx is None or self.sent == MAX_COUNTER:
            raise ConnectionError("Datagram key is retired")
        if isinstance(message, Motion):
            payload = b"M" + MOTION.pack(message.epoch, message.serial, message.after_action, *message.position)
        elif isinstance(message, Receipt):
            payload = b"A" + ACK.pack(message.epoch, message.serial)
        elif isinstance(message, Probe):
            payload = (b"R" if message.response else b"Q") + message.challenge
        else:
            raise ValueError("Invalid datagram message")
        self.sent += 1
        header = HEADER.pack(b"CSML", 1, self.session_id, self.sent)
        return header + self.tx.encrypt(self.sent.to_bytes(12, "big"), payload, header)

    def open(self, raw):
        # Size and session rejection precede decryption. Forged counters never
        # advance replay state. Old authenticated datagrams cannot move backwards.
        if (
            self.rx is None
            or type(raw) is not bytes
            or len(raw) not in (HEADER.size + 17 + MOTION.size, HEADER.size + 17 + ACK.size)
        ):
            return None
        magic, version, session_id, sequence = HEADER.unpack_from(raw)
        if magic != b"CSML" or version != 1 or session_id != self.session_id or sequence <= self.received:
            return None
        try:
            payload = self.rx.decrypt(sequence.to_bytes(12, "big"), raw[HEADER.size :], raw[: HEADER.size])
            if len(payload) == 1 + MOTION.size and payload[0:1] == b"M":
                epoch, serial, action, x, y = MOTION.unpack(payload[1:])
                result = Motion(epoch, serial, action, (x, y))
            elif len(payload) == 1 + ACK.size and payload[0:1] == b"A":
                result = Receipt(*ACK.unpack(payload[1:]))
            elif len(payload) == 17 and payload[0:1] in (b"Q", b"R"):
                result = Probe(payload[0:1] == b"R", payload[1:])
            else:
                return None
        except (InvalidTag, ValueError, struct.error):
            return None
        self.received = sequence
        return result

    def close(self):
        # Drop references; Python cannot promise secure erasure of heap copies.
        self.tx = self.rx = None



class PathProbe:
    """One finite check after TLS authentication, before selecting the fast path.

    A failed check leaves input on the reliable path. No periodic retry is
    scheduled; a later connection/lifecycle event may create a new probe.
    """

    offsets = (0, 0.016, 0.048)
    timeout = 0.1

    def __init__(self, codec, datagram):
        self.codec, self.datagram = codec, datagram
        self.challenge = secrets.token_bytes(16)
        self.state = "new"
        self.started = None
        self.attempts = 0

    @property
    def deadline(self):
        if self.state != "pending":
            return None
        return self.started + (self.offsets[self.attempts] if self.attempts < len(self.offsets) else self.timeout)

    def start(self, now):
        if self.state != "new":
            return
        self.started, self.state = now, "pending"
        self.tick(now)

    def tick(self, now):
        if self.deadline is None or now < self.deadline:
            return
        if now >= self.started + self.timeout:
            self.state = "unavailable"
            return
        self.attempts += 1
        try:
            self.datagram(self.codec.seal(Probe(False, self.challenge)))
        except OSError:
            self.state = "unavailable"

    def receive(self, raw, now):
        self.receive_message(self.codec.open(raw), now)

    def receive_message(self, message, now):
        if self.state != "pending":
            return
        if now >= self.started + self.timeout:
            self.state = "unavailable"
            return
        if isinstance(message, Probe) and message.response and message.challenge == self.challenge:
            self.state = "ready"


class Sender:
    retry_offsets = (0.008, 0.024, 0.056)
    checkpoint_interval = 0.016
    acknowledgement_timeout = 0.1

    def __init__(self, codec, epoch, position, datagram, reliable, *, probe=None):
        Packet(Kind.MOVE, epoch, position)
        self.codec, self.epoch, self.position = codec, epoch, position
        self.datagram, self.reliable = datagram, reliable
        self.serial = self.action = self.acknowledged = 0
        self.latest = None
        self.unacknowledged_since = self.retry_at = self.checkpoint_at = None
        self.retry_origin = 0
        self.retries = 0
        self.mode = "datagram" if probe is not None and probe.codec is codec and probe.state == "ready" else "reliable"

    @property
    def deadline(self):
        if self.mode != "datagram" or self.latest is None:
            return None
        values = [self.unacknowledged_since + self.acknowledgement_timeout]
        if self.retry_at is not None:
            values.append(self.retry_at)
        if self.checkpoint_at is not None:
            values.append(self.checkpoint_at)
        return min(values)

    def _fallback(self):
        self.mode = "reliable"
        self.latest = None
        self.retry_at = self.checkpoint_at = self.unacknowledged_since = None
        self.reliable(Reliable(Kind.MOVE, self.epoch, self.action, self.serial, self.position))

    def _send_latest(self):
        try:
            self.datagram(self.codec.seal(self.latest))
        except OSError:
            self._fallback()

    def move(self, position, now, *, reliable=False):
        if self.mode == "closed":
            return
        Packet(Kind.MOVE, self.epoch, position)
        counter(self.serial + 1)
        self.serial += 1
        self.position = position
        if self.mode == "reliable" or reliable:
            self.reliable(Reliable(Kind.MOVE, self.epoch, self.action, self.serial, position))
            return
        self.latest = Motion(self.epoch, self.serial, self.action, position)
        if self.unacknowledged_since is None:
            self.unacknowledged_since = now
            self.checkpoint_at = now + self.checkpoint_interval
        self.retry_origin, self.retries = now, 0
        self.retry_at = now + self.retry_offsets[0]
        self._send_latest()

    def send_action(self, kind, *values):
        if self.mode == "closed":
            return
        frame = Reliable(kind, self.epoch, self.action + 1, self.serial, self.position, values)
        if kind not in ACTIONS:
            raise ValueError("Invalid action")
        self.action += 1
        self.reliable(frame)
        if self.latest is not None:
            self.latest = Motion(self.epoch, self.serial, self.action, self.position)

    def receive(self, raw, now):
        self.receive_message(self.codec.open(raw), now)

    def receive_message(self, message, now):
        if self.mode != "datagram" or not isinstance(message, Receipt) or message.epoch != self.epoch:
            return
        if not self.acknowledged < message.serial <= self.serial:
            return
        self.acknowledged = message.serial
        if message.serial == self.serial:
            self.latest = None
            self.retry_at = self.checkpoint_at = self.unacknowledged_since = None
        elif self.latest is not None:
            self.unacknowledged_since = now
            self.checkpoint_at = now + self.checkpoint_interval

    def tick(self, now):
        if self.deadline is None or now < self.deadline:
            return
        if now >= self.unacknowledged_since + self.acknowledgement_timeout:
            self._fallback()
            return
        if self.checkpoint_at is not None and now >= self.checkpoint_at:
            # Keep pointer progress if a previously usable UDP path disappears.
            # Healthy ACKs cancel this work; it ends at the same finite fallback
            # deadline. A late snapshot cannot override a newer datagram.
            self.reliable(Reliable(Kind.MOVE, self.epoch, self.action, self.serial, self.position))
            self.checkpoint_at = now + self.checkpoint_interval
        if self.retry_at is None or now < self.retry_at:
            return
        self.retries += 1
        self.retry_at = (
            self.retry_origin + self.retry_offsets[self.retries] if self.retries < len(self.retry_offsets) else None
        )
        self._send_latest()

    def stop(self):
        self.mode = "closed"
        self.latest = None
        self.retry_at = self.checkpoint_at = self.unacknowledged_since = None


class Receiver:
    def __init__(self, codec, datagram, deliver):
        self.codec, self.datagram, self.deliver = codec, datagram, deliver
        self.epoch = None
        self.position = self.pending = None
        self.serial = self.action = 0

    def begin(self, epoch, position):
        """Only the reliable, authenticated focus owner may call this method."""
        Packet(Kind.MOVE, epoch, position)
        if self.epoch is not None:
            raise RuntimeError("A focus is already active")
        self.epoch, self.position = epoch, position
        self.serial = self.action = 0
        self.pending = None

    def end(self):
        # The existing Session must release pressed input when ending focus.
        self.epoch = None
        self.position = self.pending = None
        self.serial = self.action = 0

    def _position(self, serial, position):
        if serial < self.serial or (serial == self.serial and position != self.position):
            raise ValueError("Conflicting action position")
        if serial != self.serial:
            self.deliver(Packet(Kind.MOVE, self.epoch, position))
            self.serial, self.position = serial, position

    def receive(self, raw):
        self.receive_message(self.codec.open(raw))

    def receive_message(self, message):
        if isinstance(message, Probe) and not message.response:
            try:
                self.datagram(self.codec.seal(Probe(True, message.challenge)))
            except OSError:
                pass
            return
        if not isinstance(message, Motion) or message.epoch != self.epoch:
            return
        # ACK means authenticated receipt, not that a blocked action was applied.
        try:
            self.datagram(self.codec.seal(Receipt(message.epoch, message.serial)))
        except OSError:
            pass
        if message.serial <= self.serial or message.after_action < self.action:
            return
        if message.after_action > self.action:
            if self.pending is None or (message.serial, message.after_action) > (
                self.pending.serial,
                self.pending.after_action,
            ):
                self.pending = message
            return
        self._position(message.serial, message.position)

    def receive_reliable(self, frame):
        if not isinstance(frame, Reliable):
            raise ValueError("Invalid reliable frame")
        if frame.epoch != self.epoch:
            return
        if frame.kind == Kind.MOVE:
            if frame.action != self.action:
                raise ValueError("Out-of-order reliable snapshot")
            if frame.serial <= self.serial:
                return  # A newer datagram can have beaten a fallback snapshot.
            self._position(frame.serial, frame.position)
        else:
            if frame.action != self.action + 1:
                raise ValueError("Out-of-order reliable action")
            self._position(frame.serial, frame.position)
            self.deliver(Packet(frame.kind, frame.epoch, frame.values))
            self.action = frame.action
        pending = self.pending
        if pending is not None and pending.after_action <= self.action:
            self.pending = None
            if pending.after_action == self.action and pending.serial > self.serial:
                self._position(pending.serial, pending.position)
