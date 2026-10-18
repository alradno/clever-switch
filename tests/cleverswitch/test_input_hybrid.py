"""Complete input Service over real pinned TLS and shared UDP, with fake input."""

import dataclasses
import socket
import threading
import time

import pytest

from cleverswitch.flow.input_motion import Motion
from cleverswitch.flow.input_protocol import Decoder, Kind, Packet, Routed
from cleverswitch.flow.input_stream import Channel
from cleverswitch.flow.network import Discovery

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_network import Relay
from .test_input_service import enter
from .test_input_service import services as services
from .test_input_stream import until


@pytest.fixture
def transports(services):
    discoveries = [Discovery(p.link, 0) for p in services]
    for discovery in discoveries:
        discovery.start()
    discoveries[0].peer_port, discoveries[1].peer_port = discoveries[1].port, discoveries[0].port
    try:
        yield services, discoveries
    finally:
        for peer in services:
            peer.suspend()
        for discovery in discoveries:
            discovery.close()


def connect(peers, *, ready=True):
    assert peers[0].connect(peers[1].capabilities())
    until(lambda: all(p.channel and p.channel.lane for p in peers))
    if ready:
        until(lambda: all(p.channel.lane.probe.state == "ready" for p in peers))


@pytest.fixture
def hybrid(transports):
    peers, _ = transports
    connect(peers)
    return peers


def test_border_during_bootstrap_keeps_input_local(transports, monkeypatch):
    from cleverswitch.flow import input_stream

    peers, _ = transports
    entered, release = threading.Event(), threading.Event()
    original = input_stream.bootstrap
    errors = []

    def held(connection, link):
        if link.config["role"] == "windows":
            entered.set()
            assert release.wait(1)
        return original(connection, link)

    def connect_peer():
        try:
            peers[0].connect(peers[1].capabilities())
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(input_stream, "bootstrap", held)
    worker = threading.Thread(target=connect_peer)
    worker.start()
    try:
        assert entered.wait(1)
        until(lambda: all(p.channel is not None for p in peers))
        for peer in peers:
            x, dx = (peer.local.right, 100) if peer.role == "windows" else (0, -100)
            assert not peer.motion(x - dx, 250, dx, 0)
            assert not peer.motion(x, 250, dx, 0)
            assert not peer.backend.captured and peer.session.phase == "local"
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and not errors
    until(lambda: all(p._available() for p in peers))
    enter(peers[0])


def test_close_during_bootstrap_retires_keys_and_backend(transports, monkeypatch):
    from cleverswitch.flow import input_stream

    peers, discoveries = transports
    entered, release = threading.Event(), threading.Event()
    original = input_stream.bootstrap
    codecs, errors = [], []

    def held(connection, link):
        negotiation = original(connection, link)
        if link.config["role"] == "windows":
            codecs.append(negotiation.codec)
            entered.set()
            assert release.wait(1)
        return negotiation

    def connect_peer():
        try:
            peers[0].connect(peers[1].capabilities())
        except ConnectionError as error:
            errors.append(error)

    monkeypatch.setattr(input_stream, "bootstrap", held)
    worker = threading.Thread(target=connect_peer)
    worker.start()
    try:
        assert entered.wait(1)
        peers[0].channel.request_close()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and len(errors) == 1
    for peer in peers:
        assert peer.channel.finished.wait(1)
        peer.channel.close()
        assert not peer._available() and not peer.backend.captured
        assert peer.backend.closes == 1
    assert codecs[0].tx is None and codecs[0].rx is None
    assert all(d.motion.current is None for d in discoveries)


def test_worker_start_failure_closes_once_and_remains_closeable(monkeypatch):
    a, b = socket.socketpair()
    closed = []
    channel = Channel(a, lambda packet: None, lambda: closed.append(True))

    def fail_start(worker):
        raise RuntimeError("Cannot start thread")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    try:
        with pytest.raises(RuntimeError, match="Cannot start thread"):
            channel.start()
        assert channel.finished.is_set()
        channel.close()
        assert closed == [True]
    finally:
        b.close()


