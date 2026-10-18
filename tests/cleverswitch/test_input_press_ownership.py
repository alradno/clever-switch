"""A physical press passed locally must keep its release through capture.

Public-API adapters use fake OS surfaces; only TLS/UDP loopback is real.
"""

import threading
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_protocol import Kind
from cleverswitch.flow.input_service import Service
from cleverswitch.flow.input_session import Session

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button
from .test_input_service import DesktopBackend
from .test_input_stream import until


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("kind,code", [(Kind.KEY, 4), (Kind.KEY, 0xE1), (Kind.BUTTON, 0), (Kind.BUTTON, 4)])
@pytest.mark.parametrize("repeat", [False, True])
def test_local_press_keeps_repeat_and_release_when_capture_starts_between_them(
    native_peers, source, kind, code, repeat,
):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    channels = [peer.channel for peer in peers]
    held, unlock = threading.Event(), threading.Event()
    before, proceed, after, resume = (threading.Event() for _ in range(4))
    capture = sender.backend.capture
    inject, delivered = receiver.backend.inject, []

    def observe(packet):
        inject(packet)
        if packet.kind in (Kind.KEY, Kind.BUTTON):
            delivered.append(packet)

    receiver.backend.inject = observe

    def pause_capture(enabled):
        if not enabled:
            return capture(False)
        before.set()
        assert proceed.wait(1)
        capture(True)
        after.set()
        assert resume.wait(1)

    sender.backend.capture = pause_capture

    def hold():
        with sender.session.lock:
            held.set()
            assert unlock.wait(1)

    worker = threading.Thread(target=hold)
    worker.start()
    local_down, local_events = set(), []

    def physical(down):
        result = key(sender, code, down) if kind == Kind.KEY else button(sender, code, down)
        swallowed = result == 1 if source == 0 else result is None
        if not swallowed:
            local_events.append(down)
            local_down.add(code) if down else local_down.discard(code)
        return swallowed

    try:
        assert held.wait(1)
        edge = 1919 if source == 0 else 0
        motion(sender, edge + (-100 if source == 0 else 100), 400)
        motion(sender, edge, 400)
        unlock.set()
        worker.join(1)
        assert before.wait(1)
        assert not physical(1)
        assert local_down == {code}
        proceed.set()
        assert after.wait(1)
        assert sender.backend.captured and sender.session.phase == "local"
        if repeat:
            assert not physical(1)
        assert not physical(0)
        assert not local_down
        assert local_events == ([1, 1, 0] if repeat else [1, 0])
        # New presses that began after capture still belong to the pending
        # remote focus. Passing its locally owned release must not open a gap.
        other_down = key(sender, 6, 1)
        other_up = key(sender, 6, 0)
        assert other_down == other_up == 1 if source == 0 else other_down is other_up is None
    finally:
        unlock.set()
        proceed.set()
        resume.set()
        worker.join(1)

    until(lambda: sender.deferred.empty() and not sender.draining.is_set())
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert not delivered
    assert all(not peer.keys and not peer.buttons for peer in peers)
    assert all(not peer.session.keys and not peer.session.buttons for peer in peers)
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))
    sender.backend.capture = capture
    enter(sender)
    key(sender, 5, 1)
    until(lambda: receiver.session.keys == {5})
    key(sender, 5, 0)
    until(lambda: not receiver.session.keys)


@pytest.mark.parametrize("role", ["windows", "mac"])
@pytest.mark.parametrize("kind,code,next_code", [("key", 4, 5), ("button", 0, 4)])
def test_device_change_clears_local_ownership_in_native_order_and_shutdown_retires_it(role, kind, code, next_code):
    backend = DesktopBackend()
    peer = Service(Mock(config={"role": role}), backend, enabled=True)
    peer.session = Session(role, backend.desktop, backend, lambda packet: None)
    callback = getattr(peer, kind)
    changed = peer.keyboard_changed if kind == "key" else peer.mouse_changed
    owner = peer.local_keys if kind == "key" else peer.local_buttons
    peer.draining.set()
    assert not callback(code, 1)
    assert owner == {code}
    changed()
    assert not owner
    assert not callback(next_code, 1)
    assert owner == {next_code}
    peer._drain()
    assert owner == {next_code}  # Replaying the old notice cannot erase a newer press.
    peer._stop_backend()
    assert not peer.local_keys and not peer.local_buttons


@pytest.mark.parametrize("kind,code", [("key", 4), ("button", 0)])
def test_queue_overflow_does_not_swallow_a_local_release(kind, code):
    backend = DesktopBackend()
    peer = Service(Mock(config={"role": "windows"}), backend, enabled=True)
    callback = getattr(peer, kind)
    assert not callback(code, 1)
    backend.captured = True
    peer.draining.set()
    for _ in range(peer.deferred.maxsize):
        peer.motion(900, 400, 1, 0)
    assert not callback(code, 0)
    assert not peer.local_keys and not peer.local_buttons


def test_already_held_local_keys_and_buttons_keep_their_ownership_after_preparation():
    backend = DesktopBackend()
    backend.held_keys, backend.held_buttons = {4, 0xE1}, {0, 4}
    peer = Service(Mock(config={"role": "windows"}), backend, enabled=True)
    peer._prepare()
    assert peer.local_keys == peer.keys == {4, 0xE1}
    assert peer.local_buttons == peer.buttons == {0, 4}
    assert peer.local_keys is not peer.keys and peer.local_buttons is not peer.buttons
    peer._stop_backend()
