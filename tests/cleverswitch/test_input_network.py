"""Input over real TLS and a local TCP relay with delay or one-way outage.

Only encrypted test bytes pass through the relay. Input backends stay in memory;
the tests do not alter network settings, install hooks or inject OS events.
"""

import socket
import threading

import pytest

from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_stream import connect, until
from .test_input_stream import endpoints as endpoints


class Relay:
    def __init__(self, target, delay=0.012):
        self.target, self.delay = target, delay
        self.stop = threading.Event()
        self.resume = [threading.Event(), threading.Event()]
        self.blocked = [threading.Event(), threading.Event()]
        for event in self.resume:
            event.set()
        self.connections = []
        self.pumps = []
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.port = self.listener.getsockname()[1]
        self.worker = threading.Thread(target=self._run)
        self.worker.start()

    def _run(self):
        try:
            client, _ = self.listener.accept()
            self.connections.append(client)
            server = socket.create_connection(("127.0.0.1", self.target), timeout=1)
            self.connections.append(server)
            for connection in self.connections:
                connection.settimeout(None)
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            for direction, source, destination in ((0, client, server), (1, server, client)):
                pump = threading.Thread(target=self._forward, args=(direction, source, destination))
                self.pumps.append(pump)
                pump.start()
            for pump in self.pumps:
                pump.join()
        except OSError:
            pass

    def _forward(self, direction, source, destination):
        try:
            while not self.stop.is_set():
                data = source.recv(16384)
                if not data or self.stop.wait(self.delay):
                    return
                if not self.resume[direction].is_set():
                    self.blocked[direction].set()
                self.resume[direction].wait()
                if self.stop.is_set():
                    return
                destination.sendall(data)
        except OSError:
            pass
        finally:
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def close(self):
        self.stop.set()
        for event in self.resume:
            event.set()
        self.listener.close()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.worker.join(3)
        assert not self.worker.is_alive()
        assert all(not pump.is_alive() for pump in self.pumps)


