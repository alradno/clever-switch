import socket
import ssl
import threading
import time

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import VERSION, Decoder, Kind, Packet
from cleverswitch.flow.input_session import Session
from cleverswitch.flow.input_stream import Channel
from cleverswitch.flow.network import Link

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_core import Backend


def until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true")
        time.sleep(0.002)


class Endpoint:
    def __init__(self, pairing):
        self.role = pairing.load()["role"]
        self.desktop = Desktop([Display(0, 0, 1920, 1080)])
        self.link = Link(pairing, lambda op, args: {"ready": True}, 0)
        self.link.input_accept = self.accept
        self.backend = Backend()
        self.session = self.channel = None

    def offer(self):
        return {"version": VERSION, "displays": self.desktop.wire()}

    def accept(self, args):
        self.validate(args)
        return self.offer(), self.attach

    def validate(self, value):
        if set(value) != {"version", "displays"} or type(value["version"]) is not int or value["version"] != VERSION:
            raise ValueError("Entrada incompatible")
        Desktop.from_wire(value["displays"])

    def attach(self, connection, response=None):
        if response is not None:
            self.validate(response)
        self.session = Session(self.role, self.desktop, self.backend, lambda packet: self.channel.send(packet))
        self.channel = Channel(connection, self.session.receive, self.session.close, self.session.tick)
        self.session.on_active = self.channel.set_active
        return self.channel


@pytest.fixture
def endpoints(paired_tls):
    peers = [Endpoint(p) for p in paired_tls]
    for peer in peers:
        peer.link.start()
    peers[0].link.port, peers[1].link.port = peers[1].link.port, peers[0].link.port
    try:
        yield peers
    finally:
        for peer in peers:
            peer.link.close()


def connect(peers):
    peers[0].link.open_input(peers[0].offer(), peers[0].attach)
    until(lambda: peers[1].channel is not None)


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
def test_real_persistent_mutual_tls_moves_keys_and_rpc_stays_available(endpoints, source):
    connect(endpoints)
    a, b = endpoints[source], endpoints[1 - source]
    assert a.session.enter(100, 200)
    until(lambda: a.session.phase == "sending")
    for _ in range(60):
        assert a.session.input(Kind.KEY, 4, 1)
        assert a.session.input(Kind.KEY, 4, 0)
    until(lambda: len(b.backend.events) == 121)
    assert b.session.keys == set()
    assert a.link.call("ready") == {"ready": True}
    assert a.session.leave()
    until(lambda: b.session.phase == "local")
    assert not a.backend.captured
    assert a.channel.connection.version() == "TLSv1.3"


def test_disabled_peer_never_accepts_input_even_when_already_paired(endpoints):
    endpoints[1].link.input_accept = None
    with pytest.raises((ConnectionError, OSError)):
        endpoints[0].link.open_input(endpoints[0].offer(), endpoints[0].attach)
    assert endpoints[1].channel is None
    assert endpoints[0].link.call("ready")["ready"]


def test_disabled_source_does_not_open_a_stream(endpoints):
    endpoints[0].link.input_accept = None
    with pytest.raises(ConnectionError):
        endpoints[0].link.open_input(endpoints[0].offer(), endpoints[0].attach)
    assert endpoints[1].channel is None


def test_only_one_stream_per_link_and_no_second_capture(endpoints):
    connect(endpoints)
    original = endpoints[0].channel
    with pytest.raises(ConnectionError):
        endpoints[0].link.open_input(endpoints[0].offer(), endpoints[0].attach)
    assert endpoints[0].channel is original
    assert not original.stop.is_set()


def test_version_mismatch_and_wrong_pin_refused(endpoints, mocker):
    offer = endpoints[0].offer()
    offer["version"] = VERSION + 1
    with pytest.raises(ConnectionError):
        endpoints[0].link.open_input(offer, endpoints[0].attach)
    mocker.patch.object(endpoints[0].link.pairing, "verify_pin", side_effect=ssl.SSLError())
    with pytest.raises(ssl.SSLError):
        endpoints[0].link.open_input(endpoints[0].offer(), endpoints[0].attach)
    assert endpoints[1].channel is None


