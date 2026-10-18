"""Pinned TLS key bootstrap and bounded routing on the shared UDP listener.

Ciphertext is queued by Discovery; only the input channel owner decrypts it.
The module creates no sockets, OS hooks, workers or periodic timers.
"""

import collections
import hashlib
import secrets
import threading
import time

from .identity import private_ip, private_network
from .input_motion import ACK, HEADER, MOTION, Codec
from .network import receive, remaining_timeout, send

CAPABILITY = "motion-datagram-v1"
PACKET_SIZES = frozenset((HEADER.size + 17 + ACK.size, HEADER.size + 17 + MOTION.size))


def supports_motion(capabilities):
    return type(capabilities) is dict and type(capabilities.get(CAPABILITY)) is int and capabilities[CAPABILITY] == 1


def _hex(value, size):
    if type(value) is not str or len(value) != size * 2:
        raise ValueError("Invalid motion negotiation")
    try:
        result = bytes.fromhex(value)
    except ValueError:
        raise ValueError("Invalid motion negotiation") from None
    if len(result) != size or result.hex() != value:
        raise ValueError("Invalid motion negotiation")
    return result


class Negotiation:
    """Fresh, single-use state for ONE authenticated input connection.

    Windows initiates the existing input upgrade regardless of physical input
    direction. The server supplies a fresh ephemeral secret, never a pairing key.
    Its reply echoes the client's fresh challenge. Both challenge and link ID
    bind the derived session ID, so another upgrade's reply cannot be substituted.
    The caller must close this object on every failure and when Channel closes.
    Python drops key references on close; it cannot promise heap erasure.
    """

    def __init__(self, role, link_id):
        if role not in ("windows", "mac") or type(link_id) is not str or not 1 <= len(link_id) <= 128:
            raise ValueError("Invalid motion identity")
        self.role, self.link_id = role, link_id
        self.state = "new"
        self.challenge = None
        self.codec = None

    def _expect(self, role, state):
        if self.role != role or self.state != state:
            raise RuntimeError("Motion negotiation is not reusable")

    def offer(self, capabilities):
        self._expect("windows", "new")
        if not supports_motion(capabilities):
            self.state = "reliable"
            return None
        self.challenge = secrets.token_hex(16)
        self.state = "offered"
        return {"version": 1, "challenge": self.challenge}

    def _codec(self, secret, salt):
        # Fixed domain and length-bounded identity avoid ambiguous transcripts.
        identity = self.link_id.encode("utf-8")
        context = b"CleverSwitch/motion-bootstrap/1/" + len(identity).to_bytes(2, "big") + identity
        session_id = hashlib.sha256(context + _hex(self.challenge, 16) + salt).digest()[:16]
        self.codec = Codec(secret, session_id, self.role)
        self.challenge = None
        self.state = "ready"

    def accept(self, offer):
        self._expect("mac", "new")
        if offer is None:
            self.state = "reliable"
            return None
        try:
            if type(offer) is not dict or set(offer) != {"version", "challenge"}:
                raise ValueError("Invalid motion negotiation")
            if type(offer["version"]) is not int or offer["version"] != 1:
                raise ValueError("Invalid motion negotiation")
            _hex(offer["challenge"], 16)
            self.challenge = offer["challenge"]
            secret, salt = secrets.token_bytes(32), secrets.token_bytes(16)
            reply = {"version": 1, "challenge": self.challenge, "salt": salt.hex(), "secret": secret.hex()}
            self._codec(secret, salt)
            return reply
        except (ValueError, TypeError):
            self.close()
            raise

    def finish(self, reply):
        self._expect("windows", "offered")
        if reply is None:
            self.challenge = None
            self.state = "reliable"
            return
        try:
            if type(reply) is not dict or set(reply) != {"version", "challenge", "salt", "secret"}:
                raise ValueError("Invalid motion negotiation")
            if type(reply["version"]) is not int or reply["version"] != 1 or reply["challenge"] != self.challenge:
                raise ValueError("Invalid motion negotiation")
            self._codec(_hex(reply["secret"], 32), _hex(reply["salt"], 16))
        except (ValueError, TypeError):
            self.close()
            raise

    def close(self):
        if self.codec is not None:
            self.codec.close()
        self.codec = self.challenge = None
        self.state = "closed"