@pytest.fixture
def relayed(endpoints):
    relay = Relay(endpoints[0].link.port)
    endpoints[0].link.port = relay.port
    try:
        connect(endpoints)
        yield endpoints, relay
    finally:
        relay.close()


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
def test_delayed_tls_keeps_actions_in_order_and_returns_focus(relayed, source):
    peers, _ = relayed
    sender, receiver = peers[source], peers[1 - source]
    assert sender.session.enter(100, 200)
    until(lambda: sender.session.phase == "sending")
    expected = []
    for _ in range(8):
        for x in range(10, 40):
            assert sender.session.input(Kind.MOVE, x, 50)
        for kind, code, down in ((Kind.KEY, 4, 1), (Kind.BUTTON, 0, 1), (Kind.BUTTON, 0, 0), (Kind.KEY, 4, 0)):
            assert sender.session.input(kind, code, down)
            expected.append((kind, (code, down)))
    assert sender.session.input(Kind.MOVE, 80, 90)
    until(lambda: receiver.backend.events[-1].kind == Kind.MOVE and receiver.backend.events[-1].values == (80, 90))
    actions = [(p.kind, p.values) for p in receiver.backend.events if p.kind in (Kind.KEY, Kind.BUTTON)]
    assert actions == expected
    for index, packet in enumerate(receiver.backend.events):
        if packet.kind == Kind.KEY and packet.values[1]:
            assert receiver.backend.events[index - 1].kind == Kind.MOVE
            assert receiver.backend.events[index - 1].values == (39, 50)
    assert sender.session.leave()
    until(lambda: receiver.session.phase == "local")
    assert not sender.backend.captured and not receiver.session.keys and not receiver.session.buttons
    assert all(not peer.channel.stop.is_set() for peer in peers)


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
def test_ready_destination_receives_first_motion_without_waiting_for_return_trip(relayed, source):
    peers, relay = relayed
    sender, receiver = peers[source], peers[1 - source]
    acknowledgement = 1 - source
    relay.resume[acknowledgement].clear()
    try:
        assert sender.session.enter(100, 200)
        until(lambda: receiver.session.phase == "receiving")
        assert relay.blocked[acknowledgement].wait(0.2)
        assert sender.session.input(Kind.MOVE, 120, 300)
        until(lambda: len(receiver.backend.events) == 2, timeout=0.15)
        assert receiver.backend.events[-1].values == (120, 300)
        events = [
            (Kind.KEY, (4, 1)),
            (Kind.BUTTON, (0, 1)),
            (Kind.BUTTON, (0, 0)),
            (Kind.KEY, (4, 0)),
            (Kind.MOVE, (140, 320)),
        ]
        for kind, values in events:
            assert sender.session.input(kind, *values)
        assert sender.session.phase == "requesting"  # Acceptance is still in transit.
    finally:
        relay.resume[acknowledgement].set()
    until(lambda: sender.session.phase == "sending")
    until(lambda: len(receiver.backend.events) == len(events) + 2)
    assert [(p.kind, p.values) for p in receiver.backend.events[2:]] == events
    sender.session.leave()
    until(lambda: receiver.session.phase == "local")


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
def test_lost_acceptance_retires_early_pointer_without_delivering_actions(relayed, source):
    peers, relay = relayed
    sender, receiver = peers[source], peers[1 - source]
    # Isolate the 400 ms focus deadline from the longer connection deadline.
    for peer in peers:
        peer.channel.timeout = 2
    acknowledgement = 1 - source
    relay.resume[acknowledgement].clear()
    try:
        assert sender.session.enter(100, 200)
        assert sender.session.input(Kind.MOVE, 120, 300)
        assert sender.session.input(Kind.KEY, 0xE1, 1)
        until(lambda: bool(receiver.backend.events) and receiver.backend.events[-1].values == (120, 300), timeout=0.2)
        until(lambda: sender.session.phase == receiver.session.phase == "local", timeout=1)
        assert sender.session.failure == "timeout"
        assert not sender.backend.captured and not receiver.session.keys
        assert not any(packet.kind == Kind.KEY for packet in receiver.backend.events)
    finally:
        relay.resume[acknowledgement].set()
    until(lambda: sender.channel.received >= 1)  # Late ACCEPT has been processed.
    assert sender.session.phase == "local" and not sender.backend.captured
    assert not receiver.session.keys


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
def test_cancelled_unaccepted_handoff_cannot_apply_a_late_click(relayed, source):
    peers, relay = relayed
    sender, receiver = peers[source], peers[1 - source]
    for peer in peers:
        peer.channel.timeout = 2
    relay.resume[source].clear()
    try:
        assert sender.session.enter(100, 200)
        assert sender.session.input(Kind.BUTTON, 0, 1)
        assert sender.session.input(Kind.BUTTON, 0, 0)
        assert relay.blocked[source].wait(0.2)
        until(lambda: sender.session.phase == "local", timeout=1)
        assert sender.session.failure == "timeout"
        assert not sender.backend.captured and not receiver.backend.events
    finally:
        relay.resume[source].set()
    until(lambda: bool(receiver.session.retired))  # Delayed ENTER and RESET consumed.
    assert receiver.session.phase == "local"
    assert not any(packet.kind == Kind.BUTTON for packet in receiver.backend.events)


@pytest.mark.parametrize("blocked_direction", [0, 1], ids=["outbound_lost", "return_lost"])
@pytest.mark.parametrize("source", [0, 1], ids=["windows_source", "mac_source"])
def test_one_way_outage_releases_both_ends_and_drops_late_input(relayed, blocked_direction, source):
    peers, relay = relayed
    sender, receiver = peers[source], peers[1 - source]
    assert sender.session.enter(100, 200)
    until(lambda: sender.session.phase == "sending")
    assert sender.session.input(Kind.KEY, 0xE1, 1)
    until(lambda: receiver.session.keys == {0xE1})
    relay.resume[blocked_direction].clear()
    assert sender.session.input(Kind.MOVE, 400, 500)
    assert relay.blocked[blocked_direction].wait(1)
    until(lambda: all(peer.channel.finished.is_set() for peer in peers))
    failures = {peer.channel.failure for peer in peers}
    # The first timeout closes TLS. With one direction still working, the
    # other endpoint can receive EOF before its own heartbeat expires.
    assert "timeout" in failures and failures <= {"timeout", "connection"}
    assert not sender.backend.captured and not receiver.session.keys
    assert receiver.backend.events[-1].kind == Kind.KEY
    assert receiver.backend.events[-1].values == (0xE1, 0)
    count = len(receiver.backend.events)
    relay.resume[blocked_direction].set()
    relay.worker.join(1)
    assert not relay.worker.is_alive()
    assert len(receiver.backend.events) == count
