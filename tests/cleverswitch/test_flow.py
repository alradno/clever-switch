import copy
import json
import socket
import ssl
import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.flow.coordinator import Coordinator, Destination
from cleverswitch.flow.cursor import Edge, Sample
from cleverswitch.flow.hardware import Hardware, message
from cleverswitch.flow.identity import Pairing, canonical, private_ip, private_network, read_file, write_private
from cleverswitch.flow.network import Discovery, Link, receive, receive_exact, send


@pytest.fixture
def paired(tmp_path):
    a, b = Pairing(tmp_path / "a", True), Pairing(tmp_path / "b", True)
    invitation, response = tmp_path / "invitation.json", tmp_path / "response.json"
    a.create_invitation(invitation, "127.0.0.1", "127.0.0.0/8")
    b.accept_invitation(invitation, response, "127.0.0.2", "127.0.0.0/8")
    a.accept_response(response)
    return a, b


@pytest.fixture
def paired_tls(paired):
    # macOS does not bind arbitrary 127/8 addresses without configuring aliases.
    # Distinct certificates still authenticate two peers on independent TCP ports.
    for pairing in paired:
        config = pairing.load()
        config["address"] = config["peer"]["address"] = "127.0.0.1"
        pairing.save(config)
    return paired


def start_tls_peers(la, lb):
    la.start()
    lb.start()
    # Link.port is the peer's RPC port after each listener has bound its own port.
    la.port, lb.port = lb.port, la.port


class Devices:
    def __init__(self, host, owners, events):
        self.host, self.owners, self.events = host, owners, events
        self.enabled = True
        self.recognized = True
        self.missing = set()
        self.switch_failure = None

    def allowed(self):
        return self.enabled

    def known(self):
        return self.recognized

    def probe(self, role, timeout=0.45):
        self.events.append((self.host, "probe", role))
        return role not in self.missing and self.owners[role] == self.host

    def switch(self, role, target):
        self.events.append((self.host, "switch", role, target))
        if role == self.switch_failure:
            return  # Simulate a successful write whose radio switch fails.
        self.owners[role] = target


class Cursor:
    def __init__(self):
        self.ready = True
        self.placements = []
        self.failure = False
        self.safe = True

    def session_ready(self):
        return self.ready

    def sample(self):
        return Sample(100, 200, 0, 0, 1920, 1080, self.ready and self.safe)

    def enter(self, side, y):
        if self.failure:
            raise OSError("locked")
        self.placements.append((side, y))


def machines(tmp_path):
    owners = {"keyboard": 0, "mouse": 0}
    events = []
    a, b = Devices(0, owners, events), Devices(1, owners, events)
    c, d = Cursor(), Cursor()
    da, db = Destination(a, c, 0, tmp_path / "a-journal.json"), Destination(b, d, 1, tmp_path / "b-journal.json")
    return a, b, c, d, da, db, owners, events


class LocalLink:
    def __init__(self, destination):
        self.destination = destination
        self.calls = []
        self.drop = None
        self.lost_commit = False

    def call(self, op, args=None, timeout=3):
        self.calls.append(op)
        if self.drop == op:
            raise OSError("network unavailable")
        result = self.destination.handle(op, args or {})
        if op == "commit" and self.lost_commit:
            raise OSError("ACK lost after commit")
        return result


def run(coordinator, kind="edge", y=0.5):
    coordinator.busy.acquire()
    coordinator._run(kind, y)


@pytest.mark.parametrize("host", [0, 1], ids=["windows", "mac"])
@pytest.mark.parametrize("change", ["pause", "close", "lock", "drag"])
def test_preparation_cannot_move_devices_after_local_conditions_change(tmp_path, host, change):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    owners.update(mouse=host, keyboard=host)
    source, target = (da, db) if host == 0 else (db, da)
    link = LocalLink(target)
    coordinator = Coordinator(source.hardware, source.cursor, link, source)
    original = link.call

    def delayed_reply(op, args=None, timeout=3):
        result = original(op, args, timeout)
        if op == "prepare":
            if change == "pause":
                source.hardware.enabled = False
            elif change == "close":
                coordinator.stop_event.set()
            elif change == "lock":
                source.cursor.ready = False
            else:
                source.cursor.safe = False
        return result

    link.call = delayed_reply
    run(coordinator)
    assert not any(event[1] == "switch" for event in events)
    assert owners == {"mouse": host, "keyboard": host}
    assert target.state["phase"] == "aborted"
    assert not source.gate.locked() and not target.gate.locked()


def test_rpc_deadline_includes_fragmented_tls_response(paired_tls, mocker):
    # Every fragment arrives within the socket timeout, but the whole response
    # exceeds the RPC budget. Per-read timeouts alone cannot enforce that budget.
    client = Link(paired_tls[0], lambda op, args: {})
    server = Link(paired_tls[1], lambda op, args: {"ready": True}, 0)
    original_send = send

    def fragmented_reply(connection, value):
        if "result" not in value:
            return original_send(connection, value)
        raw = canonical(value)
        packet = struct.pack("!I", len(raw)) + raw
        for byte in packet[:10]:
            time.sleep(0.04)
            connection.sendall(bytes([byte]))
        connection.sendall(packet[10:])

    mocker.patch("cleverswitch.flow.network.send", side_effect=fragmented_reply)
    server.start()
    client.port = server.port
    try:
        with pytest.raises(TimeoutError):
            client.call("ready", timeout=0.2)
    finally:
        client.close()
        server.close()


