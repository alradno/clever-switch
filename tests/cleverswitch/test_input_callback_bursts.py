"""Recover bounded native callback backlogs; fake OS APIs and real loopback."""

import json
import queue
import time
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_protocol import Kind
from cleverswitch.flow.input_service import Service
from cleverswitch.flow.input_session import Session

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_device_continuity import busy_session
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button, wheel
from .test_input_service import DesktopBackend
from .test_input_stream import until


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("count,pace,cost,clipped", [
    (320, 0, 0, False), (320, .001, 0, False),
    (1000, 0, .002, True), (40, .008, .002, False),
], ids=["burst", "1000hz", "long_path_and_slow_receiver", "125hz_control"])
def test_backlog_recovers_position_actions_and_next_crossing(native_peers, source, count, pace, cost, clipped):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    channels = [peer.channel for peer in peers]
    x, y = sender.backend.anchor
    direction = 1 if source == 0 else -1
    expected_position = sender.remote_position
    expected, observed, deliveries = [], [], []
    position = [expected_position]
    inject = receiver.backend.inject

    def record(packet):
        if cost:
            time.sleep(cost)
        inject(packet)
        if packet.kind == Kind.MOVE:
            position[0] = packet.values
            deliveries.append((packet.values, time.monotonic()))
        elif packet.kind in (Kind.KEY, Kind.BUTTON, Kind.WHEEL):
            observed.append((packet.kind, packet.values, position[0]))

    receiver.backend.inject = record
    with busy_session(sender, True):
        started = time.monotonic()
        for index in range(count):
            # The long trace reaches the far boundary then reverses. Summing
            # or replacing raw deltas would lose its clipping and final point.
            delta = direction * (4 if index < count * .8 else -2) if clipped else direction
            motion(sender, int(x + delta), int(y))
            expected_position = sender.remote.clamp(expected_position[0] + delta, expected_position[1])
            if index == count // 4 - 1:
                button(sender, 0, 1)
                expected.append((Kind.BUTTON, (0, 1), expected_position))
            if index == count // 2 - 1:
                key(sender, 4, 1)
                key(sender, 4, 0)
                expected.extend((Kind.KEY, (4, down), expected_position) for down in (1, 0))
            if index == count * 3 // 4 - 1:
                button(sender, 0, 0)
                expected.append((Kind.BUTTON, (0, 0), expected_position))
            if index == count * 3 // 4 + 1:
                wheel(sender, 0, 1)
                expected.append((Kind.WHEEL, (0, 1), expected_position))
            if pace:
                time.sleep(max(0, started + (index + 1) * pace - time.monotonic()))
        elapsed = time.monotonic() - started
        backlog = sender.deferred.qsize()
        assert not sender.channel.stop.is_set()
        if count > 256:
            assert backlog > 256
        resumed = time.monotonic()
    until(lambda: position[0] == expected_position and len(observed) == len(expected))
    assert observed == expected
    assert not receiver.session.buttons and not receiver.session.keys
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))
    recovery = deliveries[-1][1] - resumed
    receiver.backend.inject = inject
    # Exercise releases after recovery and another crossing on the same link.
    button(sender, 0, 1)
    key(sender, 0xE1, 1)
    until(lambda: receiver.session.buttons == {0} and receiver.session.keys == {0xE1})
    assert sender.session.leave()
    until(lambda: receiver.session.phase == "local")
    button(sender, 0, 0)
    key(sender, 0xE1, 0)
    enter(sender)
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))
    assert not receiver.session.buttons and not receiver.session.keys
    print(json.dumps(dict(direction=sender.role + "_to_" + receiver.role,
                          transport="hybrid" if sender.channel.lane else "TLS", reports=count,
                          requested_rate_hz=1 / pace if pace else None, injection_cost_ms=cost * 1000,
                          path_clipped_and_reversed=clipped, source_pause_ms=round(elapsed * 1000, 3),
                          queued_callbacks=backlog, verified_actions=len(expected),
                          resume_to_final_fake_injection_ms=round(recovery * 1000, 3),
                          native_input=False)))


@pytest.mark.parametrize("role", ["windows", "mac"])
def test_full_drain_batch_rearms_consumed_wake_and_yields_before_more_work(role):
    backend = DesktopBackend()
    service = Service(Mock(config={"role": role}), backend, enabled=True)
    service.session = Session(role, backend.desktop, backend, lambda packet: None)
    service.session.phase, service.session.epoch = "sending", 1
    service.channel = Mock()
    # Isolate the continuation rule from the capacity change, including when
    # running this exact test with the older Service implementation.
    service.deferred = queue.Queue(maxsize=1024)
    observed = []

    def motion(peer, sequence):
        observed.append(sequence)

    for sequence in range(600):
        service.deferred.put((motion, (sequence,), service.session, 1, service.session.local_generation, True,
                              time.perf_counter()))
    service.channel._wake.reset_mock()  # Simulate that select consumed producer wakes.
    service._drain()
    assert 0 < len(observed) < 600  # Leave a turn for socket traffic, never drain without a bound.
    service.channel._wake.assert_called_once()
    while not service.deferred.empty():
        service._drain()
    assert observed == list(range(600))
    wakes = service.channel._wake.call_count
    service._drain()
    assert service.channel._wake.call_count == wakes
    assert not service.draining.is_set()


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("action", ["key", "button", "motion"])
def test_large_local_backlog_preserves_priority_after_queued_edge(native_peers, source, action):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    channels = [peer.channel for peer in peers]
    edge, direction = (1919, 1) if source == 0 else (0, -1)
    received = []
    inject = receiver.backend.inject

    def record(packet):
        inject(packet)
        if packet.kind in (Kind.KEY, Kind.BUTTON):
            received.append(packet)

    receiver.backend.inject = record
    with busy_session(sender, True):
        for index in range(300):
            motion(sender, edge - direction * (100 + index % 2), 400)
        motion(sender, edge, 400)
        # These reports have already reached local applications. If the queued
        # edge later enables capture, replay must still respect their ownership.
        if action == "key":
            key(sender, 4, 1)
            key(sender, 4, 0)
        elif action == "button":
            button(sender, 0, 1)
            button(sender, 0, 0)
        else:
            motion(sender, edge - direction * 20, 400)
        assert not sender.channel.stop.is_set()
    until(lambda: sender.deferred.empty() and not sender.draining.is_set()
          and all(peer.session.phase == "local" for peer in peers))
    assert not received
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))
    enter(sender)
    key(sender, 4, 1)
    key(sender, 4, 0)
    until(lambda: len(received) == 2)
    assert not receiver.session.keys and not receiver.session.buttons


@pytest.mark.parametrize("source", [0, 1])
def test_excessive_backlog_remains_bounded_and_releases_pressed_input(native_peers, source):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    button(sender, 0, 1)
    key(sender, 0xE1, 1)
    until(lambda: receiver.session.buttons == {0} and receiver.session.keys == {0xE1})
    x, y = sender.backend.anchor
    with busy_session(sender, True):
        for _ in range(sender.deferred.maxsize + 2):
            motion(sender, int(x + (1 if source == 0 else -1)), int(y))
        assert sender.deferred.qsize() <= sender.deferred.maxsize
        assert sender.channel.stop.is_set() and sender.channel.failure == "backpressure"
    until(lambda: all(peer.channel.finished.is_set() for peer in peers))
    assert all(not peer.session.buttons and not peer.session.keys for peer in peers)
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
    assert all(peer.deferred.empty() and not peer.cleanup_pending for peer in peers)
