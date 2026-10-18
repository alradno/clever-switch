"""Pinned TLS and UDP loopback for bootstrap, legacy mode and reconnect."""

import copy
import hashlib
import socket
import ssl
import struct
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.flow.identity import Pairing
from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import Kind, Packet
from cleverswitch.flow.input_service import Service
from cleverswitch.flow.input_stream import Channel
from cleverswitch.flow.network import Link, receive
from experiments.motion_lane import Motion
from experiments.motion_link import CAPABILITY, DatagramMux, Negotiation, bootstrap, supports_motion

DESKTOP = Desktop([Display(0, 0, 1920, 1080)])


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "Lab condition did not complete"
        time.sleep(0.002)


class LabChannel(Channel):
    def __init__(self, connection, endpoint, selected):
        self.endpoint, self.selected = endpoint, selected
        self.negotiation = None
        super().__init__(connection, endpoint.packets.append, self.retire)

    def start(self):
        try:
            if self.selected:
                self.negotiation = bootstrap(self.connection, self.endpoint.link)
            super().start()
        except Exception:
            self.retire()
            raise

    def retire(self):
        if self.negotiation is not None:
            self.negotiation.close()


class Endpoint:
    """Lab upgrade adapter; the native Service remains unchanged."""

    def __init__(self, pairing, capable=True):
        self.capable = capable
        self.packets = []
        self.channel = None
        self.offer_received = None
        self.link = Link(pairing, self.status, 0)
        self.link.input_accept = self.accept

    def status(self, op, args):
        return {"capabilities": {CAPABILITY: 1}} if self.capable else {}

    def accept(self, offer):
        self.offer_received = copy.deepcopy(offer)
        expected = {"version", "displays"} | ({"motion"} if self.capable and "motion" in offer else set())
        if set(offer) != expected or offer.get("motion", 1) != 1:
            raise ValueError("Invalid lab upgrade")
        selected = self.capable and "motion" in offer
        response = {"version": 1, "displays": DESKTOP.wire()}
        if selected:
            response["motion"] = 1
        return response, lambda connection: self.attach(connection, selected)

    def attach(self, connection, selected):
        self.channel = LabChannel(connection, self, selected)
        return self.channel

    def connect(self):
        capabilities = self.link.call("status").get("capabilities")
        selected = self.capable and supports_motion(capabilities)
        offer = {"version": 1, "displays": DESKTOP.wire()}
        if selected:
            offer["motion"] = 1

        def factory(connection, response):
            assert (response.get("motion") == 1) == selected
            return self.attach(connection, selected)

        self.link.open_input(offer, factory)