def test_pairing_keys_stay_local_and_tls_is_mutual(paired, tmp_path):
    a, b = paired
    assert a.load()["peer"]["cert"] == b.load()["cert"]
    assert b.load()["peer"]["cert"] == a.load()["cert"]
    for exported in ("invitation.json", "response.json"):
        assert "PRIVATE KEY" not in (tmp_path / exported).read_text()
    assert "secret" not in read_file(tmp_path / "response.json")
    assert a.tls().verify_mode == ssl.CERT_REQUIRED
    assert a.tls(True).minimum_version == ssl.TLSVersion.TLSv1_3
    with pytest.raises(ValueError):
        a.create_invitation(tmp_path / "duplicate.json", "127.0.0.1", "127.0.0.0/8")
    with pytest.raises(ValueError):
        b.accept_invitation(tmp_path / "invitation.json", tmp_path / "duplicate.json", "127.0.0.2", "127.0.0.0/8")
    with pytest.raises(ValueError):
        b.accept_response(tmp_path / "response.json")


def test_pairing_rejects_tampered_response_and_invalid_files(tmp_path):
    a, b = Pairing(tmp_path / "a", True), Pairing(tmp_path / "b", True)
    assert a.load() is None
    with pytest.raises(ValueError):
        a.tls()
    a.create_invitation(tmp_path / "i.json", "127.0.0.1", "127.0.0.0/8")
    a.export_invitation(tmp_path / "copy.json")
    assert read_file(tmp_path / "copy.json") == read_file(tmp_path / "i.json")
    b.accept_invitation(tmp_path / "i.json", tmp_path / "r.json", "127.0.0.2", "127.0.0.0/8")
    response = read_file(tmp_path / "r.json")
    response["address"] = "127.0.0.3"
    write_private(tmp_path / "r.json", canonical(response))
    with pytest.raises(ValueError, match="no pertenece"):
        a.accept_response(tmp_path / "r.json")
    assert "peer" not in a.load()
    write_private(tmp_path / "too-big.json", b"0" * 16385)
    with pytest.raises(ValueError):
        read_file(tmp_path / "too-big.json")
    write_private(tmp_path / "list.json", b"[]")
    with pytest.raises(ValueError):
        read_file(tmp_path / "list.json")
    write_private(tmp_path / "bad.json", b'{"kind":"response"}')
    with pytest.raises(ValueError):
        Pairing(tmp_path / "fresh").accept_invitation(
            tmp_path / "bad.json", tmp_path / "r.json", "192.168.1.2", "192.168.1.0/24"
        )


@pytest.mark.parametrize("address", ["8.8.8.8", "127.0.0.1", "169.254.1.1", "224.0.0.1", "::1", "example.com"])
def test_only_private_ipv4(address):
    with pytest.raises(ValueError):
        private_ip(address)


def test_private_network_boundaries():
    assert private_ip("192.168.1.2") == "192.168.1.2"
    assert private_network("192.168.1.0/24", "192.168.1.2") == "192.168.1.0/24"
    for network in ("0.0.0.0/0", "10.0.0.0/24", "192.168.1.2/32"):
        with pytest.raises(ValueError):
            private_network(network, "192.168.1.2")


