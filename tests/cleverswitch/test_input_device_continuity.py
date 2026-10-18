"""Seeded reconnection bursts through native adapters with simulated OS input."""

import contextlib
import random
import threading

import pytest

from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button, wheel
from .test_input_stream import until


@contextlib.contextmanager
def busy_session(peer, enabled):
    held, release = threading.Event(), threading.Event()
    expired = threading.Event()

    def hold():
        with peer.session.lock:
            held.set()
            if not release.wait(2):
                expired.set()

    worker = threading.Thread(target=hold) if enabled else None
    try:
        if worker is not None:
            worker.start()
            assert held.wait(1)
        yield
    finally:
        release.set()
        if worker is not None:
            worker.join(1)
            assert not worker.is_alive() and not expired.is_set()


def device_change(peer, kind):
    peer.backend.device_monitor.notify(kind)
    if peer.role == "windows":
        # Join the real adapter's message-pump queue so this notification
        # precedes the next physical report, even while Service is deferred.
        peer.backend.dispatch.call(lambda: None)


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("busy", [False, True], ids=["immediate", "deferred"])
@pytest.mark.parametrize("seed", range(8))
def test_repeated_device_changes_preserve_action_order_position_and_next_crossing(native_peers, source, busy, seed):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    channels = [peer.channel for peer in peers]
    enter(sender)
    observed, expected = [], []
    inject = receiver.backend.inject
    position = [receiver.backend.previous]

    def record(packet):
        inject(packet)
        if packet.kind == Kind.MOVE:
            position[0] = packet.values
        elif packet.kind in (Kind.KEY, Kind.BUTTON, Kind.WHEEL):
            observed.append((packet.kind, packet.values, position[0]))

    receiver.backend.inject = record
    x, y = sender.backend.anchor
    point = (500, 400)
    start = sender.remote_position
    motion(sender, int(x + point[0] - start[0]), int(y + point[1] - start[1]))
    until(lambda: position[0] == point)
    held_keys, held_buttons = set(), set()
    randomizer = random.Random(seed)
    changed = set()
    for _ in range(6):
        with busy_session(sender, busy):
            for _ in range(16):
                choice = randomizer.randrange(8)
                if choice < 2:
                    dx, dy = randomizer.choice((-3, -1, 1, 3)), randomizer.choice((-2, 0, 2))
                    point = point[0] + dx, point[1] + dy
                    motion(sender, int(x + dx), int(y + dy))
                elif choice < 4:
                    usage = randomizer.choice((4, 5, 0xE1, 0xE4))
                    down = int(usage not in held_keys)
                    held_keys.add(usage) if down else held_keys.remove(usage)
                    expected.append((Kind.KEY, (usage, down), point))
                    key(sender, usage, down)
                elif choice == 4:
                    code = randomizer.randrange(5)
                    down = int(code not in held_buttons)
                    held_buttons.add(code) if down else held_buttons.remove(code)
                    expected.append((Kind.BUTTON, (code, down), point))
                    button(sender, code, down)
                elif choice == 5:
                    axis, amount = randomizer.randrange(2), randomizer.choice((-0.5, 0.25))
                    values = (amount, 0) if axis == 0 else (0, amount)
                    expected.append((Kind.WHEEL, values, point))
                    wheel(sender, *values)
                else:
                    kind, held = ("keyboard", held_keys) if choice == 6 else ("mouse", held_buttons)
                    packet_kind = Kind.KEY if kind == "keyboard" else Kind.BUTTON
                    expected.extend((packet_kind, (code, 0), point) for code in sorted(held))
                    held.clear()
                    device_change(sender, kind)
                    changed.add(kind)
            if busy:
                assert not sender.deferred.empty()
        until(lambda: sender.deferred.empty() and not sender.draining.is_set())
        until(lambda: len(observed) >= len(expected))
        assert observed == expected
        assert sender.keys == sender.backend.held_keys == receiver.session.keys == held_keys
        assert sender.buttons == sender.backend.held_buttons == receiver.session.buttons == held_buttons
        assert sender.session.phase == "sending" and receiver.session.phase == "receiving"
        assert sender.backend.captured
    assert changed == {"keyboard", "mouse"}
    for kind in ("keyboard", "mouse"):
        device_change(sender, kind)
    until(lambda: not receiver.session.keys and not receiver.session.buttons)
    sender.session.leave()
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))
    enter(sender)
    key(sender, 4, 1)
    until(lambda: receiver.session.keys == {4})
    key(sender, 4, 0)
    until(lambda: not receiver.session.keys)