@pytest.mark.parametrize("unavailable", [0, 1])
@pytest.mark.parametrize("source", [0, 1])
def test_missing_capability_uses_original_reliable_service(transports, monkeypatch, unavailable, source):
    peers, _ = transports
    monkeypatch.setattr(peers[unavailable], "capabilities", lambda: {})
    assert peers[0].connect(peers[1].capabilities())
    until(lambda: all(p._available() for p in peers))
    assert all(p.channel.lane is None for p in peers)
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    sender.motion(100, 200, 100 if source == 0 else -100, 0)
    sender.key(4, 1)
    sender.key(4, 0)
    until(lambda: receiver.backend.events[-1].kind == Kind.KEY and receiver.backend.events[-1].values == (4, 0))
    assert not receiver.session.keys


def test_capability_withdrawn_after_status_falls_back_without_bootstrap(transports):
    peers, discoveries = transports
    advertised = peers[1].capabilities()
    assert advertised
    discoveries[1].close()
    assert peers[0].connect(advertised)
    until(lambda: all(p._available() for p in peers))
    assert all(p.channel.lane is None for p in peers)
    enter(peers[0])


def test_unsolicited_motion_upgrade_is_rejected_before_backend_starts(services):
    sender = services[0]
    response = services[1].offer(motion=True)
    with pytest.raises(ValueError, match="no solicitado"):
        sender.attach(None, response)
    assert sender.backend.starts == 0 and sender.channel is None


@pytest.mark.parametrize("source", [0, 1])
def test_closed_udp_listener_reverts_to_tls_without_losing_pressed_input(hybrid, source):
    sender, receiver = hybrid[source], hybrid[1 - source]
    enter(sender)
    sender.key(4, 1)
    until(lambda: receiver.session.keys == {4})
    transport = sender.channel.transport
    transport.close()
    assert not sender.capabilities() and not transport.motion_ready()
    sender.session.input(Kind.MOVE, 333, 444)
    sender.key(4, 0)
    until(lambda: receiver.backend.events[-1].kind == Kind.KEY and receiver.backend.events[-1].values == (4, 0))
    assert not receiver.session.keys
    assert sender.channel.lane.sender.mode == "reliable"
    assert receiver.backend.events[-2].values == (333, 444)
    assert not sender.channel.stop.is_set()


@pytest.mark.parametrize("kind,values", [
    (Kind.MOVE, (100.0, 200.0)), (Kind.KEY, (4, 1)), (Kind.BUTTON, (0, 0)),
    (Kind.WHEEL, (1.5, -3.0)), (Kind.LOCKS, (1, 3)),
])
def test_action_position_envelope_roundtrips_all_fragment_boundaries(kind, values):
    frame = Routed(Packet(kind, 42, values), 1, 7, (100, 200))
    encoded = frame.encode(1)
    for size in range(1, len(encoded) + 1):
        decoder = Decoder(routed=True)
        decoded = []
        for i in range(0, len(encoded), size):
            decoded.extend(decoder.feed(encoded[i:i + size]))
        assert decoded == [frame] and not decoder.buffer
    with pytest.raises(ValueError):
        Decoder().feed(encoded)
    assert "100" not in repr(frame) and "values" not in repr(frame)


@pytest.mark.parametrize("change", [
    {"action": True}, {"action": -1}, {"serial": -1}, {"serial": 2**64},
    {"position": (float("nan"), 1)}, {"packet": Packet(Kind.ENTER, 1, (100, 200))},
    {"packet": Packet(Kind.MOVE, 1, (300, 200))},
])
def test_invalid_action_envelope_is_rejected(change):
    frame = Routed(Packet(Kind.KEY, 1, (4, 1)), 1, 1, (100, 200))
    with pytest.raises(ValueError):
        dataclasses.replace(frame, **change)


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
def test_service_crosses_moves_clicks_types_and_returns_with_hybrid_transport(hybrid, source):
    sender, receiver = hybrid[source], hybrid[1 - source]
    enter(sender)
    expected = []
    for block in range(8):
        for index in range(40):
            assert sender.session.input(Kind.MOVE, 100 + block * 40 + index, 300)
        position = (139 + block * 40, 300)
        for kind, values in ((Kind.KEY, (4, 1)), (Kind.BUTTON, (0, 1)), (Kind.BUTTON, (0, 0)), (Kind.KEY, (4, 0))):
            assert sender.session.input(kind, *values)
            expected.append((kind, values, position))
    assert sender.session.input(Kind.MOVE, 600, 400)
    until(lambda: receiver.backend.events[-1].values == (600, 400))
    cursor, observed = None, []
    for packet in receiver.backend.events:
        if packet.kind == Kind.MOVE:
            cursor = packet.values
        else:
            observed.append((packet.kind, packet.values, cursor))
    assert observed == expected
    assert sender.channel.lane.sent > 0 and receiver.channel.lane.received > 0
    assert sender.channel.lane.sender.mode == "datagram"
    assert sender.session.leave()
    until(lambda: receiver.session.phase == "local")
    assert not receiver.session.keys and not receiver.session.buttons and not sender.backend.captured
    assert all(peer.channel.failure is None for peer in hybrid)