def test_tls_real_two_peer_switch_and_reverse(paired_tls, tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    la, lb = Link(paired_tls[0], da.handle, 0), Link(paired_tls[1], db.handle, 0)
    start_tls_peers(la, lb)
    try:
        ca, cb = Coordinator(a, c, la, da), Coordinator(b, d, lb, db)
        run(ca, y=0.7)
        assert owners == {"mouse": 1, "keyboard": 1}
        assert db.state["phase"] == "committed"
        assert d.placements == [("left", 0.7)]
        mouse_proof = events.index((1, "probe", "mouse"))
        keyboard_move = events.index((0, "switch", "keyboard", 1))
        assert mouse_proof < keyboard_move
        assert la.call("status", {"tx": db.state["tx"]}) == {"phase": "committed"}
        run(cb, y=0.2)
        assert owners == {"mouse": 0, "keyboard": 0}
        assert c.placements == [("right", 0.2)]
    finally:
        la.close()
        lb.close()


def test_tls_rejects_unknown_client_and_replay(paired_tls, tmp_path):
    server = Link(paired_tls[0], lambda op, args: {"ready": True}, 0)
    client = Link(paired_tls[1], lambda op, args: {}, 0)
    server.start()
    client.port = server.port
    try:
        unknown = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        unknown.check_hostname = False
        unknown.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", server.port), source_address=(client.address, 0)) as raw:
            with unknown.wrap_socket(raw) as secure:
                send(secure, {"link": server.config["link"], "id": "a" * 32, "op": "ready", "args": {}})
                with pytest.raises((OSError, ConnectionError)):
                    receive(secure)
        assert client.call("ready") == {"ready": True}
        request = {"link": server.config["link"], "id": "a" * 32, "op": "ready", "args": {}}
        for index in range(2):
            with socket.create_connection(("127.0.0.1", server.port), source_address=(client.address, 0)) as raw:
                with client.client_context.wrap_socket(raw) as secure:
                    send(secure, request)
                    if index == 0:
                        assert receive(secure)["result"] == {"ready": True}
                    else:
                        with pytest.raises(ConnectionError):
                            receive(secure)
        for change in ({"op": "shell"}, {"link": "wrong"}, {"args": []}, {"extra": True}, {"id": 1}):
            invalid = {**request, "id": "b" * 32, **change}
            with socket.create_connection(("127.0.0.1", server.port), source_address=(client.address, 0)) as raw:
                with client.client_context.wrap_socket(raw) as secure:
                    send(secure, invalid)
                    with pytest.raises(ConnectionError):
                        receive(secure)
        wrong = Pairing(tmp_path / "unpaired", True)
        wrong.identity(server.config["link"], "mac")
        config = client.pairing.load()
        config["cert"] = (wrong.directory / "identity.pem").read_text()
        wrong.save(config)
        attacker = Link(wrong, lambda op, args: {}, server.port)
        with pytest.raises((OSError, ConnectionError)):
            attacker.call("ready")
        with pytest.raises(ValueError):
            client.call("shell")
    finally:
        server.close()


def test_framing_limits_and_disconnect():
    a, b = socket.socketpair()
    try:
        send(a, {"ready": True})
        assert receive(b) == {"ready": True}
        a.sendall(struct.pack("!I", 2049))
        with pytest.raises(ValueError):
            receive(b)
        with pytest.raises(ValueError):
            send(a, {"data": "x" * 2049})
        a.sendall(struct.pack("!I", 2) + b"[]")
        with pytest.raises(ValueError):
            receive(b)
        a.close()
        with pytest.raises(ConnectionError):
            receive_exact(b, 1)
    finally:
        a.close()
        b.close()


def test_discovery_authenticated_dhcp_updates_and_replay(paired):
    la, lb = Link(paired[0], lambda *args: {}, 0), Link(paired[1], lambda *args: {}, 0)
    da, db = Discovery(la), Discovery(lb)
    packet = db.packet()
    assert da.accept(packet, "127.0.0.2")
    assert not da.accept(packet, "127.0.0.2")
    assert not da.accept(db.packet(), "127.0.0.3")
    assert not da.accept(da.packet(), "127.0.0.1")
    forged = json.loads(db.packet())
    forged["address"] = "8.8.8.8"
    assert not da.accept(canonical(forged), "8.8.8.8")
    assert not da.accept(b"[]", "127.0.0.2")
    assert not da.accept(b"x" * 513, "127.0.0.2")
    lb.address = "127.0.0.3"
    assert da.accept(db.packet(), "127.0.0.3")
    assert la.peer == "127.0.0.3"


def test_discovery_orders_departure_and_restarted_peer_notices(paired):
    from unittest.mock import Mock

    la, lb = Link(paired[0], lambda *args: {}, 0), Link(paired[1], lambda *args: {}, 0)
    la.on_announcement, la.on_departure = Mock(), Mock()
    da, db = Discovery(la), Discovery(lb)
    old_arrival = db.packet()
    departing = db.packet("departing")
    assert da.accept(departing, lb.address)
    la.on_departure.assert_called_once()
    assert not da.accept(old_arrival, lb.address)
    restarted = Discovery(lb)
    assert da.accept(restarted.packet(), lb.address)
    assert not da.accept(db.packet("departing"), lb.address)
    fresh_receiver = Discovery(la)
    assert fresh_receiver.accept(restarted.packet(), lb.address)
    assert not fresh_receiver.accept(db.packet("departing"), lb.address)
    assert la.on_announcement.call_count == 2
    la.on_departure.assert_called_once()


def test_discovery_transmits_same_notice_to_both_targets_and_replies_once(paired, mocker):
    la, lb = Link(paired[0], lambda *args: {}, 0), Link(paired[1], lambda *args: {}, 0)
    da, db = Discovery(la), Discovery(lb)
    sender = mocker.patch("cleverswitch.flow.network.socket.socket").return_value.__enter__.return_value
    db.announce()
    first, second = sender.sendto.call_args_list
    assert first.args[0] == second.args[0]  # Broadcast/unicast is one event, not two wakeups.
    da.socket = mocker.Mock()
    da.wake_reader = mocker.Mock()
    da.socket.recvfrom.side_effect = [(first.args[0], (lb.address, 1)), (second.args[0], (lb.address, 1))]
    mocker.patch(
        "cleverswitch.flow.network.select.select",
        side_effect=[([da.socket], [], [])] * 2 + [([da.wake_reader], [], [])],
    )
    reply = mocker.patch.object(da, "announce")
    da._run()
    reply.assert_called_once_with("reply")


def test_discovery_reply_does_not_echo_and_failed_reply_keeps_listener(paired, mocker):
    la, lb = Link(paired[0], lambda *args: {}, 0), Link(paired[1], lambda *args: {}, 0)
    da, db = Discovery(la), Discovery(lb)
    da.socket, da.wake_reader = mocker.Mock(), mocker.Mock()
    packets = [(db.packet(), (lb.address, 1)), (db.packet("reply"), (lb.address, 1))]
    da.socket.recvfrom.side_effect = packets
    mocker.patch(
        "cleverswitch.flow.network.select.select",
        side_effect=[([da.socket], [], [])] * 2 + [([da.wake_reader], [], [])],
    )
    reply = mocker.patch.object(da, "announce", side_effect=OSError("temporary network loss"))
    da._run()
    assert da.socket.recvfrom.call_count == 2
    reply.assert_called_once_with("reply")


def test_real_udp_notice_updates_both_addresses_then_mutual_tls_reconnects(paired_tls, mocker):
    from cleverswitch.flow import network

    la = Link(paired_tls[0], lambda *args: {"ready": True, "protocol": 2}, 0)
    lb = Link(paired_tls[1], lambda *args: {"ready": True, "protocol": 2}, 0)
    da, db = Discovery(la), Discovery(lb)
    departed = threading.Event()
    la.on_departure = departed.set
    try:
        start_tls_peers(la, lb)
        # macOS requires both peers on 127.0.0.1 with distinct ports. Exercise
        # the real receive loop and UDP reply without touching the user's LAN.
        da.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        da.socket.bind((la.address, 0))
        da.wake_reader, da.wake_writer = socket.socketpair()
        da.worker = threading.Thread(target=da._run, daemon=True)
        da.worker.start()
        la.peer, lb.peer = "127.0.0.3", "127.0.0.4"  # Stale last-known addresses.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.bind((lb.address, 0))
            sender.settimeout(2)
            mocker.patch.object(network, "PORT", sender.getsockname()[1] - 1)
            sender.sendto(db.packet(), da.socket.getsockname())
            reply, address = sender.recvfrom(513)
            assert db.accept(reply, address[0]) == "reply"
            assert la.peer == lb.address and lb.peer == la.address
            assert la.call("ready")["ready"] and lb.call("ready")["ready"]
            sender.sendto(db.packet("departing"), da.socket.getsockname())
            assert departed.wait(2)
    finally:
        da.close()
        db.close()
        la.close()
        lb.close()
    assert not da.worker.is_alive()


def test_failed_broadcast_still_notifies_last_known_peer(paired, mocker):
    discovery = Discovery(Link(paired[0], lambda *args: {}, 0))
    sender = mocker.patch("cleverswitch.flow.network.socket.socket").return_value.__enter__.return_value
    sender.sendto.side_effect = [OSError("broadcast unavailable"), 1]
    discovery.announce()
    assert sender.sendto.call_count == 2
    assert sender.sendto.call_args.args[1][0] == discovery.link.peer
    sender.settimeout.assert_called_once_with(0.2)


def test_availability_rejects_forgery_and_accepts_legacy_hint(paired):
    import hashlib
    import hmac

    da = Discovery(Link(paired[0], lambda *args: {}, 0))
    db = Discovery(Link(paired[1], lambda *args: {}, 0))
    packet = json.loads(db.packet())
    packet["kind"] = "departing"  # A modified announcement cannot park the peer.
    assert not da.accept(canonical(packet), db.link.address)
    for key in ("proof", "session", "epoch", "sequence", "kind"):
        packet.pop(key)
    packet["proof"] = hmac.new(db.secret, canonical(packet), hashlib.sha256).hexdigest()
    assert da.accept(canonical(packet), db.link.address)


def test_restart_after_clock_correction_is_not_rejected_indefinitely(paired, mocker):
    now = [0]
    mocker.patch("cleverswitch.flow.network.time.monotonic", side_effect=lambda: now[0])
    da = Discovery(Link(paired[0], lambda *args: {}, 0))
    db = Discovery(Link(paired[1], lambda *args: {}, 0))
    assert da.accept(db.packet(), db.link.address)
    restarted = Discovery(db.link)
    restarted.epoch = db.epoch - 1_000_000_000
    assert not da.accept(restarted.packet(), db.link.address)
    now[0] = 31  # Old packets have expired; a newly signed event can wake us.
    assert da.accept(restarted.packet(), db.link.address)


def test_idle_tls_listener_blocks_without_periodic_wakeups_and_closes(paired, mocker):
    from cleverswitch.flow import network

    entered = threading.Event()
    original = network.select.select

    def select(readers, writers, errors):
        entered.set()
        return original(readers, writers, errors)  # No timeout argument.

    wait = mocker.patch.object(network.select, "select", side_effect=select)
    server = Link(paired[0], lambda op, args: {"ready": True}, 0)
    server.start()
    assert entered.wait(1)
    threading.Event().wait(0.03)
    assert wait.call_count == 1
    server.close()
    assert not server.worker.is_alive()
    assert wait.call_count == 1


def test_cursor_dwell_drag_corners_and_bounce():
    clock = [0.0]
    edge = Edge("right", clock=lambda: clock[0])
    sample = Sample(1919, 600, 0, 0, 1920, 1080)
    assert edge.poll(sample) is None
    clock[0] = 0.39
    assert edge.poll(sample) is None
    clock[0] = 0.41
    assert edge.poll(sample) == pytest.approx(600 / 1080)
    clock[0] = 1
    assert edge.poll(sample) is None
    assert edge.poll(Sample(1900, 600, 0, 0, 1920, 1080)) is None
    assert edge.poll(Sample(1919, 10, 0, 0, 1920, 1080)) is None
    assert edge.poll(Sample(1919, 600, 0, 0, 1920, 1080, False)) is None
    edge.cooldown()
    assert edge.poll(sample) is None
    assert edge.poll(Sample(1921, 600, 0, 0, 1920, 1080)) is None
    left = Edge("left", clock=lambda: clock[0])
    assert left.poll(Sample(0, 100, 0, 0, 1000, 500)) is None
    clock[0] = 2
    assert left.poll(Sample(0, 100, 0, 0, 1000, 500)) == 0.2
    with pytest.raises(ValueError):
        Edge("top")


def test_destination_lease_expiry_returns_mouse_without_moving_source_keyboard(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    now = [0]
    db.clock = lambda: now[0]
    assert db.handle("prepare", {"tx": "1" * 32, "kind": "edge", "protocol": 2, "y": 0.5}) == {"accepted": True}
    a.switch("mouse", 1)
    assert db.handle("mouse", {"tx": "1" * 32}) == {"present": True}
    assert owners["keyboard"] == 0
    now[0] = 31
    db.expire()
    assert owners == {"mouse": 0, "keyboard": 0}
    assert db.state["phase"] == "aborted"


def test_restart_recovers_half_switch_and_preserves_committed_state(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    db.handle("prepare", {"tx": "1" * 32, "kind": "edge", "protocol": 2, "y": 0.5})
    owners.update(mouse=1, keyboard=1)
    restarted = Destination(b, d, 1, db.journal)
    b.recognized = False
    restarted.expire()
    assert restarted.recovering
    b.recognized = True
    restarted.deadline = 0
    restarted.expire()
    assert owners == {"mouse": 0, "keyboard": 0}
    assert not restarted.recovering
    # A fully committed switch survives a crash without an unsolicited return.
    run(Coordinator(a, c, LocalLink(restarted), da))
    recovered_commit = Destination(b, d, 1, db.journal)
    recovered_commit.expire()
    assert recovered_commit.state["phase"] == "committed"
    assert owners == {"mouse": 1, "keyboard": 1}


@pytest.mark.parametrize("failure", ["paused", "locked", "unknown", "network", "mouse-write", "cursor-warp"])
def test_handoff_failures_keep_or_return_inputs(tmp_path, failure):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    link = LocalLink(db)
    if failure == "paused":
        b.enabled = False
    elif failure == "locked":
        d.ready = False
    elif failure == "unknown":
        b.recognized = False
    elif failure == "network":
        link.drop = "prepare"
    elif failure == "mouse-write":
        a.switch_failure = "mouse"
    elif failure == "cursor-warp":
        d.failure = True
    ca = Coordinator(a, c, link, da)
    if failure == "mouse-write":
        ca._confirmed = lambda *args: False
    run(ca)
    assert owners == {"mouse": 0, "keyboard": 0}
    assert not ca.busy.locked()
    if failure != "cursor-warp":
        assert (0, "switch", "keyboard", 1) not in events


def test_lost_commit_ack_queries_status_without_rolling_back(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    link = LocalLink(db)
    link.lost_commit = True
    ca = Coordinator(a, c, link, da)
    run(ca)
    assert owners == {"mouse": 1, "keyboard": 1}
    assert link.calls[-1] == "status"
    assert "abort" not in link.calls


def test_keyboard_native_switch_already_departed_is_supported(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    owners["keyboard"] = 1
    ca = Coordinator(a, c, LocalLink(db), da)
    run(ca, "keyboard")
    assert owners == {"mouse": 1, "keyboard": 1}
    assert not d.placements
    assert (0, "switch", "keyboard", 1) not in events


def test_diverted_keyboard_remains_manual_when_peer_is_offline(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    link = LocalLink(db)
    link.drop = "prepare"
    run(Coordinator(a, c, link, da), "keyboard")
    assert owners == {"mouse": 0, "keyboard": 0}


def test_destination_state_machine_rejects_out_of_order_and_stale_tx(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    args = {"tx": "1" * 32, "kind": "edge", "protocol": 2, "y": 0.5}
    assert db.handle("ready", {})["ready"] is True
    for invalid in ({**args, "kind": "shell"}, {**args, "y": float("nan")}, {**args, "tx": "bad"}, {**args, "y": True}):
        with pytest.raises(ValueError):
            db.handle("prepare", invalid)
    db.handle("prepare", args)
    assert not db.handle("prepare", {**args, "tx": "2" * 32})["accepted"]
    assert db.handle("status", {"tx": "2" * 32}) == {"phase": "unknown"}
    assert not db.handle("keyboard", {"tx": args["tx"]})["present"]
    assert db.handle("commit", {"tx": args["tx"]}) == {"phase": "prepared"}
    assert not db.handle("mouse", {"tx": args["tx"]})["present"]
    b.enabled = False
    assert db.handle("mouse", {"tx": args["tx"]})["phase"] == "aborted"
    assert not db.handle("mouse", {"tx": args["tx"]})["present"]
    before = len(events)
    db.handle("abort", {"tx": args["tx"]})
    assert len(events) == before
    for op in ("ready", "return", "mouse"):
        with pytest.raises(ValueError):
            db.handle(op, {"extra": 1})


def test_manual_recovery_freshly_confirms_return(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    owners.update(mouse=1, keyboard=1)
    ca = Coordinator(a, c, LocalLink(db), da)
    assert ca.recover()
    ca.worker.join(3)
    assert owners == {"mouse": 0, "keyboard": 0}
    assert "confirmados aquí" in ca.status
    ca.close()
    assert not ca.request()


def test_manual_recovery_confirms_arrival_even_when_return_ack_is_lost(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    owners.update(mouse=1, keyboard=1)
    base = LocalLink(db)

    class LostReturnAck:
        def call(self, op, args=None, timeout=3):
            base.call(op, args, timeout)
            raise OSError("return processed but acknowledgement lost")

    ca = Coordinator(a, c, LostReturnAck(), da)
    assert ca.recover()
    ca.worker.join(2)
    assert owners == {"mouse": 0, "keyboard": 0}
    assert "confirmados aquí" in ca.status
    assert base.calls == ["return"]  # Never duplicate a radio command on an ambiguous ACK.


def test_manual_recovery_allows_bluetooth_arrival_after_five_seconds(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    owners.update(mouse=1, keyboard=1)
    now = [0.0]
    ca = Coordinator(a, c, LocalLink(db), da, clock=lambda: now[0])
    probe = a.probe
    a.probe = lambda role: now[0] >= 6 and probe(role)
    ca.stop_event = Mock()
    ca.stop_event.is_set.return_value = False
    ca.stop_event.wait.side_effect = lambda dt: now.__setitem__(0, now[0] + dt) or False
    assert ca.recover()
    ca.worker.join(2)
    assert "confirmados aquí" in ca.status
    assert 6 <= now[0] < 8


def test_manual_recovery_latches_both_edges_and_enforces_quiet_period(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    owners.update(mouse=1, keyboard=1)
    now = [0.0]
    ca = Coordinator(a, c, LocalLink(db), da, clock=lambda: now[0])
    ea, eb = Edge("right", clock=lambda: now[0]), Edge("left", clock=lambda: now[0])
    da.arrived, db.arrived = ea.cooldown, eb.cooldown
    assert ca.recover()
    ca.worker.join(2)
    assert not ca.can_start()
    now[0] = 10
    for edge, x in ((ea, 999), (eb, 0)):
        sample = Sample(x, 400, 0, 0, 1000, 800)
        assert edge.poll(sample) is None
        now[0] += 1
        assert edge.poll(sample) is None
        assert edge.latched


def test_closed_coordinator_refuses_manual_recovery(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    link = LocalLink(db)
    ca = Coordinator(a, c, link, da)
    ca.close()
    assert not ca.recover()
    assert not link.calls


@pytest.mark.parametrize("failed_operation", ["probe", "switch"])
def test_one_device_error_does_not_prevent_returning_the_other(tmp_path, failed_operation):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    owners.update(mouse=1, keyboard=1)
    original = getattr(b, failed_operation)

    def fail_keyboard(role, *args, **kwargs):
        if role == "keyboard":
            raise OSError("keyboard disconnected during cleanup")
        return original(role, *args, **kwargs)

    setattr(b, failed_operation, fail_keyboard)
    assert db.handle("return", {}) == {"returned": True}
    assert owners["mouse"] == 0
    assert not db.gate.locked()


def test_hardware_nonce_probe_cannot_be_satisfied_by_cache_or_wrong_reply():
    from cleverswitch.event.hidpp_response_event import HidppResponseEvent
    from cleverswitch.model.logi_device import LogiDevice
    from cleverswitch.policy import PairPolicy
    from cleverswitch.topic.topic import Topic
    from cleverswitch.topic.topics import Topics

    device = LogiDevice(1, 2, 255, "mouse", {0x1814: 4}, name="MX Master 3")
    registry = Mock()
    registry.all_entries.return_value = [device]
    context = SimpleNamespace(
        device_registry=registry, policy=PairPolicy(), topics=Topics(*(Topic() for _ in range(5)))
    )
    hardware = Hardware(context)
    writes = []
    context.topics.write.publish = writes.append
    result = []
    worker = threading.Thread(target=lambda: result.append(hardware.probe("mouse", 1)))
    worker.start()
    # Synchronize on the condition instead of an arbitrary test sleep.
    for _ in range(1000):
        with hardware.condition:
            if hardware.pending:
                break
        threading.Event().wait(0.001)
    pid, slot, nonce = hardware.pending
    reply = HidppResponseEvent(slot=slot, pid=pid, feature_index=0, function=1, sw_id=8, payload=bytes((2, 0, nonce)))
    for invalid in (copy.copy(reply), copy.copy(reply), copy.copy(reply)):
        invalid.payload = bytes((2, 0, (nonce + 1) % 256))
        hardware.notify(invalid)
        assert not hardware.answer
    reply.slot = 0  # Direct Bluetooth replies may use inverted device number.
    hardware.notify(reply)
    worker.join(2)
    assert result == [True]
    assert len(writes[0].hid_message) == 20
    assert not hardware.probe("mouse", 0.001)
    assert not hardware.known()
    hardware.switch("mouse", 1)
    assert writes[-1].hid_message[4] == 1
    with pytest.raises(ValueError):
        hardware.switch("mouse", 4)
    context.policy.paused.set()
    assert not hardware.allowed()
    hardware.notify(None)
    device.connected = False
    assert not hardware.probe("mouse")
    with pytest.raises(RuntimeError):
        hardware.switch("mouse", 1)
    hardware.close()
    assert message(1, 0x1234, b"\x00")[:4] == b"\x11\x01\x12\x34"


@pytest.mark.parametrize("lost_op", ["prepare", "mouse", "keyboard", "commit"])
def test_lost_acknowledgement_is_retried_without_duplicate_switch_or_rollback(tmp_path, lost_op):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    base = LocalLink(db)
    missed = []

    class Lossy:
        def call(self, op, args=None, timeout=3):
            result = base.call(op, args, timeout)
            if op == lost_op and not missed:
                missed.append(op)
                raise OSError("response lost after processing")
            return result

    ca = Coordinator(a, c, Lossy(), da)
    run(ca)
    assert owners == {"mouse": 1, "keyboard": 1}
    assert db.state["phase"] == "committed"
    assert not da.gate.locked() and not db.gate.locked()
    assert sum(e == (0, "switch", "mouse", 1) for e in events) == 1
    assert sum(e == (0, "switch", "keyboard", 1) for e in events) == 1
    assert "abort" not in base.calls
    assert ca.last_error is None


def test_transient_commit_ping_does_not_send_successfully_arrived_devices_back(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    original = b.probe
    missed = []

    def probe(role, timeout=0.45):
        if role == "mouse" and db.state and db.state["phase"] == "keyboard" and not missed:
            missed.append(role)
            return False
        return original(role, timeout)

    b.probe = probe
    run(Coordinator(a, c, LocalLink(db), da))
    assert missed == ["mouse"]
    assert db.state["phase"] == "committed"
    assert owners == {"mouse": 1, "keyboard": 1}
    assert not any(e[1] == "switch" and e[-1] == 0 for e in events)


def test_held_button_during_arrival_defers_placement_without_returning_devices(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    enter = d.enter
    attempts = []

    def held_once(side, y):
        attempts.append((side, y))
        if len(attempts) == 1:
            raise BlockingIOError("mouse button held during Bluetooth arrival")
        enter(side, y)

    d.enter = held_once
    ca = Coordinator(a, c, LocalLink(db), da)
    run(ca)
    assert db.state["phase"] == "committed"
    assert owners == {"mouse": 1, "keyboard": 1}
    assert len(attempts) == 2 and len(d.placements) == 1
    assert not any(e[1] == "switch" and e[-1] == 0 for e in events)


def test_confirmed_commit_clears_error_after_all_commit_replies_were_lost(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    link = LocalLink(db)
    ca = Coordinator(a, c, link, da)

    def uncertain_commit(tx):
        assert link.call("commit", {"tx": tx})["phase"] == "committed"
        return False  # Commit and status ACKs remained unavailable for the source deadline.

    ca._commit = uncertain_commit
    run(ca)
    assert owners == {"mouse": 1, "keyboard": 1}
    assert ca.phase == "committed" and ca.last_error is None
    assert read_file(da.journal.with_name("last-transfer.json"))["reason"] is None


def test_incoming_lease_and_outgoing_coordinator_share_one_gate(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    cb = Coordinator(b, d, LocalLink(da), db)
    args = {"tx": "a" * 32, "kind": "edge", "y": 0.5, "protocol": 2}
    assert db.handle("prepare", args)["accepted"]
    assert cb.busy is db.gate and cb.busy.locked()
    assert not cb.request()
    assert db.handle("prepare", args)["accepted"]  # Same transaction, no new lease.
    assert not db.handle("prepare", {**args, "tx": "b" * 32})["accepted"]
    db.handle("abort", {"tx": args["tx"]})
    assert not cb.busy.locked()
    assert not cb.request()  # Short quiet period after rollback, native buttons still work.
    assert not any(e[1] == "switch" for e in events)


def test_outgoing_gate_refuses_incoming_lease_and_manual_return(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    ca = Coordinator(a, c, LocalLink(db), da)
    assert ca.busy.acquire(blocking=False)
    try:
        result = da.handle("prepare", {"tx": "a" * 32, "kind": "edge", "y": 0.5, "protocol": 2})
        assert result == {"accepted": False, "reason": "busy"}
        assert da.handle("return", {}) == {"returned": False}
    finally:
        ca.busy.release()
    assert owners == {"mouse": 0, "keyboard": 0}
    assert not any(e[1] == "switch" for e in events)


def test_prepare_journal_failure_releases_gate_and_keeps_previous_state(tmp_path, mocker):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    mocker.patch.object(db, "_record", side_effect=OSError("disk"))
    with pytest.raises(OSError):
        db.handle("prepare", {"tx": "a" * 32, "kind": "edge", "y": 0.5, "protocol": 2})
    assert db.state is None and not db.gate.locked()
    assert owners == {"mouse": 0, "keyboard": 0}


def test_mixed_old_and_new_protocol_never_moves_inputs(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    assert db.handle("prepare", {"tx": "a" * 32, "kind": "edge", "y": 0.5}) == {
        "accepted": False,
        "reason": "update_required",
    }
    link = Mock()
    link.call.return_value = {"ready": True}  # Old peer without the shared gate protocol.
    ca = Coordinator(a, c, link, da)
    run(ca)
    assert "actualiza" in ca.status.lower()
    assert owners == {"mouse": 0, "keyboard": 0}
    assert not any(e[1] == "switch" for e in events)


def test_observed_native_keys_never_rewrite_current_channel_or_fallback_when_offline(tmp_path):
    from cleverswitch.event.host_change_event import HostChangeEvent

    a, b, c, d, da, db, owners, events = machines(tmp_path)
    origin = SimpleNamespace(pid=1, slot=1, role="keyboard")
    a.context = SimpleNamespace(device_registry=Mock(), policy=Mock())
    a.context.device_registry.all_entries.return_value = [origin]
    a.context.policy.allows.return_value = True
    ca = Coordinator(a, c, LocalLink(db), da)
    for target in (0, 2):
        assert ca.keyboard_event(HostChangeEvent(1, 1, target))
    assert not events
    link = Mock()
    link.call.side_effect = OSError("offline")
    ca.link = link
    run(ca, "keyboard")
    assert owners == {"mouse": 0, "keyboard": 0}
    assert not any(e[1] == "switch" for e in events)


def test_presence_retry_is_finite_under_persistent_rpc_loss(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    now = [0.0]
    ca = Coordinator(a, c, Mock(), da, clock=lambda: now[0])
    ca.link.call.side_effect = OSError("offline")
    stop = Mock()
    stop.is_set.return_value = False
    stop.wait.side_effect = lambda dt: now.__setitem__(0, now[0] + dt) or False
    ca.stop_event = stop
    assert not ca._confirmed("mouse", "a" * 32)
    assert 8 <= now[0] < 8.2
    assert ca.link.call.call_count < 60
    assert all(0 < c.kwargs["timeout"] <= 2 for c in ca.link.call.call_args_list)


def test_commit_stays_pending_then_lease_returns_inputs_if_radio_never_recovers(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    now = [0]
    db.clock = lambda: now[0]
    tx = "a" * 32
    db.handle("prepare", {"tx": tx, "kind": "edge", "y": 0.5, "protocol": 2})
    owners["mouse"] = 1
    assert db.handle("mouse", {"tx": tx})["present"]
    owners["keyboard"] = 1
    assert db.handle("keyboard", {"tx": tx})["present"]
    b.missing.add("mouse")
    assert db.handle("commit", {"tx": tx}) == {"phase": "keyboard", "pending": True}
    assert owners == {"mouse": 1, "keyboard": 1} and db.gate.locked()
    b.missing.clear()
    now[0] = 31
    db.expire()
    assert owners == {"mouse": 0, "keyboard": 0}
    assert db.state["phase"] == "aborted" and not db.gate.locked()


@pytest.mark.parametrize("host", [0, 1])
def test_real_tls_repeated_handoffs_with_lost_responses(paired_tls, tmp_path, host):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    faults = set()

    def handler(destination):
        def receive(op, args):
            result = destination.handle(op, args)
            if op in ("prepare", "mouse", "keyboard", "commit"):
                key = (destination.host, args["tx"], op)
                if key not in faults:
                    faults.add(key)
                    raise OSError("one lost ACK per stage, over actual TLS")
            return result

        return receive

    la, lb = Link(paired_tls[0], handler(da), 0), Link(paired_tls[1], handler(db), 0)
    start_tls_peers(la, lb)
    try:
        ca, cb = Coordinator(a, c, la, da), Coordinator(b, d, lb, db)
        owners.update(mouse=host, keyboard=host)
        for step in range(6):
            source = (host + step) % 2
            run(ca if source == 0 else cb, y=0.5)
            assert owners == {"mouse": 1 - source, "keyboard": 1 - source}
            assert not da.gate.locked() and not db.gate.locked()
            assert (db if source == 0 else da).state["phase"] == "committed"
        assert len(faults) == 24
    finally:
        la.close()
        lb.close()
