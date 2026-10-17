"""Bounded TLS RPC and authenticated LAN announcements after offline pairing."""

import base64
import collections
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import select
import socket
import struct
import threading
import time

from .identity import PORT, canonical, local_addresses, private_ip

log = logging.getLogger(__name__)
MAX_MESSAGE = 2048
OPS = frozenset(("ready", "prepare", "mouse", "keyboard", "commit", "status", "abort", "return", "follow", "input"))


def remaining_timeout(connection, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Se agotó el plazo de comunicación con el otro equipo")
    connection.settimeout(remaining)


def receive_exact(connection, size, deadline=None):
    data = bytearray()
    while len(data) < size:
        if deadline is not None:
            remaining_timeout(connection, deadline)
        part = connection.recv(size - len(data))
        if not part:
            raise ConnectionError("Enlace interrumpido")
        data.extend(part)
    return bytes(data)


def receive(connection, deadline=None):
    size = struct.unpack("!I", receive_exact(connection, 4, deadline))[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("Mensaje inválido")
    value = json.loads(receive_exact(connection, size, deadline))
    if not isinstance(value, dict):
        raise ValueError("Mensaje inválido")
    return value


def send(connection, value):
    raw = canonical(value)
    if len(raw) > MAX_MESSAGE:
        raise ValueError("Mensaje demasiado grande")
    connection.sendall(struct.pack("!I", len(raw)) + raw)


class Link:
    def __init__(self, pairing, handler, port=PORT):
        self.pairing = pairing
        self.config = pairing.load()
        self.handler = handler
        self.port = port
        self.address = self.config["address"]
        self.peer = self.config["peer"]["address"]
        self.server_context = pairing.tls(True)
        self.client_context = pairing.tls()
        self.stop_event = threading.Event()
        self.listener = None
        self.worker = None
        self.replays = collections.deque(maxlen=512)
        self.error = None
        self.on_authenticated = lambda: None
        self.on_announcement = lambda: None
        self.on_departure = lambda: None
        self.on_failure = lambda: None
        self.wake_reader = self.wake_writer = None
        # Explicitly installed by the opted-in input service, never by pairing.
        self.input_accept = None
        self.input_channel = None
        self.input_lock = threading.Lock()
        self.input_opening = False

    def start(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener = listener
        # Never all-interfaces or public listening. OS firewall policy is unchanged.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.address, self.port))
        self.port = listener.getsockname()[1]
        listener.listen(4)
        listener.setblocking(False)
        self.wake_reader, self.wake_writer = socket.socketpair()
        self.worker = threading.Thread(target=self._serve, name="flow-tls", daemon=True)
        self.worker.start()

    def _serve(self):
        # One handshake worker bounds resource use. Input may upgrade a verified
        # connection to one separately owned stream; RPC remains responsive.
        while not self.stop_event.is_set():
            try:
                readable, _, _ = select.select([self.listener, self.wake_reader], [], [])
                if self.wake_reader in readable:
                    return
                raw, remote = self.listener.accept()
            except BlockingIOError:
                continue
            except OSError:
                return
            with raw:
                connection = None
                handed_off = False
                try:
                    raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    private_ip(remote[0], self.pairing.testing)
                    if remote[0] != self.peer:
                        continue
                    deadline = time.monotonic() + 2.5
                    remaining_timeout(raw, deadline)
                    connection = self.server_context.wrap_socket(raw, server_side=True)
                    self.pairing.verify_pin(connection)
                    self.on_authenticated()
                    request = receive(connection, deadline)
                    if set(request) != {"link", "id", "op", "args"}:
                        raise ValueError("Formato inválido")
                    if request["link"] != self.config["link"] or request["op"] not in OPS:
                        raise ValueError("Operación inválida")
                    nonce = request["id"]
                    if not isinstance(nonce, str) or len(nonce) != 32 or nonce in self.replays:
                        raise ValueError("Solicitud repetida")
                    if not isinstance(request["args"], dict):
                        raise ValueError("Parámetros inválidos")
                    self.replays.append(nonce)
                    if request["op"] == "input":
                        with self.input_lock:
                            if (
                                self.stop_event.is_set()
                                or self.input_accept is None
                                or self.input_opening
                                or (self.input_channel is not None and not self.input_channel.finished.is_set())
                            ):
                                raise ValueError("Entrada por red no disponible")
                            result, factory = self.input_accept(request["args"])
                            remaining_timeout(connection, deadline)
                            send(connection, {"id": nonce, "result": result})
                            channel = factory(connection)
                            try:
                                channel.start()
                            except Exception:
                                channel.close()
                                raise
                            self.input_channel = channel
                            handed_off = True
                    else:
                        result = self.handler(request["op"], request["args"])
                        remaining_timeout(connection, deadline)
                        send(connection, {"id": nonce, "result": result})
                except Exception:
                    # No peer payload, certificates or pairing secrets in logs.
                    log.debug("Rejected or interrupted local peer request")
                finally:
                    if connection is not None and not handed_off:
                        connection.close()

    def call(self, op, args=None, timeout=3.0):
        try:
            return self._call(op, args, timeout)
        except Exception:
            self.on_failure()
            raise

    def _call(self, op, args=None, timeout=3.0):
        if self.stop_event.is_set():
            raise ConnectionError("Enlace cerrado")
        if op not in OPS or op == "input":
            raise ValueError("Operación inválida")
        # One budget spans TCP, TLS, writes and every received fragment. Socket
        # timeouts alone restart on each read and can outlive transfer recovery.
        deadline = time.monotonic() + timeout
        nonce = secrets.token_hex(16)
        peer = private_ip(self.peer, self.pairing.testing)
        with socket.create_connection((peer, self.port), timeout=timeout, source_address=(self.address, 0)) as raw:
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remaining_timeout(raw, deadline)
            with self.client_context.wrap_socket(raw) as connection:
                self.pairing.verify_pin(connection)
                remaining_timeout(connection, deadline)
                send(connection, {"link": self.config["link"], "id": nonce, "op": op, "args": args or {}})
                reply = receive(connection, deadline)
                if set(reply) != {"id", "result"} or reply["id"] != nonce or not isinstance(reply["result"], dict):
                    raise ValueError("Respuesta inválida")
                self.on_authenticated()
                return reply["result"]

    def open_input(self, args, factory, timeout=2):
        """Upgrade once; ownership of the pinned connection goes to Channel.

        Only one endpoint (Windows) initiates in production. Physical source and
        input destination can subsequently change independently of that role.
        """
        with self.input_lock:
            if (
                self.stop_event.is_set()
                or self.input_accept is None
                or self.input_opening
                or (self.input_channel is not None and not self.input_channel.finished.is_set())
            ):
                raise ConnectionError("Entrada por red no disponible")
            self.input_opening = True
        raw = connection = None
        channel = None
        try:
            deadline = time.monotonic() + timeout
            peer = private_ip(self.peer, self.pairing.testing)
            raw = socket.create_connection((peer, self.port), timeout=timeout, source_address=(self.address, 0))
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remaining_timeout(raw, deadline)
            connection = self.client_context.wrap_socket(raw)
            self.pairing.verify_pin(connection)
            nonce = secrets.token_hex(16)
            remaining_timeout(connection, deadline)
            send(connection, {"link": self.config["link"], "id": nonce, "op": "input", "args": args})
            reply = receive(connection, deadline)
            if set(reply) != {"id", "result"} or reply["id"] != nonce or not isinstance(reply["result"], dict):
                raise ValueError("Respuesta inválida")
            with self.input_lock:
                if self.stop_event.is_set() or self.input_accept is None:
                    raise ConnectionError("Entrada por red cancelada")
                channel = factory(connection, reply["result"])
                channel.start()
                self.input_channel = channel
            return channel
        except Exception:
            if channel is not None:
                channel.close()
            elif connection is not None:
                connection.close()
            raise
        finally:
            if raw is not None:
                raw.close()
            with self.input_lock:
                self.input_opening = False

    def close(self):
        self.stop_event.set()
        with self.input_lock:
            channel = self.input_channel
            self.input_accept = None
        input_error = None
        if channel is not None:
            try:
                channel.close()
            except Exception as error:
                input_error = error
        if self.wake_writer:
            try:
                self.wake_writer.send(b"x")
            except OSError:
                pass
        if self.listener:
            self.listener.close()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=4)
        for sock in (self.wake_reader, self.wake_writer):
            if sock:
                sock.close()
        if input_error is not None:
            raise RuntimeError("El canal de entrada no ha terminado") from None


class Discovery:
    """Only paired peers can change the destination address; TLS still pins identity."""

    def __init__(self, link):
        self.link = link
        self.secret = base64.b64decode(link.config["secret"])
        self.seen = collections.deque(maxlen=256)
        self.worker = None
        self.socket = None
        self.wake_reader = self.wake_writer = None
        self.stop_event = threading.Event()
        self.session = secrets.token_hex(16)
        self.epoch = time.time_ns()
        self.sequence = 0
        self.packet_lock = threading.Lock()
        self.peer_session = None
        self.peer_epoch = 0
        self.peer_sequence = 0
        self.peer_observed_at = -float("inf")

    def packet(self, kind="available"):
        if kind not in ("available", "reply", "departing"):
            raise ValueError("Aviso de disponibilidad inválido")
        with self.packet_lock:
            self.sequence += 1
            sequence = self.sequence
        packet = {
            "link": self.link.config["link"],
            "role": self.link.config["role"],
            "time": int(time.time()),
            "nonce": secrets.token_hex(16),
            "address": self.link.address,
            "session": self.session,
            "epoch": self.epoch,
            "sequence": sequence,
            "kind": kind,
        }
        packet["proof"] = hmac.new(self.secret, canonical(packet), hashlib.sha256).hexdigest()
        return canonical(packet)

    def accept(self, raw, sender):
        if len(raw) > 512:
            return False
        try:
            packet = json.loads(raw)
            proof = packet.pop("proof")
            expected = hmac.new(self.secret, canonical(packet), hashlib.sha256).hexdigest()
            if not isinstance(proof, str) or not hmac.compare_digest(proof, expected):
                return False
            legacy = {"link", "role", "time", "nonce", "address"}
            extended = legacy | {"session", "epoch", "sequence", "kind"}
            if set(packet) not in (legacy, extended):
                return False
            role = "mac" if self.link.config["role"] == "windows" else "windows"
            if packet["link"] != self.link.config["link"] or packet["role"] != role:
                return False
            address = private_ip(packet["address"], self.link.pairing.testing)
            if address != sender or ipaddress.ip_address(address) not in ipaddress.ip_network(
                self.link.config["network"]
            ):
                return False
            if type(packet["time"]) is not int or abs(packet["time"] - time.time()) > 30:
                return False
            if not isinstance(packet["nonce"], str) or len(packet["nonce"]) != 32 or packet["nonce"] in self.seen:
                return False
            kind = packet.get("kind", "available")
            if set(packet) == extended:
                session, epoch, sequence = packet["session"], packet["epoch"], packet["sequence"]
                recent = time.monotonic() - self.peer_observed_at <= 30
                if (
                    kind not in ("available", "reply", "departing")
                    or not isinstance(session, str)
                    or len(session) != 32
                    or type(sequence) is not int
                    or not 0 < sequence < 2**63
                    or type(epoch) is not int
                    or not 0 < epoch < 2**63
                    or (recent and epoch < self.peer_epoch)
                    or (recent and epoch == self.peer_epoch and session != self.peer_session)
                    or (session == self.peer_session and (epoch != self.peer_epoch or sequence <= self.peer_sequence))
                ):
                    return False
                # Order process incarnations too: a delayed departure from a
                # previous boot must not retire the newly available peer.
                # After the packet lifetime, a fresh restart may have an older
                # wall clock (NTP correction); do not reject it indefinitely.
                self.peer_session, self.peer_epoch, self.peer_sequence = session, epoch, sequence
                self.peer_observed_at = time.monotonic()
            elif self.peer_session:
                # A delayed legacy packet cannot supersede an ordered notice.
                return False
            self.seen.append(packet["nonce"])
            self.link.peer = address
            if kind == "departing":
                self.link.on_departure()
            else:
                self.link.on_announcement()
            return kind
        except (ValueError, KeyError, TypeError, AttributeError):
            return False

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket = sock  # close() also owns a socket whose bind/start failed.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Broadcast reception requires wildcard UDP binding. Packets cannot issue
        # commands and must pass HMAC + subnet checks; control uses private-IP TLS.
        sock.bind(("0.0.0.0", PORT + 1))
        self.wake_reader, self.wake_writer = socket.socketpair()
        self.worker = threading.Thread(target=self._run, name="flow-discovery", daemon=True)
        self.worker.start()

    def _run(self):
        # No periodic broadcasts or timeout polling. Offline standby is passive.
        while not self.link.stop_event.is_set() and not self.stop_event.is_set():
            try:
                readable, _, _ = select.select([self.socket, self.wake_reader], [], [])
                if self.wake_reader in readable:
                    return
                raw, sender = self.socket.recvfrom(513)
                kind = self.accept(raw, sender[0])
            except OSError:
                return
            if kind == "available":
                # Reply once so a returning peer also learns our DHCP address.
                # Replies never cause replies, and send failure keeps us listening.
                try:
                    self.announce("reply")
                except OSError:
                    log.debug("Availability reply could not be sent")

    def announce(self, kind="available"):
        network = ipaddress.ip_network(self.link.config["network"])
        targets = [self.link.peer]
        if kind == "available":
            targets.insert(0, str(network.broadcast_address))
        packet = self.packet(kind)
        # Bind the selected private interface even with a VPN/default-route change.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sender.settimeout(0.2)
            sender.bind((self.link.address, 0))
            sent, error = False, None
            for target in dict.fromkeys(targets):
                try:
                    sender.sendto(packet, (target, PORT + 1))
                    sent = True
                except OSError as exc:
                    error = exc
            if not sent and error:
                raise error

    def close(self):
        self.stop_event.set()
        if self.wake_writer:
            try:
                self.wake_writer.send(b"x")
            except OSError:
                pass
        if self.socket:
            self.socket.close()
        if self.worker:
            self.worker.join(timeout=2)
        for sock in (self.wake_reader, self.wake_writer):
            if sock:
                sock.close()


def refresh_address(pairing):
    config = pairing.load()
    addresses = local_addresses()
    if any(ip == config["address"] for _, ip, _ in addresses):
        return config
    matching = [
        (ip, net) for _, ip, net in addresses if ipaddress.ip_address(ip) in ipaddress.ip_network(config["network"])
    ]
    if len(matching) != 1:
        raise ValueError("Selecciona de nuevo la red del enlace")
    config["address"], config["network"] = matching[0]
    pairing.save(config)
    return config
