"""Physical local activity racing a remote arrival; only simulated OS APIs."""

import ctypes as c
import threading

import pytest

from cleverswitch.flow.input_keys import MAC, WINDOWS
from cleverswitch.flow.input_windows import MouseHook, Point

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_stream import until


def activity(peer, kind, down=1):
    if kind == "key":
        return key(peer, 4, down)
    if kind in ("motion", "stationary"):
        return motion(peer, 950 if kind == "motion" else 900, 400)
    if peer.role == "windows":
        raw = MouseHook(Point(900, 400), 120 << 16 if kind == "wheel" else 0, 0, 0, 0)
        return peer.backend._mouse(0, 0x20A if kind == "wheel" else 0x201 if down else 0x202, c.addressof(raw))
    q = peer.backend.q
    event = {q.kCGScrollWheelEventFixedPtDeltaAxis1: 1} if kind == "wheel" else {}
    message = q.kCGEventScrollWheel if kind == "wheel" else q.kCGEventLeftMouseDown if down else q.kCGEventLeftMouseUp
    return peer.backend._callback(None, message, event, None)


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("kind", ["key", "button", "wheel", "motion", "stationary"])
def test_local_activity_during_arrival_keeps_priority_without_closing_link(native_peers, source, kind):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    motion(receiver, 900, 400)
    checked, release = threading.Event(), threading.Event()
    can_receive = receiver.session.can_receive

    def pause_after_local_check():
        allowed = can_receive()
        checked.set()
        assert release.wait(1)
        return allowed

    receiver.session.can_receive = pause_after_local_check
    edge = 1919 if source == 0 else 0
    motion(sender, edge + (-100 if source == 0 else 100), 400)
    try:
        motion(sender, edge, 400)
        assert checked.wait(1)
        assert receiver.session.epoch == 0
        activity(receiver, kind)
        assert receiver.deferred.qsize() == 1
        key(sender, 5, 1)
        key(sender, 5, 0)
    finally:
        release.set()
    receiver.session.can_receive = can_receive
    if kind == "stationary":
        until(lambda: sender.session.phase == "sending" and receiver.deferred.empty())
        assert receiver.session.phase == "receiving"
        sender.session.leave()
    else:
        until(lambda: all(peer.session.phase == "local" for peer in peers), timeout=0.5)
        assert receiver.session.failure == "physical"
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert all(peer._available() and not peer.backend.captured for peer in peers)
    if kind in ("key", "button"):
        assert receiver.keys == {4} if kind == "key" else receiver.buttons == {0}
        activity(receiver, kind, 0)
    assert all(not peer.keys and not peer.buttons for peer in peers)
    assert all(not peer.session.keys and not peer.session.buttons for peer in peers)
    if receiver.role == "windows":
        injected = [e for e in receiver.backend.api.inputs if e.kind == 1 and e.key.scan == WINDOWS[5]]
    else:
        injected = [e for e in receiver.backend.q.events if e.get(9) == MAC[5]]
    assert len(injected) == (2 if kind == "stationary" else 0)
    enter(sender)
    key(sender, 4, 1)
    until(lambda: receiver.session.keys == {4})
    key(sender, 4, 0)
    until(lambda: not receiver.session.keys)