def bootstrap(connection, link, *, timeout=0.5):
    """Run on the sole TLS owner, only after both peers selected the extension.

    Link has already authenticated the upgrade. Recheck the pin and exact peer
    here before generating/exchanging ephemeral material. On error no live key
    escapes; the owning Channel must close the connection as well. The returned
    negotiation belongs to that Channel and must close with it.
    """
    if type(timeout) not in (int, float) or not 0 < timeout <= 2:
        raise ValueError("Invalid bootstrap deadline")
    deadline = time.monotonic() + timeout
    link.pairing.verify_pin(connection)
    address = private_ip(connection.getpeername()[0], link.pairing.testing)
    private_network(link.config["network"], address, link.pairing.testing)
    if address != link.peer:
        raise ValueError("Unexpected motion peer")
    negotiation = Negotiation(link.config["role"], link.config["link"])
    try:
        if negotiation.role == "windows":
            offer = negotiation.offer({CAPABILITY: 1})
            remaining_timeout(connection, deadline)
            send(connection, offer)
            negotiation.finish(receive(connection, deadline))
        else:
            reply = negotiation.accept(receive(connection, deadline))
            remaining_timeout(connection, deadline)
            send(connection, reply)
        return negotiation
    except Exception:
        negotiation.close()
        raise


class Inbox:
    """Bounded ciphertext handoff from the UDP listener to the channel owner."""

    capacity = 64

    def __init__(self, session_id, peer, wake):
        self.session_id, self.peer = session_id, peer
        self.wake = wake
        self.lock = threading.Lock()
        self.queue = collections.deque(maxlen=self.capacity)
        self.closed = False
        self.dropped = 0

    def put(self, raw, sender):
        if type(raw) is not bytes or len(raw) not in PACKET_SIZES or sender != self.peer:
            return False
        magic, version, session_id, sequence = HEADER.unpack_from(raw)
        if magic != b"CSML" or version != 1 or session_id != self.session_id or sequence == 0:
            return False
        with self.lock:
            if self.closed:
                return False
            wake = not self.queue
            if len(self.queue) == self.capacity:
                self.dropped += 1
            self.queue.append(raw)
        if wake:
            self.wake()
        return True

    def take(self):
        with self.lock:
            packets = tuple(self.queue)
            self.queue.clear()
            return packets

    def close(self):
        with self.lock:
            self.closed = True
            self.queue.clear()


class DatagramMux:
    """Share one existing UDP listener without changing its ports or firewall.

    Register only after pinned TLS succeeds, using that connection's actual peer
    IP and the saved local subnet. The application integration must keep the
    existing fixed UDP port; ephemeral ports are used only by loopback tests.
    This object does not authenticate ciphertext. It rejects unrelated sources
    cheaply and hands a bounded batch to the sole codec/session owner.
    """

    def __init__(self, discovery):
        self.discovery = discovery
        self.lock = threading.Lock()
        self.current = None
        self.closed = False

    def register(self, codec, peer, network, wake, *, testing=False):
        address, port = peer
        address = private_ip(address, testing)
        private_network(network, address, testing)
        if type(port) is not int or not 1 <= port <= 65535 or codec.tx is None or codec.rx is None:
            raise ValueError("Invalid motion route")
        inbox = Inbox(codec.session_id, (address, port), wake)
        with self.lock:
            if self.closed:
                raise ConnectionError("Datagram listener closed")
            if self.current is not None:
                self.current.close()
            self.current = inbox
        return inbox

    def unregister(self, inbox):
        # A late close from the previous TLS connection cannot remove a new one.
        with self.lock:
            inbox.close()
            if self.current is inbox:
                self.current = None

    def receive(self, raw, sender):
        with self.lock:
            if self.closed:
                return False
            inbox = self.current
        if type(raw) is not bytes or len(raw) > 512:
            return False
        if raw.startswith(b"CSML"):
            return inbox is not None and inbox.put(raw, sender)
        return self.discovery(raw, sender[0])

    def close(self):
        with self.lock:
            self.closed = True
            if self.current is not None:
                self.current.close()
                self.current = None
