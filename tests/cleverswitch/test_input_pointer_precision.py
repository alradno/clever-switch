"""Absolute pixel contracts through fake native APIs, without moving a cursor.

Microsoft WPF's Input.cs documents each pixel as a range of the 65536-unit
absolute space. Decode that range independently to check the actual target,
not merely the normalized endpoints emitted by our adapter.
"""

import ctypes as c

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import Kind, Packet
from cleverswitch.flow.input_windows import MouseHook, Point

from .test_input_native import mac as mac
from .test_input_native import win as win

SCREENS = [
    (0, 0, 1280, 720),
    (0, 0, 1920, 1080),
    (0, 0, 2560, 1440),
    (0, 0, 3840, 2160),
    (0, 0, 5120, 2880),
    (0, 0, 7680, 4320),
    (-1920, -1080, 3840, 2160),
    (-3840, -2160, 7680, 4320),
]


def physical(event, desktop):
    return (
        desktop.left + event.mouse.x * (desktop.right - desktop.left + 1) // 65536,
        desktop.top + event.mouse.y * (desktop.bottom - desktop.top + 1) // 65536,
    )


@pytest.mark.parametrize("geometry", SCREENS)
@pytest.mark.parametrize("axis", [0, 1], ids=["horizontal", "vertical"])
def test_windows_each_pixel_is_reachable_without_duplicates_or_skips(win, geometry, axis):
    backend, api = win
    desktop = Desktop([Display(*geometry)])
    backend.pointer_desktop = desktop
    point = [geometry[0] + geometry[2] // 2, geometry[1] + geometry[3] // 2]
    for coordinate in range(geometry[axis], geometry[axis] + geometry[axis + 2]):
        point[axis] = coordinate
        backend.inject(Packet(Kind.MOVE, 1, tuple(point)))
        assert physical(api.inputs[-1], desktop) == tuple(point)
        assert backend.previous == tuple(point)
    api.GetSystemMetrics.assert_not_called()
    api.EnumDisplayMonitors.assert_not_called()
    api.SetThreadDpiAwarenessContext.assert_not_called()


@pytest.mark.parametrize("platform", ["win", "mac"])
@pytest.mark.parametrize("point", [(0.25, 1.75), (100.5, 200.5), (100.75, 200.25), (1918.9, 1078.9)])
def test_first_physical_report_uses_the_position_actually_requested_from_the_os(platform, point, request):
    backend, api = request.getfixturevalue(platform)
    desktop = Desktop([Display(0, 0, 1920, 1080)])
    backend._desktop = desktop
    if platform == "win":
        backend.pointer_desktop = desktop
    backend.inject(Packet(Kind.MOVE, 1, point))
    if platform == "win":
        actual = physical(api.inputs[-1], desktop)
        assert actual == tuple(round(value) for value in point)
        next_point = actual[0] + 1, actual[1]
        raw = MouseHook(Point(*next_point), 0, 0, 0, 0)
        backend._mouse(0, 0x200, c.addressof(raw))
    else:
        assert api.events[-1]["position"] == point
        next_point = point[0] + 1, point[1]
        backend._callback(None, api.kCGEventMouseMoved, {"position": next_point}, None)
    backend.sink.motion.assert_called_once_with(*next_point, 1, 0)


@pytest.mark.parametrize("geometry", [(0, 0, 1919, 1079), (-1920, -1080, 1919, 1079)])
def test_windows_capture_anchor_is_an_actual_pixel_on_odd_sized_displays(win, geometry):
    backend, api = win
    desktop = Desktop([Display(*geometry)])
    backend._desktop = desktop
    backend.start(backend.sink)
    backend.capture(True)
    actual = physical(api.inputs[-1], desktop)
    assert backend.anchor == actual
    raw = MouseHook(Point(actual[0] + 1, actual[1]), 0, 0, 0, 0)
    backend._mouse(0, 0x200, c.addressof(raw))
    backend.sink.motion.assert_called_once_with(actual[0] + 1, actual[1], 1, 0)
