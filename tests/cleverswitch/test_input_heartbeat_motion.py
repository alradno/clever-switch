"""Link heartbeats must not replay obsolete pointer positions after a stall."""

import socket
import threading

import pytest

from cleverswitch.flow.input_protocol import Decoder, Kind, Outbox, Packet, Routed
from cleverswitch.flow.input_stream import Channel

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_stream import until


def move(x, *, routed=False, epoch=9, action=0):
    packet = Packet(Kind.MOVE, epoch, (x, 200))
    return Routed(packet, action, int(x), packet.values) if routed else packet


@pytest.mark.parametrize("routed", [False, True], ids=["tls", "hybrid_fallback"])
@pytest.mark.parametrize("kind", [Kind.PING, Kind.PONG])
def test_outbox_keeps_newest_motion_across_link_heartbeats(routed, kind):
    outbox = Outbox(4)
    first, latest = move(100, routed=routed), move(300, routed=routed)
    heartbeat = Packet(kind, 123)
    assert outbox.put(first)
    assert not outbox.put(heartbeat)
    assert not outbox.put(latest)
    assert outbox.take() == [latest, heartbeat]
    assert outbox.coalesced == 1


@pytest.mark.parametrize("routed", [False, True], ids=["tls", "hybrid_fallback"])
@pytest.mark.parametrize("kind", [Kind.PING, Kind.PONG])
def test_receiver_does_not_inject_stale_motion_separated_by_heartbeats(routed, kind):
    a, b = socket.socketpair()
    a.setblocking(False)
    channel = Channel(a, lambda packet: None, lambda: None)
    packets = [move(100, routed=routed), Packet(kind, 123), move(300, routed=routed)]
    try:
        b.sendall(b"".join(packet.encode(i) for i, packet in enumerate(packets, 1)))
        decoded, needs_write = channel._read_packets(Decoder(routed=routed))
        assert decoded == [packets[2], packets[1]]
        assert not needs_write and channel.received_coalesced == 1
    finally:
        channel.close()
        b.close()


@pytest.mark.parametrize("barrier", [
    Packet(Kind.KEY, 9, (0xE1, 1)), Packet(Kind.BUTTON, 9, (0, 1)), Packet(Kind.WHEEL, 9, (0, 1)),
    Packet(Kind.LOCKS, 9, (1, 1)), Packet(Kind.LEAVE, 9), Packet(Kind.RESET, 9),
    Packet(Kind.ENTER, 10, (100, 200)), Packet(Kind.ACCEPT, 9),
])
def test_heartbeat_selection_preserves_action_and_focus_boundaries(barrier):
    packets = [move(100), Packet(Kind.PING, 1), barrier, Packet(Kind.PONG, 2), move(300)]
    outbox = Outbox()
    for packet in packets:
        outbox.put(packet)
    assert outbox.take() == packets
    assert outbox.coalesced == 0


@pytest.mark.parametrize("latest", [move(300, epoch=10), move(300, routed=True), move(300, routed=True, action=1)])
def test_different_focus_or_transport_barrier_is_never_coalesced(latest):
    first = move(100, routed=isinstance(latest, Routed) and latest.action == 1)
    packets = [first, Packet(Kind.PING, 1), latest]
    outbox = Outbox()
    for packet in packets:
        outbox.put(packet)
    assert outbox.take() == packets


def test_full_outbox_can_replace_motion_without_losing_heartbeats():
    outbox = Outbox(4)
    outbox.put(move(100))
    controls = [Packet(Kind.PING if i % 2 else Kind.PONG, i) for i in range(1, 4)]
    for packet in controls:
        outbox.put(packet)
    outbox.put(move(300))
    assert outbox.take() == [move(300), *controls]
    assert outbox.coalesced == 1


def test_older_reliable_checkpoint_cannot_replace_newer_unsent_motion():
    outbox = Outbox()
    latest, previous = move(300, routed=True), move(100, routed=True)
    heartbeat = Packet(Kind.PING, 1)
    for packet in (latest, heartbeat, previous):
        outbox.put(packet)
    assert outbox.take() == [latest, heartbeat]


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("blocked", ["sender", "receiver"])
def test_stalled_pointer_with_idle_keyboard_resumes_at_latest_position(native_peers, source, blocked):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    if sender.channel.lane is not None:
        # Exercise the negotiated reliable fallback as well as legacy TLS.
        until(lambda: receiver.channel.lane.receiver.action == 1)
        sender.channel.lane.sender.mode = "reliable"
    observed = []
    original_inject = receiver.backend.inject

    def inject(packet):
        original_inject(packet)
        if packet.kind == Kind.MOVE:
            observed.append(packet.values)

    receiver.backend.inject = inject
    channel = (sender if blocked == "sender" else receiver).channel
    original_tick = channel.on_tick
    waiting, release = threading.Event(), threading.Event()

    def tick():
        if not waiting.is_set():
            waiting.set()
            assert release.wait(1)
        original_tick()

    channel.on_tick = tick
    channel._wake()
    try:
        assert waiting.wait(1)
        x, y = sender.backend.anchor
        direction = 1 if source == 0 else -1
        for i in range(20):
            motion(sender, int(x + direction), int(y))
            sender.channel.send(Packet(Kind.PING, 900 + i))
        latest = sender.remote_position
        if blocked == "receiver":
            until(lambda: not sender.channel.outbox.pending() and not sender.channel.wirebox.pending())
        release.set()
        until(lambda: observed and observed[-1] == latest)
        assert observed == [latest]
        assert sender._available() and receiver._available()
        assert sender.session.phase == "sending" and receiver.session.phase == "receiving"
    finally:
        release.set()
        channel.on_tick = original_tick
        receiver.backend.inject = original_inject
