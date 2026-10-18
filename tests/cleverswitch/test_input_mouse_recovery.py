"""Mouse disappearance and return through real TLS/UDP, with simulated OS APIs."""

import ctypes as c
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_service import Service
from cleverswitch.flow.input_session import Session
from cleverswitch.flow.input_windows import MouseHook, Point

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_service import DesktopBackend
from .test_input_stream import until


def button(peer, code, down):
    if peer.role == "windows":
        raw = MouseHook(Point(400, 300), (code - 2) << 16 if code >= 3 else 0, 0, 0, 0)
        message = (0x201, 0x204, 0x207, 0x20B, 0x20B)[code] + (0 if down else 1)
        return peer.backend._mouse(0, message, c.addressof(raw))
    q = peer.backend.q
    message = (q.kCGEventLeftMouseDown, q.kCGEventRightMouseDown, q.kCGEventOtherMouseDown)[min(code, 2)]
    return peer.backend._callback(None, message + (0 if down else 1), {q.kCGMouseEventButtonNumber: code}, None)


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("code", range(5))
def test_mouse_reconnection_releases_lost_buttonup_without_losing_keyboard_or_focus(native_peers, source, code):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    key(sender, 4, 1)
    button(sender, code, 1)
    until(lambda: receiver.session.keys == {4} and receiver.session.buttons == {code})
    sender.backend.device_monitor.notify("mouse")
    until(lambda: not receiver.session.buttons)
    assert not sender.buttons and not sender.backend.held_buttons
    assert sender.keys == sender.backend.held_keys == receiver.session.keys == {4}
    assert sender.session.phase == "sending" and sender.backend.captured
    button(sender, code, 1)
    until(lambda: receiver.session.buttons == {code})
    button(sender, code, 0)
    key(sender, 4, 0)
    until(lambda: not receiver.session.keys and not receiver.session.buttons)
    x, y = sender.backend.anchor
    motion(sender, int(x + (20 if source == 0 else -20)), int(y))
    assert sender.session.phase == "sending" and all(peer._available() for peer in peers)


@pytest.mark.parametrize("source", [0, 1])
def test_mouse_reconnection_locally_does_not_leave_crossing_inhibited(native_peers, source):
    peers, _, _ = native_peers
    sender = peers[source]
    button(sender, 0, 1)
    assert sender.buttons == {0}
    sender.backend.device_monitor.notify("mouse")
    until(lambda: not sender.buttons and not sender.backend.held_buttons)
    enter(sender)
    key(sender, 4, 1)
    until(lambda: peers[1 - source].session.keys == {4})
    key(sender, 4, 0)
    until(lambda: not peers[1 - source].session.keys)


@pytest.mark.parametrize("source", [0, 1])
def test_mouse_topology_at_receiver_does_not_release_remote_drag(native_peers, source):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    button(sender, 0, 1)
    until(lambda: receiver.session.buttons == {0})
    receiver.backend.device_monitor.notify("mouse")
    # Join the notification's owner thread before checking what it changed.
    if receiver.role == "windows":
        receiver.backend.dispatch.call(lambda: None)
    assert receiver.session.buttons == {0} and receiver.session.phase == "receiving"
    button(sender, 0, 0)
    until(lambda: not receiver.session.buttons)


@pytest.mark.parametrize("previous_epoch", [0, 10])
def test_queued_mouse_removal_clears_lost_button_even_after_focus_ends(previous_epoch):
    backend = DesktopBackend()
    service = Service(Mock(config={"role": "windows"}), backend)
    service.session = Session("windows", backend.desktop, backend, lambda packet: None)
    service.session.epoch = previous_epoch
    service.keys.add(4)
    service.draining.set()
    service.button(0, 1)
    service.mouse_changed()
    service.draining.clear()
    service.session.epoch = 0
    service._drain()
    assert not service.buttons and service.keys == {4}
    assert service.deferred.empty()