def test_drop_restores_local_capture_and_releases_remote_modifier(endpoints):
    connect(endpoints)
    a, b = endpoints
    a.session.enter(100, 200)
    until(lambda: a.session.phase == "sending")
    a.session.input(Kind.KEY, 0xE0, 1)
    until(lambda: b.session.keys == {0xE0})
    b.channel.close()
    until(lambda: a.channel.finished.is_set())
    assert not a.backend.captured
    assert not b.session.keys
    assert b.backend.events[-1].values == (0xE0, 0)


def test_idle_stream_does_not_poll_or_emit_fast_heartbeats(endpoints):
    connect(endpoints)
    time.sleep(0.15)
    assert all(p.channel.sent == p.channel.received == 0 for p in endpoints)
    start = time.monotonic()
    for peer in endpoints:
        peer.link.close()
    assert time.monotonic() - start < 1


def test_silent_peer_cannot_keep_capture_forever():
    a, b = socket.socketpair()
    closed = threading.Event()
    channel = Channel(a, lambda p: None, closed.set, active_interval=0.02, active_timeout=0.08)
    try:
        channel.set_active(True)
        channel.start()
        assert closed.wait(0.5)
        assert channel.failure == "timeout"
    finally:
        channel.close()
        b.close()


def test_queue_saturation_does_not_block_native_callback():
    a, b = socket.socketpair()
    channel = Channel(a, lambda p: None, lambda: None)
    try:
        for _ in range(256):
            channel.send(Packet(Kind.KEY, 1, (4, 1)))
        start = time.monotonic()
        with pytest.raises(BufferError):
            channel.send(Packet(Kind.KEY, 1, (4, 0)))
        assert time.monotonic() - start < 0.1
        assert channel.stop.is_set()
    finally:
        channel.close()
        b.close()


def test_partial_frame_is_discarded_on_close_and_callback_fires_once():
    a, b = socket.socketpair()
    calls, packets = [], []
    channel = Channel(a, packets.append, lambda: calls.append("closed"))
    channel.start()
    b.sendall(Packet(Kind.KEY, 1, (4, 1)).encode(1)[:-1])
    b.close()
    assert channel.finished.wait(1)
    channel.close()
    channel.close()
    assert packets == [] and calls == ["closed"]


def test_stream_reopens_only_after_old_cleanup_finishes(endpoints):
    connect(endpoints)
    first = endpoints[0].channel
    first.close()
    until(lambda: endpoints[1].channel.finished.is_set())
    connect(endpoints)
    assert endpoints[0].channel is not first
    assert not endpoints[0].channel.stop.is_set()


def test_invalid_frame_closes_channel_without_injection():
    a, b = socket.socketpair()
    packets = []
    channel = Channel(a, packets.append, lambda: None)
    channel.start()
    try:
        b.sendall(b"FAIL" + Packet(Kind.KEY, 1, (4, 1)).encode(1)[4:])
        assert channel.finished.wait(1)
        assert channel.failure == "protocol"
        assert packets == []
    finally:
        channel.close()
        b.close()


def test_receiver_coalesces_available_motion_but_keeps_click_position_and_epoch():
    a, b = socket.socketpair()
    a.setblocking(False)
    channel = Channel(a, lambda p: None, lambda: None)
    packets = [
        Packet(Kind.MOVE, 1, (10, 20)),
        Packet(Kind.MOVE, 1, (20, 30)),
        Packet(Kind.BUTTON, 1, (0, 1)),
        Packet(Kind.MOVE, 1, (30, 40)),
        Packet(Kind.MOVE, 1, (40, 50)),
        Packet(Kind.KEY, 1, (4, 1)),
        Packet(Kind.MOVE, 1, (50, 60)),
        Packet(Kind.MOVE, 2, (60, 70)),
    ]
    try:
        b.sendall(b"".join(packet.encode(i) for i, packet in enumerate(packets, 1)))
        decoded, wants_write = channel._read_packets(Decoder())
        assert decoded == [packets[i] for i in (1, 2, 4, 5, 6, 7)]
        assert channel.received_coalesced == 2 and not wants_write
    finally:
        channel.close()
        b.close()


