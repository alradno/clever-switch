"""Input allowed locally during capture preparation must not be sent again.

Native APIs are simulated. A busy local session puts the crossing on the TLS
worker, where physical callbacks can arrive while capture is being prepared.
"""

import ctypes as c
import threading

import pytest

from cleverswitch.flow.input_protocol import Kind
from cleverswitch.flow.input_windows import MouseHook, Point

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button
from .test_input_stream import until


def physical(peer, action):
    edge = 1919 if peer.role == "windows" else 0
    point = (edge, 400 if action == "stationary" else 700)
    if action == "key":
        return key(peer, 4, 1)
    if action in ("motion", "stationary"):
        return motion(peer, *point)
    if peer.role == "windows":
        raw = MouseHook(Point(*point), 120 << 16 if action == "wheel" else 0, 0, 0, 0)
        return peer.backend._mouse(0, 0x20A if action == "wheel" else 0x201, c.addressof(raw))
    q = peer.backend.q
    raw = {"position": point, q.kCGScrollWheelEventFixedPtDeltaAxis1: 1}
    kind = q.kCGEventScrollWheel if action == "wheel" else q.kCGEventLeftMouseDown
    return peer.backend._callback(None, kind, raw, None)


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("action", ["key", "button", "wheel", "motion", "stationary"])
@pytest.mark.parametrize("stage", ["before_capture", "after_capture"])
def test_local_input_during_outgoing_capture_keeps_its_original_destination(native_peers, source, action, stage):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    channels = [peer.channel for peer in peers]
    held, unlock = threading.Event(), threading.Event()
    preparing, resume = threading.Event(), threading.Event()
    capture = sender.backend.capture
    injected, inject = [], receiver.backend.inject

    def record(packet):
        injected.append(packet)
        return inject(packet)

    receiver.backend.inject = record

    def pause_capture(enabled):
        if not enabled:
            return capture(False)
        if stage == "after_capture":
            capture(True)
        preparing.set()
        assert resume.wait(1)
        if stage == "before_capture":
            capture(True)

    sender.backend.capture = pause_capture

    def hold_session():
        with sender.session.lock:
            held.set()
            assert unlock.wait(1)

    blocker = threading.Thread(target=hold_session)
    blocker.start()
    try:
        assert held.wait(1)
        edge = 1919 if source == 0 else 0
        motion(sender, edge + (-100 if source == 0 else 100), 400)
        motion(sender, edge, 400)
        unlock.set()
        blocker.join(1)
        assert preparing.wait(1)
        assert sender.session.epoch and sender.session.phase == "local"
        assert sender.backend.captured == (stage == "after_capture")
        result = physical(sender, action)
        swallowed = result == 1 if source == 0 else result is None
        assert swallowed == (stage == "after_capture")
        assert sender.deferred.qsize() == 1
    finally:
        unlock.set()
        resume.set()
        blocker.join(1)

    locally_delivered = stage == "before_capture" and action != "stationary"
    until(lambda: sender.deferred.empty() and not sender.draining.is_set())
    if locally_delivered:
        until(lambda: all(peer.session.phase == "local" for peer in peers), timeout=0.5)
        assert sender.session.failure == "physical"
        assert not any(packet.kind in (Kind.KEY, Kind.BUTTON, Kind.WHEEL) for packet in injected)
        assert not sender.backend.captured and not sender.backend.hidden
        if action in ("button", "wheel", "motion"):
            assert sender.backend.previous == (edge, 700)
    else:
        until(lambda: sender.session.phase == "sending" and sender.deferred.empty())
        assert sender.backend.captured
        if action in ("key", "button", "wheel"):
            kind = {"key": Kind.KEY, "button": Kind.BUTTON, "wheel": Kind.WHEEL}[action]
            until(lambda: any(packet.kind == kind for packet in injected))
            assert sum(packet.kind == kind for packet in injected) == 1
    sender.backend.capture = capture
    if action == "key":
        key(sender, 4, 0)
    elif action == "button":
        button(sender, 0, 0)
    if not locally_delivered:
        sender.session.leave()
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert all(not peer.keys and not peer.buttons for peer in peers)
    assert all(not peer.session.keys and not peer.session.buttons for peer in peers)
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))
    enter(sender)
    key(sender, 5, 1)
    until(lambda: receiver.session.keys == {5})
    key(sender, 5, 0)
    until(lambda: not receiver.session.keys)