@pytest.fixture
def real_peers(tmp_path):
    # The application pairing flow creates disposable laboratory identities only.
    first, second = Pairing(tmp_path / "windows", True), Pairing(tmp_path / "mac", True)
    invitation, response = tmp_path / "offer.json", tmp_path / "answer.json"
    first.create_invitation(invitation, "127.0.0.1", "127.0.0.0/8")
    second.accept_invitation(invitation, response, "127.0.0.2", "127.0.0.0/8")
    first.accept_response(response)
    for pairing in (first, second):
        config = pairing.load()
        config["address"] = config["peer"]["address"] = "127.0.0.1"
        pairing.save(config)
    peers = [Endpoint(p) for p in (first, second)]
    for peer in peers:
        peer.link.start()
    peers[0].link.port, peers[1].link.port = peers[1].link.port, peers[0].link.port

    def file_hashes():
        return {
            p.relative_to(tmp_path): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.rglob("*") if p.is_file()
        }

    before = file_hashes()
    try:
        yield peers
    finally:
        for peer in peers:
            peer.link.close()
        assert file_hashes() == before  # No rewrite of pairing or session keys on disk.


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
def test_real_tls_bootstrap_and_shared_udp_with_motion_and_discovery(real_peers, source):
    real_peers[0].connect()
    wait_for(lambda: all(p.channel is not None and p.channel.worker is not None for p in real_peers))
    sender, receiver = real_peers[source], real_peers[1 - source]
    source_codec, target_codec = (p.channel.negotiation.codec for p in (sender, receiver))
    assert source_codec.session_id == target_codec.session_id
    assert all(p.channel.connection.version() == "TLSv1.3" for p in real_peers)
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as outgoing,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as incoming,
    ):
        for sock in (outgoing, incoming):
            sock.bind(("127.0.0.1", 0))
            sock.settimeout(1)
        discovery = Mock(return_value="discovery")
        router = DatagramMux(discovery)
        inbox = router.register(target_codec, outgoing.getsockname(), "127.0.0.0/8", Mock(), testing=True)
        raw = source_codec.seal(Motion(1, 1, 0, (120, 240)))
        outgoing.sendto(raw, incoming.getsockname())
        assert router.receive(*incoming.recvfrom(513))
        assert target_codec.open(inbox.take()[0]).position == (120, 240)
        outgoing.sendto(b'{"synthetic":"discovery"}', incoming.getsockname())
        assert router.receive(*incoming.recvfrom(513)) == "discovery"
        discovery.assert_called_once()
        router.close()
    sender.channel.send(Packet(Kind.KEY, 1, (4, 1)))
    sender.channel.send(Packet(Kind.KEY, 1, (4, 0)))
    wait_for(lambda: len(receiver.packets) == 2)
    assert [(p.kind, p.values) for p in receiver.packets] == [(Kind.KEY, (4, 1)), (Kind.KEY, (4, 0))]
    sender.channel.close()
    wait_for(lambda: receiver.channel.finished.is_set())
    assert source_codec.tx is target_codec.tx is None


@pytest.mark.parametrize("legacy", [0, 1], ids=["old_windows", "old_mac"])
def test_legacy_peer_gets_unchanged_offer_and_reliable_input(real_peers, legacy):
    real_peers[legacy].capable = False
    real_peers[0].connect()
    wait_for(lambda: real_peers[1].channel is not None)
    assert real_peers[1].offer_received == {"version": 1, "displays": DESKTOP.wire()}
    assert all(peer.channel.negotiation is None for peer in real_peers)
    real_peers[0].channel.send(Packet(Kind.MOVE, 1, (100, 200)))
    wait_for(lambda: bool(real_peers[1].packets))
    assert real_peers[1].packets[-1].values == (100, 200)


@pytest.mark.parametrize("legacy", [0, 1], ids=["original_windows_service", "original_mac_service"])
def test_actual_unmodified_service_receives_and_releases_reliable_input(real_peers, legacy):
    old, modern = real_peers[legacy], real_peers[1 - legacy]
    old.capable = False
    backend = SimpleNamespace(
        desktop=DESKTOP, ready=lambda: True, start=lambda _: None, close=lambda: None,
        capture=lambda _: None, inject=Mock(), display_events=True,
    )
    service = Service(old.link, backend, enabled=True)
    try:
        service.connect() if legacy == 0 else modern.connect()
        wait_for(lambda: service.channel is not None and modern.channel is not None)
        assert modern.channel.negotiation is None
        modern.channel.send(Packet(Kind.ENTER, 1, (100, 200)))
        modern.channel.send(Packet(Kind.KEY, 1, (4, 1)))
        modern.channel.send(Packet(Kind.KEY, 1, (4, 0)))
        modern.channel.send(Packet(Kind.LEAVE, 1))
        wait_for(lambda: any(p.kind == Kind.LEFT for p in modern.packets))
        assert [(c.args[0].kind, c.args[0].values) for c in backend.inject.call_args_list] == [
            (Kind.MOVE, (100, 200)), (Kind.KEY, (4, 1)), (Kind.KEY, (4, 0)),
        ]
        assert service.session.phase == "local" and not service.session.keys
        assert not service.channel.failure and not modern.channel.failure
    finally:
        service.close()


