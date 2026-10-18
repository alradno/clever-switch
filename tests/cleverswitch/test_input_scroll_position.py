"""Scroll hit testing after asynchronous native posts, using fake OS APIs only."""

import ctypes as c

import pytest

from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_device_continuity import busy_session
from .test_input_end_to_end import enter, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import wheel
from .test_input_native import event
from .test_input_native import mac as mac
from .test_input_stream import until


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("axis", [0, 1], ids=["horizontal", "vertical"])
@pytest.mark.parametrize("count", [1, 24], ids=["single", "burst"])
@pytest.mark.parametrize("busy", [False, True], ids=["immediate", "deferred"])
def test_scroll_targets_each_ordered_position_before_native_queue_is_applied(
    native_peers, monkeypatch, source, axis, count, busy,
):
    peers, windows_api, mac_api = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    # Keep the simulated OS cursor at its previous position until all native
    # posts arrive. CGEventPost returns before WindowServer applies a move;
    # wheel hit testing uses the location carried by that individual event.
    queued = []
    if receiver.role == "mac":
        mac_api.position = (900, 100)
        monkeypatch.setattr(mac_api, "CGEventPost", lambda tap, value: queued.append(value.copy()))

        def scrolls():
            return [entry for entry in queued if entry["kind"] == mac_api.kCGEventScrollWheel]

    else:
        first = len(windows_api.inputs)

        def scrolls():
            return [entry for entry in windows_api.inputs[first:] if entry.kind == 0 and entry.mouse.flags & 0x1800]

    anchor_x, anchor_y = sender.backend.anchor
    expected = []
    previous = sender.remote_position
    with busy_session(sender, busy):
        for index in range(count):
            point = (300 + index * 5 if source == 0 else 1500 - index * 5, 300 + index * 3)
            # Physical reports are deltas from the last report. The routing
            # worker's position may still lag behind callbacks in its queue.
            motion(sender, int(anchor_x + point[0] - previous[0]), int(anchor_y + point[1] - previous[1]))
            previous = point
            amount = 0.25 if index % 2 == 0 else -0.5
            wheel(sender, amount if axis == 0 else 0, amount if axis == 1 else 0)
            expected.append((point, amount))
        if busy:
            assert not sender.deferred.empty()
    until(lambda: len(scrolls()) == count)
    if receiver.role == "mac":
        field = (mac_api.kCGScrollWheelEventFixedPtDeltaAxis2, mac_api.kCGScrollWheelEventFixedPtDeltaAxis1)[axis]
        observed = [(entry["position"], entry[field] * (-1 if axis == 0 else 1)) for entry in scrolls()]
        assert mac_api.position == (900, 100)
    else:
        # SendInput applies move/wheel records in order. Decode its absolute
        # coordinates independently instead of reading backend.previous.
        observed, position = [], (900, 100)
        desktop = receiver.local
        width, height = desktop.right - desktop.left + 1, desktop.bottom - desktop.top + 1
        for entry in windows_api.inputs[first:]:
            if entry.kind != 0:
                continue
            if entry.mouse.flags & 1:
                position = (
                    desktop.left + entry.mouse.x * width // 65536,
                    desktop.top + entry.mouse.y * height // 65536,
                )
            if entry.mouse.flags & 0x1800:
                observed.append((position, c.c_int32(entry.mouse.data).value / 120))
    assert observed == expected
    assert sender.session.phase == "sending" and receiver.session.phase == "receiving"
    assert all(peer._available() for peer in peers)


@pytest.mark.parametrize("point", [(0, 0), (1200, 700), (-50.25, -120.5), (1800.75, 900.25)])
def test_mac_scroll_keeps_fractional_and_negative_ordered_coordinates(mac, monkeypatch, point):
    backend, api = mac
    queued = []
    monkeypatch.setattr(api, "CGEventPost", lambda tap, value: queued.append(value.copy()))
    backend.inject(event(Kind.MOVE, *point))
    backend.inject(event(Kind.WHEEL, 0.25, -0.5))
    assert queued[-1]["position"] == point
    assert api.position != point
    assert queued[-1][93] == -0.5 and queued[-1][94] == -0.25


def test_mac_wheel_without_an_ordered_move_preserves_the_native_location(mac):
    backend, api = mac
    backend.inject(event(Kind.WHEEL, 0, 1))
    assert api.events[-1]["position"] == (500, 300)


def test_mac_scroll_event_creation_failure_never_posts_invalid_input(mac, monkeypatch):
    backend, api = mac
    backend.inject(event(Kind.MOVE, 100, 200))
    before = len(api.events)
    monkeypatch.setattr(api, "CGEventCreateScrollWheelEvent", lambda *args: None)
    with pytest.raises(PermissionError):
        backend.inject(event(Kind.WHEEL, 0, 1))
    assert len(api.events) == before