@pytest.mark.parametrize("source", [0, 1])
def test_udp_blocked_before_connection_stays_reliable_and_stops_probing(transports, monkeypatch, source):
    peers, discoveries = transports
    attempts = []
    for discovery in discoveries:
        monkeypatch.setattr(discovery, "send_motion", lambda *args: attempts.append(len(args[0])))
    connect(peers, ready=False)
    until(lambda: all(p.channel.lane.probe.state == "unavailable" for p in peers))
    assert len(attempts) == 6
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    assert sender.session.input(Kind.MOVE, 100, 200)
    assert sender.session.input(Kind.KEY, 4, 1)
    assert sender.session.input(Kind.KEY, 4, 0)
    until(lambda: len(receiver.backend.events) >= 4)
    assert sender.channel.lane.sender.mode == "reliable"
    assert sender.session.leave()
    until(lambda: receiver.session.phase == "local")
    time.sleep(0.12)
    assert len(attempts) == 6
    assert all(p.channel.lane.deadline is None for p in peers)


@pytest.mark.parametrize("source", [0, 1])
def test_udp_path_failure_bridges_to_reliable_and_does_not_retry_next_focus(hybrid, monkeypatch, source):
    sender, receiver = hybrid[source], hybrid[1 - source]
    enter(sender)
    attempts = []
    monkeypatch.setattr(sender.channel.transport, "send_motion", lambda *args: attempts.append(len(args[0])))
    assert sender.session.input(Kind.MOVE, 123, 234)
    until(lambda: receiver.backend.events[-1].values == (123, 234))
    until(lambda: sender.channel.lane.probe.state == "unavailable")
    assert sender.channel.lane.sender.mode == "reliable" and len(attempts) == 4
    assert sender.session.leave()
    until(lambda: receiver.session.phase == "local")
    assert sender.session.enter(100, 200)
    until(lambda: sender.session.phase == "sending")
    sender.session.input(Kind.MOVE, 345, 456)
    until(lambda: receiver.backend.events[-1].values == (345, 456))
    assert len(attempts) == 4 and sender.channel.lane.sender.mode == "reliable"


@pytest.mark.parametrize("source", [0, 1])
def test_last_lost_datagram_is_retried_and_delivered_once(hybrid, monkeypatch, source):
    sender, receiver = hybrid[source], hybrid[1 - source]
    enter(sender)
    transport = sender.channel.transport
    original = transport.send_motion
    calls = []

    def send(raw, peer):
        calls.append(raw)
        if len(calls) > 1:
            original(raw, peer)

    monkeypatch.setattr(transport, "send_motion", send)
    sender.session.input(Kind.MOVE, 234, 345)
    until(lambda: receiver.backend.events[-1].values == (234, 345))
    until(lambda: sender.channel.lane.sender.latest is None)
    assert len(calls) >= 2 and calls[0] != calls[1]
    assert sum(p.kind == Kind.MOVE and p.values == (234, 345) for p in receiver.backend.events) == 1
    assert sender.channel.lane.sender.deadline is None


@pytest.fixture
def relayed_hybrid(transports):
    peers, _ = transports
    relay = Relay(peers[0].link.port, delay=0)
    peers[0].link.port = relay.port
    try:
        connect(peers)
        yield peers, relay
    finally:
        relay.close()


