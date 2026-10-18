"""Pointer relocation between reports, with fake native APIs and loopback only."""

import ctypes as c

import pytest

from cleverswitch.flow.input_keys import EVENT_TAG
from cleverswitch.flow.input_windows import MouseHook, Point

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_device_continuity import busy_session
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_native import mac as mac
from .test_input_native import win as win
from .test_input_stream import until


def pointer_action(backend, action, point, tag=0):
    """A click/scroll reports where the cursor is, without a preceding move."""
    results = []
    if hasattr(backend, "api"):
        if action == "wheel":
            messages = ((0x20A, 120 << 16), (0x20E, 120 << 16))
        elif action == "extra":
            messages = ((0x20B, 2 << 16), (0x20C, 2 << 16))
        else:
            messages = ((0x201, 0), (0x202, 0))
        for kind, data in messages:
            raw = MouseHook(Point(*point), data, 0, 0, tag)
            results.append(backend._mouse(0, kind, c.addressof(raw)))
    else:
        q = backend.q
        if action == "wheel":
            kinds = (q.kCGEventScrollWheel,)
        elif action == "extra":
            kinds = (q.kCGEventOtherMouseDown, q.kCGEventOtherMouseUp)
        else:
            kinds = (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp)
        raw = {
            "position": point,
            q.kCGMouseEventButtonNumber: 4 if action == "extra" else 0,
            q.kCGMouseEventClickState: 1,
            q.kCGScrollWheelEventFixedPtDeltaAxis1: 1,
            q.kCGScrollWheelEventFixedPtDeltaAxis2: -1,
            q.kCGEventSourceUserData: tag,
        }
        results.extend(backend._callback(None, kind, raw, None) is None for kind in kinds)
    return results


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("action", ["button", "extra", "wheel"])
@pytest.mark.parametrize("busy", [False, True], ids=["immediate", "deferred"])
def test_local_click_or_scroll_after_relocation_does_not_invent_an_edge_crossing(
    native_peers, source, action, busy,
):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    channels = [peer.channel for peer in peers]
    edge = 1919 if source == 0 else 0
    interior = edge + (-100 if source == 0 else 100)
    motion(sender, interior, 400)
    assert sender.armed
    # Native cursor placement can produce no motion event. The next click or
    # wheel carries the new position; a following vertical move must not use
    # the old interior point and invent a horizontal displacement to the edge.
    with busy_session(sender, busy):
        assert not any(pointer_action(sender.backend, action, (edge, 500)))
        motion(sender, edge, 501)
        if busy:
            assert not sender.deferred.empty()
    until(lambda: sender.deferred.empty() and not sender.draining.is_set())
    assert sender.session.phase == receiver.session.phase == "local"
    assert not sender.backend.captured and not sender.keys and not sender.buttons
    assert sender.backend.previous == (edge, 501)
    # Retaining local control must not disable a subsequent intentional cross.
    enter(sender)
    key(sender, 4, 1)
    until(lambda: receiver.session.keys == {4})
    key(sender, 4, 0)
    until(lambda: not receiver.session.keys)
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))


@pytest.mark.parametrize("platform", ["win", "mac"])
@pytest.mark.parametrize("action", ["button", "extra", "wheel"])
def test_tagged_pointer_actions_cannot_replace_the_physical_motion_baseline(request, platform, action):
    backend, _ = request.getfixturevalue(platform)
    backend.previous = (500, 300)
    pointer_action(backend, action, (10, 20), EVENT_TAG)
    assert backend.previous == (500, 300)
    assert not backend.held_buttons
    assert not backend.sink.motion.called and not backend.sink.button.called and not backend.sink.wheel.called
