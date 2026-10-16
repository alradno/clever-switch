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
OPS = frozenset(("ready", "prepare", "mouse", "keyboard", "commit", "status", "abort", "return", "follow"))


def receive_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise ConnectionError("Enlace interrumpido")
        data.extend(part)
    return bytes(data)


def receive(connection):
    size = struct.unpack("!I", receive_exact(connection, 4))[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("Mensaje inválido")
    value = json.loads(receive_exact(connection, size))
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
        self.on_failure = lambda: None
        self.wake_reader = self.wake_writer = None

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
        # One worker bounds resource use. Each connection handles exactly one request.
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
                try:
                    private_ip(remote[0], self.pairing.testing)
                    if remote[0] != self.peer:
                        continue
                    raw.settimeout(2.5)
                    with self.server_context.wrap_socket(raw, server_side=True) as connection:
                        self.pairing.verify_pin(connection)
                        self.on_authenticated()
                        request = receive(connection)
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
                        result = self.handler(request["op"], request["args"])
                        send(connection, {"id": nonce, "result": result})
                except Exception:
                    # No peer payload, certificates or pairing secrets in logs.
                    log.debug("Rejected or interrupted local peer request")

    def call(self, op, args=None, timeout=3.0):
        try:
            return self._call(op, args, timeout)
        except Exception:
            self.on_failure()
            raise

    def _call(self, op, args=None, timeout=3.0):
        if op not in OPS:
            raise ValueError("Operación inválida")
        nonce = secrets.token_hex(16)
        peer = private_ip(self.peer, self.pairing.testing)
        with socket.create_connection((peer, self.port), timeout=timeout, source_address=(self.address, 0)) as raw:
            with self.client_context.wrap_socket(raw) as connection:
                self.pairing.verify_pin(connection)
                send(connection, {"link": self.config["link"], "id": nonce, "op": op, "args": args or {}})
                reply = receive(connection)
                if set(reply) != {"id", "result"} or reply["id"] != nonce or not isinstance(reply["result"], dict):
                    raise ValueError("Respuesta inválida")
                self.on_authenticated()
                return reply["result"]

    def close(self):
        self.stop_event.set()
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

    def packet(self):
        packet = {
            "link": self.link.config["link"],
            "role": self.link.config["role"],
            "time": int(time.time()),
            "nonce": secrets.token_hex(16),
            "address": self.link.address,
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
            if set(packet) != {"link", "role", "time", "nonce", "address"}:
                return False
            role = "mac" if self.link.config["role"] == "windows" else "windows"
            if packet["link"] != self.link.config["link"] or packet["role"] != role:
                return False
            address = private_ip(packet["address"], self.link.pairing.testing)
            if address != sender or ipaddress.ip_address(address) not in ipaddress.ip_network(
                self.link.config["network"]
            ):
                return False
            if not isinstance(packet["time"], int) or abs(packet["time"] - time.time()) > 30:
                return False
            if not isinstance(packet["nonce"], str) or len(packet["nonce"]) != 32 or packet["nonce"] in self.seen:
                return False
            self.seen.append(packet["nonce"])
            self.link.peer = address
            self.link.on_announcement()
            return True
        except (ValueError, KeyError, TypeError, AttributeError):
            return False

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Broadcast reception requires wildcard UDP binding. Packets cannot issue
        # commands and must pass HMAC + subnet checks; control uses private-IP TLS.
        sock.bind(("0.0.0.0", PORT + 1))
        self.socket = sock
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
                self.accept(raw, sender[0])
            except OSError:
                return

    def announce(self):
        network = ipaddress.ip_network(self.link.config["network"])
        # Bind the selected private interface even with a VPN/default-route change.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sender.bind((self.link.address, 0))
            sender.sendto(self.packet(), (str(network.broadcast_address), PORT + 1))
            sender.sendto(self.packet(), (self.link.peer, PORT + 1))

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