@pytest.mark.parametrize("source", [0, 1])
def test_motion_progresses_while_reliable_bytes_are_held(relayed_hybrid, source):
    peers, relay = relayed_hybrid
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    relay.resume[source].clear()
    try:
        sender.channel.send(Packet(Kind.PING, 900))
        assert relay.blocked[source].wait(0.3)
        sender.session.input(Kind.MOVE, 432, 543)
        until(lambda: receiver.backend.events[-1].values == (432, 543), timeout=0.3)
        assert sender.channel.lane.sender.mode == "datagram"
    finally:
        relay.resume[source].set()


@pytest.mark.parametrize("source", [0, 1])
def test_future_udp_waits_for_stalled_action_at_its_exact_position(relayed_hybrid, source):
    peers, relay = relayed_hybrid
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    sender.session.input(Kind.MOVE, 100, 200)
    until(lambda: receiver.backend.events[-1].values == (100, 200))
    relay.resume[source].clear()
    try:
        sender.session.input(Kind.BUTTON, 0, 1)
        assert relay.blocked[source].wait(0.3)
        sender.session.input(Kind.MOVE, 300, 400)
        until(lambda: receiver.channel.lane.receiver.pending is not None, timeout=0.3)
        assert receiver.backend.events[-1].values == (100, 200)
        assert not receiver.session.buttons
    finally:
        relay.resume[source].set()
    until(lambda: receiver.backend.events[-1].values == (300, 400))
    clicks = [i for i, p in enumerate(receiver.backend.events) if p.kind == Kind.BUTTON]
    assert len(clicks) == 1 and receiver.backend.events[clicks[0] - 1].values == (100, 200)
    sender.session.input(Kind.BUTTON, 0, 0)
    until(lambda: not receiver.session.buttons)


@pytest.mark.parametrize("source", [0, 1])
def test_cancel_discards_future_motion_and_late_actions(relayed_hybrid, source):
    peers, relay = relayed_hybrid
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    before = receiver.channel.received
    relay.resume[source].clear()
    try:
        sender.session.input(Kind.KEY, 4, 1)
        assert relay.blocked[source].wait(0.3)
        sender.session.input(Kind.MOVE, 500, 600)
        until(lambda: receiver.channel.lane.receiver.pending is not None, timeout=0.3)
        receiver.session.cancel("local")
        until(lambda: sender.session.phase == "local")
    finally:
        relay.resume[source].set()
    until(lambda: receiver.channel.received == before + 1)
    assert receiver.session.phase == "local" and not receiver.session.keys
    assert receiver.channel.lane.receiver.pending is None
    assert not any(p.kind == Kind.KEY or p.values == (500, 600) for p in receiver.backend.events)


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("queued", [False, True])
def test_keyboard_channel_change_recovers_held_keys_without_losing_mouse(hybrid, source, queued):
    sender, receiver = hybrid[source], hybrid[1 - source]
    enter(sender)
    sender.key(0xE1, 1)
    until(lambda: receiver.session.keys == {0xE1})
    sender.keyboard_changed()
    until(lambda: not receiver.session.keys)
    blocked, release = threading.Event(), threading.Event()

    def hold_session():
        with sender.session.lock:
            blocked.set()
            release.wait(2)

    worker = threading.Thread(target=hold_session) if queued else None
    if worker is not None:
        worker.start()
    try:
        if worker is not None:
            assert blocked.wait(1)
        assert sender.key(4, 1)
        assert sender.key(4, 0)
        x, y = sender.remote_position
        # Mouse and keyboard must use the same callback queue. A direct
        # Session.input could overtake deferred keys only in this test.
        assert sender.motion(222, 333, 222 - x, 333 - y)
    finally:
        release.set()
        if worker is not None:
            worker.join(2)
            assert not worker.is_alive()
    until(lambda: receiver.backend.events[-1].values == (222, 333))
    assert [(p.kind, p.values) for p in receiver.backend.events[-3:]] == [
        (Kind.KEY, (4, 1)), (Kind.KEY, (4, 0)), (Kind.MOVE, (222, 333))
    ]
    assert not receiver.session.keys and sender.session.phase == "sending"
    assert sender.channel.lane.sender.mode == "datagram"