def test_queued_actions_share_bounded_writes_without_a_batching_delay():
    a, b = socket.socketpair()
    writes, received = [], []

    class ObservedSocket:
        def __getattr__(self, name):
            return getattr(a, name)

        def send(self, data):
            writes.append(len(data))
            return a.send(data)

    channel = Channel(ObservedSocket(), lambda packet: None, lambda: None)
    receiver = Channel(b, received.append, lambda: None)
    actions = [Packet(Kind.KEY, 7, (4, down)) for _ in range(100) for down in (1, 0)]
    try:
        for packet in actions:
            channel.send(packet)
        receiver.start()
        channel.start()
        until(lambda: len(received) == len(actions) and channel.sent == len(actions))
        assert received == actions
        assert len(writes) == 4  # At most 64 already queued frames per write.
        assert channel.sent == len(actions)
        before = time.monotonic()
        channel.send(Packet(Kind.MOVE, 7, (12, 20)))
        until(lambda: len(received) == len(actions) + 1)
        assert time.monotonic() - before < 0.1  # A lone frame does not wait for more.
    finally:
        channel.close()
        receiver.close()


def test_coalesced_outbox_only_wakes_the_transport_when_work_becomes_available(mocker):
    a, b = socket.socketpair()
    channel = Channel(a, lambda packet: None, lambda: None)
    wake = mocker.patch.object(channel, "_wake")
    try:
        for x in range(1000):
            channel.send(Packet(Kind.MOVE, 1, (x, 0)))
        channel.send(Packet(Kind.BUTTON, 1, (0, 1)))
        assert wake.call_count == 1
        assert channel.outbox.coalesced == 999
    finally:
        channel.close()
        b.close()


def test_short_writes_and_tls_retry_preserve_every_batched_action():
    a, b = socket.socketpair()
    attempts, received = [], []

    class ShortSocket:
        def __getattr__(self, name):
            return getattr(a, name)

        def send(self, data):
            attempts.append(data)
            if len(attempts) % 3 == 1:
                raise ssl.SSLWantWriteError()
            return a.send(data[:17])

    channel = Channel(ShortSocket(), lambda packet: None, lambda: None)
    receiver = Channel(b, received.append, lambda: None)
    packets = [Packet(Kind.KEY, 9, (4, down)) for _ in range(10) for down in (1, 0)]
    try:
        for packet in packets:
            channel.send(packet)
        channel.start()
        receiver.start()
        until(lambda: received == packets and channel.sent == len(packets))
        assert attempts[0] == attempts[1]  # OpenSSL gets identical bytes after WANT_WRITE.
        assert channel.sent == len(packets)
        assert not channel.stop.is_set()
    finally:
        channel.close()
        receiver.close()


@pytest.mark.parametrize("source", [0, 1])
def test_round_trip_uses_existing_authenticated_heartbeat_without_input_history(endpoints, source):
    connect(endpoints)
    peer = endpoints[source]
    assert peer.channel.timing() is None
    assert peer.session.enter(10, 20)
    until(lambda: peer.channel.timing() is not None and peer.channel.timing()["samples"] >= 2)
    timing = peer.channel.timing()
    assert 0 <= timing["minimum_ms"] <= timing["average_ms"] <= timing["maximum_ms"]
    assert timing["age_seconds"] < 1
    assert timing["mean_variation_ms"] >= 0
    assert all(type(v) in (int, float) for v in timing.values())


def test_round_trip_aggregate_and_unmatched_pong(mocker):
    a, b = socket.socketpair()
    channel = Channel(a, lambda packet: None, lambda: None)
    try:
        channel.start()
        b.sendall(Packet(Kind.PONG, 1).encode(1))
        b.sendall(Packet(Kind.KEY, 1, (4, 0)).encode(2))
        until(lambda: channel.received == 1)
        assert channel.timing() is None  # Unsolicited replies are not measurements.
        channel._round_trip(0.010, 1.0)
        channel._round_trip(0.030, 2.0)
        channel._round_trip(0.020, 3.0)
        mocker.patch("cleverswitch.flow.input_stream.time.perf_counter", return_value=4.0)
        assert channel.timing() == {
            "samples": 3,
            "latest_ms": 20.0,
            "minimum_ms": 10.0,
            "average_ms": 20.0,
            "maximum_ms": 30.0,
            "mean_variation_ms": 15.0,
            "age_seconds": 1.0,
        }
    finally:
        channel.close()
        b.close()


