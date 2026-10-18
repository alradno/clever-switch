"""Native clock contracts; fake input APIs only, never hooks or injection."""

import ctypes as c
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_keys import EVENT_TAG
from cleverswitch.flow.input_protocol import Kind, Packet
from cleverswitch.flow.input_windows import MouseHook, Point

from .test_input_native import mac as mac
from .test_input_native import win as win


@pytest.mark.parametrize("created_stamp", [0, 5000000000])
def test_constructed_event_timestamp_is_not_the_native_clock(mac, created_stamp):
    backend, q = mac
    backend.clock = lambda: 1000.0
    backend.event_clock = lambda: 10000000000
    q.CGEventGetTimestamp = lambda event: created_stamp
    backend.inject(Packet(Kind.BUTTON, 1, (0, 1), 998750000))
    # A fresh CGEvent can have timestamp zero. Its age is relative to now,
    # not that field. Keep the receiver's local event clock independent.
    assert q.events[-1]["timestamp"] == 8750000000
    backend.inject(Packet(Kind.BUTTON, 1, (0, 0), 998800000))
    assert q.events[-1]["timestamp"] == 8800000000


@pytest.mark.parametrize("now,stamp", [(10000, 9880), (2**32 + 100, 2**32 - 20)])
def test_windows_capture_age_uses_modular_os_milliseconds(win, now, stamp):
    backend, _ = win
    backend.kernel.GetTickCount64.return_value = now
    event = MouseHook(Point(500, 400), 0, 0, stamp, 0)
    backend._mouse(0, 0x200, c.addressof(event))
    assert backend.capture_age.snapshot()["maximum_ms"] == 120
    backend.sink.motion.assert_called_once()
    backend.sink.fail.assert_not_called()


@pytest.mark.parametrize("stamp", [0, 10001])
def test_windows_missing_or_future_capture_time_does_not_reject_motion(win, stamp):
    backend, _ = win
    backend.kernel.GetTickCount64.return_value = 10000
    event = MouseHook(Point(500, 400), 0, 0, stamp, 0)
    backend._mouse(0, 0x200, c.addressof(event))
    assert backend.capture_age.snapshot()["samples"] == 0
    backend.sink.motion.assert_called_once()
    backend.sink.fail.assert_not_called()


@pytest.mark.parametrize("stamp", [None, 0, 10000000001, 9880000000])
def test_mac_capture_age_ignores_absent_or_future_times_and_preserves_motion(mac, stamp):
    backend, q = mac
    backend.event_clock = lambda: 10000000000
    event = {"position": (500, 400)}
    if stamp is not None:
        event["timestamp"] = stamp
    backend._callback(None, q.kCGEventMouseMoved, event, None)
    report = backend.capture_age.snapshot()
    assert report["samples"] == int(stamp == 9880000000)
    assert report["maximum_ms"] == (120 if stamp == 9880000000 else None)
    backend.sink.motion.assert_called_once()
    backend.sink.fail.assert_not_called()


@pytest.mark.parametrize("platform", ["windows", "mac"])
def test_failed_timing_query_cannot_drop_native_input(win, mac, platform):
    backend, api = win if platform == "windows" else mac
    if platform == "windows":
        backend.kernel.GetTickCount64.side_effect = OSError("clock unavailable")
        event = MouseHook(Point(500, 400), 0, 0, 1, 0)
        backend._mouse(0, 0x200, c.addressof(event))
    else:
        api.CGEventGetTimestamp = Mock(side_effect=OSError("timestamp unavailable"))
        backend._callback(None, api.kCGEventMouseMoved, {"position": (500, 400)}, None)
    backend.sink.motion.assert_called_once()
    backend.sink.fail.assert_not_called()
    assert backend.capture_age.snapshot()["samples"] == 0


def test_injected_motion_is_never_sampled_as_physical_capture(win, mac):
    backend, _ = win
    backend.kernel.GetTickCount64.return_value = 10000
    for flags, tag in [(1, 0), (0, EVENT_TAG)]:
        event = MouseHook(Point(500, 400), 0, flags, 9900, tag)
        backend._mouse(0, 0x200, c.addressof(event))
    backend.kernel.GetTickCount64.assert_not_called()
    assert backend.capture_age.snapshot()["samples"] == 0
    backend, q = mac
    backend.event_clock = Mock(return_value=10000000000)
    backend._callback(None, q.kCGEventMouseMoved, {q.kCGEventSourceUserData: EVENT_TAG}, None)
    backend.event_clock.assert_not_called()
    assert backend.capture_age.snapshot()["samples"] == 0