def test_reconnect_retires_keys_inbox_and_late_old_datagram(hybrid):
    old_channels = [p.channel for p in hybrid]
    old_codecs = [c.lane.codec for c in old_channels]
    raw = old_codecs[0].seal(Motion(1, 1, 0, (100, 200)))
    for peer in hybrid:
        peer.suspend()
    connect(hybrid)
    for channel in old_channels:
        channel.close()
    assert all(c.tx is None for c in old_codecs)
    target = hybrid[1].channel
    assert target.transport.motion.current is target.inbox
    assert not target.transport.motion.receive(raw, target.inbox.peer)
    assert all(p.session.phase == "local" for p in hybrid)
    enter(hybrid[0])
    hybrid[0].session.input(Kind.MOVE, 333, 444)
    until(lambda: hybrid[1].backend.events[-1].values == (333, 444))


def test_authenticated_datagrams_cannot_create_focus_or_capture(hybrid):
    source, target = hybrid
    raw = source.channel.lane.codec.seal(Motion(123, 1, 0, (100, 200)))
    before = target.channel.lane.received
    source.channel.transport.send_motion(raw, ("127.0.0.1", target.channel.transport.port))
    until(lambda: target.channel.lane.received > before)
    assert target.session.phase == "local" and not target.backend.captured
    assert not target.backend.events


@pytest.mark.parametrize("source", [0, 1])
def test_normal_leave_preserves_actions_already_queued_at_their_position(hybrid, source):
    sender, receiver = hybrid[source], hybrid[1 - source]
    enter(sender)
    entered, release = threading.Event(), threading.Event()
    original_tick = sender.channel.on_tick

    def hold_once():
        if not entered.is_set():
            entered.set()
            assert release.wait(1)
        original_tick()

    sender.channel.on_tick = hold_once
    sender.channel._wake()
    try:
        assert entered.wait(1)
        sender.session.input(Kind.MOVE, 400, 500)
        sender.session.input(Kind.KEY, 4, 1)
        sender.session.input(Kind.KEY, 4, 0)
        sender.session.input(Kind.BUTTON, 0, 1)
        sender.session.input(Kind.BUTTON, 0, 0)
        sender.session.leave()
    finally:
        release.set()
    until(lambda: receiver.session.phase == "local")
    actions = []
    position = None
    for packet in receiver.backend.events:
        if packet.kind == Kind.MOVE:
            position = packet.values
        else:
            actions.append((packet.kind, packet.values, position))
    assert actions == [
        (Kind.KEY, (4, 1), (400, 500)), (Kind.KEY, (4, 0), (400, 500)),
        (Kind.BUTTON, (0, 1), (400, 500)), (Kind.BUTTON, (0, 0), (400, 500)),
    ]
    assert not receiver.session.keys and not receiver.session.buttons


@pytest.mark.parametrize("source", [0, 1])
def test_delayed_receiver_uses_latest_udp_position_without_replaying_a_burst(hybrid, source):
    sender, receiver = hybrid[source], hybrid[1 - source]
    enter(sender)
    held, release, emitted = threading.Event(), threading.Event(), threading.Event()
    original_receiver_tick = receiver.channel.on_tick
    original_sender_tick = sender.channel.on_tick
    before = len(receiver.backend.events)

    def hold_receiver():
        if not held.is_set():
            held.set()
            assert release.wait(1)
        original_receiver_tick()

    def burst_on_owner():
        if not emitted.is_set():
            # Use the TLS owner for every nonce and sender-state mutation.
            for index in range(32):
                sender.channel.lane.outgoing(
                    Packet(Kind.MOVE, sender.session.epoch, (100 + index, 400)), time.monotonic()
                )
            emitted.set()
        original_sender_tick()

    receiver.channel.on_tick = hold_receiver
    receiver.channel._wake()
    try:
        assert held.wait(1)
        sender.channel.on_tick = burst_on_owner
        sender.channel._wake()
        assert emitted.wait(1)
        until(lambda: len(receiver.channel.inbox.queue) >= 32, timeout=0.3)
    finally:
        release.set()
    until(lambda: receiver.backend.events[-1].values == (131, 400))
    assert [(p.kind, p.values) for p in receiver.backend.events[before:]] == [(Kind.MOVE, (131, 400))]
    until(lambda: sender.channel.lane.sender.latest is None)
    assert sender.channel.lane.deadline is None
    assert all(p.channel.failure is None for p in hybrid)
