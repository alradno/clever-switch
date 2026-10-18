"""Seeded mixed native reports over real TLS/UDP; OS input is simulated."""

import ctypes as c
import random
import time

import pytest

from cleverswitch.flow.input_protocol import Kind
from cleverswitch.flow.input_windows import MouseHook, Point

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_stream import until


def button(peer, index, down):
    if peer.role == "windows":
        message = ((0x201, 0x202), (0x204, 0x205), (0x207, 0x208), (0x20B, 0x20C), (0x20B, 0x20C))[index][not down]
        data = ((index - 2) << 16) if index >= 3 else 0
        raw = MouseHook(Point(900, 400), data, 0, 0, 0)
        return peer.backend._mouse(0, message, c.addressof(raw))
    q = peer.backend.q
    message = (
        (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp),
        (q.kCGEventRightMouseDown, q.kCGEventRightMouseUp),
        (q.kCGEventOtherMouseDown, q.kCGEventOtherMouseUp),
    )[min(index, 2)][not down]
    return peer.backend._callback(None, message, {q.kCGMouseEventButtonNumber: index}, None)


def wheel(peer, horizontal, vertical):
    if peer.role == "windows":
        for axis, value in enumerate((horizontal, vertical)):
            if value:
                raw = MouseHook(Point(900, 400), (int(value * 120) & 0xFFFF) << 16, 0, 0, 0)
                peer.backend._mouse(0, 0x20E if axis == 0 else 0x20A, c.addressof(raw))
    else:
        q = peer.backend.q
        peer.backend._callback(
            None,
            q.kCGEventScrollWheel,
            {q.kCGScrollWheelEventFixedPtDeltaAxis1: vertical, q.kCGScrollWheelEventFixedPtDeltaAxis2: -horizontal},
            None,
        )


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("delay", [0, 0.001])
@pytest.mark.parametrize("seed", range(8))
def test_mixed_native_reports(native_peers, source, delay, seed):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    observed, expected, position = [], [], [receiver.backend.previous]
    inject = receiver.backend.inject

    def traced(packet):
        if delay:
            time.sleep(delay)
        inject(packet)
        if packet.kind == Kind.MOVE:
            position[0] = packet.values
        else:
            observed.append((packet.kind, packet.values, position[0]))

    receiver.backend.inject = traced
    direction = 1 if source == 0 else -1
    x, y = sender.backend.anchor
    motion(sender, int(x + direction * 300), int(y))
    ideal = (301 if source == 0 else 1618, 400)
    randomizer, keys, buttons = random.Random(seed), set(), set()
    for i in range(300):
        choice = randomizer.randrange(10)
        if choice < 6:
            dx, dy = randomizer.choice((-2, -1, 1, 2)), randomizer.choice((-1, 0, 1))
            ideal = ideal[0] + dx, ideal[1] + dy
            motion(sender, int(x + dx), int(y + dy))
        elif choice < 8:
            code = randomizer.choice((4, 5, 0xE0, 0xE1, 0xE5))
            down = int(code not in keys)
            keys.add(code) if down else keys.remove(code)
            key(sender, code, down)
            expected.append((Kind.KEY, (code, down), ideal))
        elif choice == 8:
            code = randomizer.randrange(5)
            down = int(code not in buttons)
            buttons.add(code) if down else buttons.remove(code)
            button(sender, code, down)
            expected.append((Kind.BUTTON, (code, down), ideal))
        else:
            value = randomizer.choice((-1, -0.25, 0.25, 1))
            values = (value, 0) if randomizer.getrandbits(1) else (0, value)
            wheel(sender, *values)
            expected.append((Kind.WHEEL, values, ideal))
        if i % 8 == 0:
            time.sleep(0.001)
    for code in sorted(keys):
        key(sender, code, 0)
        expected.append((Kind.KEY, (code, 0), ideal))
    for code in sorted(buttons):
        button(sender, code, 0)
        expected.append((Kind.BUTTON, (code, 0), ideal))
    until(lambda: len(observed) >= len(expected))
    assert observed == expected
    assert not receiver.session.keys and not receiver.session.buttons
    assert sender.session.phase == "sending" and receiver.session.phase == "receiving"
    assert all(peer._available() for peer in peers)