def test_reconnect_uses_new_keys_and_late_old_close_does_not_retire_them(real_peers):
    real_peers[0].connect()
    wait_for(lambda: all(p.channel and p.channel.worker for p in real_peers))
    old_channels = [p.channel for p in real_peers]
    old_codecs = [c.negotiation.codec for c in old_channels]
    ciphertext = old_codecs[0].seal(Motion(1, 1, 0, (10, 20)))
    old_channels[0].close()
    wait_for(lambda: old_channels[1].finished.is_set())
    real_peers[0].connect()
    wait_for(lambda: real_peers[1].channel is not old_channels[1] and real_peers[1].channel.worker is not None)
    for channel in old_channels:
        channel.close()
    current = [p.channel.negotiation.codec for p in real_peers]
    assert current[1].open(ciphertext) is None
    assert current[1].open(current[0].seal(Motion(1, 1, 0, (30, 40)))).position == (30, 40)
    assert all(c.tx is None for c in old_codecs)


@pytest.mark.parametrize("side", [0, 1])
def test_rejected_pin_prevents_upgrade_and_ephemeral_keys(real_peers, side, monkeypatch):
    reject = Mock(side_effect=ssl.SSLError("Synthetic pin mismatch"))
    monkeypatch.setattr(real_peers[side].link.pairing, "verify_pin", reject)
    with pytest.raises((ConnectionError, OSError)):
        real_peers[0].connect()
    assert all(p.channel is None for p in real_peers)
    assert real_peers[1].offer_received is None
    assert reject.call_count >= 1


@pytest.mark.parametrize("seconds", [0, -1, True, float("nan"), 3])
def test_bootstrap_rejects_invalid_deadline(seconds):
    with pytest.raises(ValueError):
        bootstrap(Mock(), Mock(), timeout=seconds)


def test_bootstrap_rechecks_exact_peer_before_keys():
    connection = Mock()
    connection.getpeername.return_value = ("192.168.1.35", 47654)
    link = SimpleNamespace(pairing=Mock(testing=False), peer="192.168.1.34", config={"network": "192.168.1.0/24"})
    with pytest.raises(ValueError, match="Unexpected motion peer"):
        bootstrap(connection, link)
    link.pairing.verify_pin.assert_called_once_with(connection)
    connection.sendall.assert_not_called()


@pytest.mark.parametrize("role", ["windows", "mac"])
def test_bootstrap_read_failure_retires_state(role, monkeypatch):
    connection = Mock()
    connection.getpeername.return_value = ("192.168.1.34", 47654)
    link = SimpleNamespace(pairing=Mock(testing=False), peer="192.168.1.34", config={
        "network": "192.168.1.0/24", "link": "lab-link", "role": role,
    })
    retired = []
    original = Negotiation.close

    def close(item):
        original(item)
        retired.append(item)

    monkeypatch.setattr(Negotiation, "close", close)
    monkeypatch.setattr("experiments.motion_link.receive", Mock(side_effect=TimeoutError))
    with pytest.raises(TimeoutError):
        bootstrap(connection, link)
    assert len(retired) == 1 and retired[0].state == "closed" and retired[0].codec is None


def test_failed_reply_write_retires_server_key(monkeypatch):
    connection = Mock()
    connection.getpeername.return_value = ("192.168.1.34", 47654)
    link = SimpleNamespace(pairing=Mock(testing=False), peer="192.168.1.34", config={
        "network": "192.168.1.0/24", "link": "lab-link", "role": "mac",
    })
    codecs = []
    original = Negotiation._codec

    def track(item, secret, salt):
        original(item, secret, salt)
        codecs.append(item.codec)

    monkeypatch.setattr(Negotiation, "_codec", track)
    monkeypatch.setattr("experiments.motion_link.receive", lambda *_: {"version": 1, "challenge": "a" * 32})
    connection.sendall.side_effect = OSError
    with pytest.raises(OSError):
        bootstrap(connection, link)
    assert len(codecs) == 1 and codecs[0].tx is codecs[0].rx is None


def test_partial_reply_has_one_total_deadline():
    # Exact framed receive path; pinned TLS is exercised by the tests above.
    reader, writer = socket.socketpair()
    try:
        deadline = time.monotonic() + 0.04
        writer.sendall(struct.pack("!I", 20) + b"{")
        with pytest.raises(TimeoutError):
            receive(reader, deadline)
        assert time.monotonic() < deadline + 0.3
    finally:
        reader.close()
        writer.close()