@pytest.mark.parametrize("closing_at", ["tick", "packet"])
def test_close_stops_current_receive_batch_and_unsent_input(closing_at):
    a, b = socket.socketpair()
    received = []
    send_attempts = []

    class ObservedSocket:
        def __getattr__(self, name):
            return getattr(a, name)

        def send(self, data):
            send_attempts.append(len(data))
            return a.send(data)

    def on_packet(packet):
        received.append(packet)
        if closing_at == "packet":
            channel.request_close()

    def on_tick():
        if closing_at == "tick":
            channel.request_close()

    channel = Channel(ObservedSocket(), on_packet, lambda: None, on_tick)
    incoming = [Packet(Kind.KEY, 1, (4, down)) for down in (1, 0)]
    try:
        channel.send(Packet(Kind.BUTTON, 1, (0, 1)))
        b.sendall(b"".join(packet.encode(i) for i, packet in enumerate(incoming, 1)))
        channel.start()
        assert channel.finished.wait(1)
        assert received == ([] if closing_at == "tick" else incoming[:1])
        assert channel.failure is None
        assert send_attempts == []  # Detect even a partial send after cancellation.
        b.settimeout(0.2)
        try:
            assert b.recv(8192) == b""
        except ConnectionResetError:
            pass  # Windows TCP socketpair may reset when incoming bytes remain unread.
    finally:
        channel.close()
        b.close()


@pytest.mark.parametrize("barrier", [False, True])
def test_unsent_motion_stays_fresh_until_socket_is_writable(barrier, mocker):
    a, b = socket.socketpair()
    b.settimeout(1)
    channel = Channel(a, lambda packet: None, lambda: None)
    waiting, release = threading.Event(), threading.Event()
    import select

    real_select = select.select

    def blocked_writer(readers, writers, errors, timeout):
        if a in writers:
            waiting.set()
            assert release.wait(1)
        return real_select(readers, writers, errors, timeout)

    mocker.patch("cleverswitch.flow.input_stream.select.select", side_effect=blocked_writer)
    try:
        channel.send(Packet(Kind.MOVE, 8, (1, 100)))
        channel.start()
        assert waiting.wait(1)
        for x in range(2, 21):
            channel.send(Packet(Kind.MOVE, 8, (x, 100)))
            if barrier and x == 10:
                channel.send(Packet(Kind.BUTTON, 8, (0, 1)))
        if barrier:
            channel.send(Packet(Kind.BUTTON, 8, (0, 0)))
        release.set()
        expected = [(Kind.MOVE, (20, 100))]
        if barrier:
            expected = [(Kind.MOVE, (10, 100)), (Kind.BUTTON, (0, 1)), *expected, (Kind.BUTTON, (0, 0))]
        decoder, received = Decoder(), []
        while len(received) < len(expected):
            received.extend(decoder.feed(b.recv(8192)))
        assert [(p.kind, p.values) for p in received] == expected
        until(lambda: channel.sent >= len(expected))
        assert channel.sent == len(expected)
        assert channel.outbox.coalesced == (18 if barrier else 19)
    finally:
        release.set()
        channel.close()
        b.close()


@pytest.mark.parametrize("retry", [ssl.SSLWantWriteError, BlockingIOError])
def test_new_motion_cannot_replace_a_write_already_submitted_to_tls(retry):
    a, b = socket.socketpair()
    b.settimeout(1)
    attempts = []

    class Retrying:
        def __getattr__(self, name):
            return getattr(a, name)

        def send(self, data):
            attempts.append(bytes(data))
            if len(attempts) == 1:
                channel.send(Packet(Kind.MOVE, 4, (2, 20)))
                raise retry()
            return a.send(data)

    channel = Channel(Retrying(), lambda packet: None, lambda: None)
    try:
        channel.send(Packet(Kind.MOVE, 4, (1, 20)))
        channel.start()
        decoder, received = Decoder(), []
        while len(received) < 2:
            received.extend(decoder.feed(b.recv(8192)))
        assert attempts[0] == attempts[1]
        assert [p.values for p in received] == [(1, 20), (2, 20)]
        until(lambda: channel.sent == 2)
        assert channel.failure is None
    finally:
        channel.close()
        b.close()
