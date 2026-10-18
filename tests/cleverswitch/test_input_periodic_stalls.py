"""Repeated 140 ms stalls with an idle keyboard; native APIs are simulated."""

import json
import time
from itertools import pairwise

import pytest

from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_stream import until


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("rate", [125, 1000])
@pytest.mark.parametrize("blocked", ["sender", "receiver"])
def test_periodic_stalls_preserve_motion_link_and_subsequent_typing(native_peers, source, rate, blocked):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    channels = [peer.channel for peer in peers]
    channel = (sender if blocked == "sender" else receiver).channel
    tick, inject = channel.on_tick, receiver.backend.inject
    started = time.perf_counter()
    next_pause = started + .15
    pauses = 0
    observed, offered = [], {}

    def stalled_tick():
        nonlocal next_pause, pauses
        if pauses < 3 and time.perf_counter() >= next_pause:
            time.sleep(.14)
            pauses += 1
            next_pause += .52
        tick()

    def completed(packet):
        inject(packet)
        if packet.kind == Kind.MOVE:
            observed.append((packet.values, time.perf_counter()))

    channel.on_tick = stalled_tick
    receiver.backend.inject = completed
    direction = 1 if source == 0 else -1
    x, y = sender.backend.anchor
    first = sender.remote_position
    count = int(1.4 * rate)
    try:
        for index in range(count):
            wait = started + index / rate - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            point = first[0] + direction * (index + 1), first[1]
            offered[point] = time.perf_counter()
            motion(sender, int(x + direction), int(y))
        until(lambda: observed and observed[-1][0] == point)
        assert pauses == 3
        assert all(direction * (b[0][0] - a[0][0]) >= 0 for a, b in pairwise(observed))
        assert all(position in offered for position, _ in observed)
        assert all(peer.channel is original and peer._available()
                   for peer, original in zip(peers, channels, strict=True))
        assert sender.session.phase == "sending" and receiver.session.phase == "receiving"
        if sender.channel.lane:
            until(lambda: sender.channel.lane.sender.latest is None)
            assert sender.channel.lane.deadline is None
        latencies = sorted((stamp - offered[position]) * 1000 for position, stamp in observed)
        report = {
            "scope": "simulated_native_input_real_loopback; imposed_stalls_are_not_removed",
            "direction": sender.role + "_to_" + receiver.role,
            "hybrid": sender.channel.lane is not None,
            "report_rate_hz": rate,
            "blocked_stage": blocked,
            "pause_count": pauses, "requested_pause_ms": 140, "pause_interval_ms": 520,
            "offered_moves": count, "delivered_moves": len(observed),
            "completion_ms": {"p50": round(latencies[len(latencies) // 2], 3),
                              "p99": round(latencies[int(.99 * (len(latencies) - 1))], 3),
                              "maximum": round(latencies[-1], 3)},
        }
        # The movement-only workload cannot starve subsequent keyboard input.
        key(sender, 4, 1)
        until(lambda: receiver.session.keys == {4})
        key(sender, 4, 0)
        until(lambda: not receiver.session.keys)
        assert sender.session.leave()
        until(lambda: all(peer.session.phase == "local" for peer in peers))
        assert all(not peer.session.unreleased and not peer.backend.captured for peer in peers)
        print("periodic_stall_result=" + json.dumps(report, sort_keys=True))
    finally:
        channel.on_tick = tick
        receiver.backend.inject = inject
