"""Sustained native reports with slow receivers; OS input is always simulated.

Run this file with ``pytest -s --no-cov`` for aggregate completion timings.
Only the generated stream is measured; no physical or Wi-Fi latency is implied.
"""

import json
import time
from itertools import pairwise

import pytest

from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_device_continuity import device_change
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button, wheel
from .test_input_stream import until


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("reconnect", [False, True], ids=["continuous", "keyboard_reconnect"])
@pytest.mark.parametrize(
    "rate,delay,pause",
    [(125, 0, 0), (1000, 0, 0), (1000, 0.002, 0), (1000, 0.005, 0), (1000, 0.002, 0.04)],
    ids=["125hz", "1000hz", "1000hz_2ms_receiver", "1000hz_5ms_receiver", "1000hz_40ms_pause"],
)
def test_sustained_reports_preserve_actions_and_device_recovery(native_peers, source, reconnect, rate, delay, pause):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    channels = [peer.channel for peer in peers]
    enter(sender)
    observed, expected, offered = [], [], {}
    inject = receiver.backend.inject
    paused = False
    started = time.perf_counter()

    def completed(packet):
        nonlocal paused
        if pause and not paused and time.perf_counter() - started >= 0.2:
            paused = True
            time.sleep(pause)
        if delay:
            time.sleep(delay)
        inject(packet)
        # Completion must be stamped after the simulated cost AND the real
        # adapter's call to the fake API, not on entry into the injector.
        observed.append((packet.kind, packet.values, receiver.backend.previous, time.perf_counter()))

    receiver.backend.inject = completed
    direction = 1 if source == 0 else -1
    x, y = sender.backend.anchor
    first = sender.remote_position
    count, group = int(rate * 0.6), rate // 5
    peak, late, changes = 0, 0, 0
    for index in range(count):
        wait = started + index / rate - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        elif wait < -1 / rate:
            late += 1
        point = (first[0] + direction * (index + 1), first[1])
        offered[point] = time.perf_counter()
        motion(sender, int(x + direction), int(y))
        action = index % group
        if action == 0:
            key(sender, 4, 1)
            expected.append((Kind.KEY, (4, 1), point))
        elif action == 1:
            button(sender, 0, 1)
            expected.append((Kind.BUTTON, (0, 1), point))
        elif action == 2:
            if reconnect:
                # A channel change loses key-up while the pointer keeps moving
                # and a mouse button is held. Reconnect must release only keys.
                device_change(sender, "keyboard")
                expected.append((Kind.KEY, (4, 0), point))
                changes += 1
            wheel(sender, 0, 1)
            expected.append((Kind.WHEEL, (0, 1), point))
        elif action == 3:
            button(sender, 0, 0)
            expected.append((Kind.BUTTON, (0, 0), point))
        elif action == 4:
            if reconnect:
                key(sender, 4, 1)
                expected.append((Kind.KEY, (4, 1), point))
            key(sender, 4, 0)
            expected.append((Kind.KEY, (4, 0), point))
        peak = max(peak, len(sender.channel.outbox.items))

    until(lambda: observed and observed[-1][2] == point)
    assert [(kind, values, position) for kind, values, position, _ in observed if kind != Kind.MOVE] == expected
    assert not sender.keys and not sender.buttons
    assert not receiver.session.keys and not receiver.session.buttons
    assert sender.session.phase == "sending" and receiver.session.phase == "receiving"
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))
    assert paused == bool(pause)
    assert changes == (3 if reconnect else 0)
    moves = [(values, stamp) for kind, values, _, stamp in observed if kind == Kind.MOVE]
    assert moves and moves[-1][0] == point
    assert all(direction * (b[0][0] - a[0][0]) >= 0 for a, b in pairwise(moves))
    assert all(values in offered for values, _ in moves)
    latencies = sorted((stamp - offered[values]) * 1000 for values, stamp in moves)
    assert latencies[0] >= delay * 1000
    report = {
        "scope": "native_adapters_fake_OS_real_loopback_completion",
        "direction": sender.role + "_to_" + receiver.role,
        "hybrid": sender.channel.lane is not None,
        "rate_hz": rate,
        "receiver_cost_ms": delay * 1000,
        "receiver_pause_ms": pause * 1000,
        "keyboard_reconnects": changes,
        "offered_moves": count,
        "completed_moves": len(moves),
        "verified_actions": len(expected),
        "sampled_peak_outbox": peak,
        "scheduler_late": late,
        "completion_ms": {
            "p50": round(latencies[len(latencies) // 2], 3),
            "p99": round(latencies[int(0.99 * (len(latencies) - 1))], 3),
            "max": round(latencies[-1], 3),
        },
    }
    # Finish with live input so leave must actually release it. Afterwards the
    # same authenticated connection must support a fresh crossing and typing.
    key(sender, 0xE1, 1)
    button(sender, 0, 1)
    until(lambda: receiver.session.keys == {0xE1} and receiver.session.buttons == {0})
    assert sender.session.leave()
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert not receiver.session.keys and not receiver.session.buttons
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
    key(sender, 0xE1, 0)
    button(sender, 0, 0)
    receiver.backend.inject = inject
    enter(sender)
    key(sender, 5, 1)
    until(lambda: receiver.session.keys == {5})
    key(sender, 5, 0)
    until(lambda: not receiver.session.keys)
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))
    assert sender.session.leave()
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert all(not peer.session.unreleased and not peer.session.pending_capture for peer in peers)
    print("sustained_result=" + json.dumps(report, sort_keys=True))
