"""Scan Windows absolute pixels with a fake SendInput, without OS input.

PYTHONPATH=src .venv/bin/python benchmarks/input_pointer_precision.py

The decoder follows Microsoft WPF Input.cs's documented 65536-unit pixel
ranges. This checks coordinate arithmetic, not native movement or display
latency. --expect-errors is for comparison against an older production module.
"""

import argparse
import ctypes as c
import json
from unittest.mock import Mock

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import Kind, Packet
from cleverswitch.flow.input_windows import Input, WindowsInput


def scan(geometry, axis):
    desktop = Desktop([Display(*geometry)])
    api = Mock()
    last = []

    def submitted(count, pointer, size):
        assert count == 1 and size == c.sizeof(Input)
        item = c.cast(pointer, c.POINTER(Input)).contents
        assert item.kind == 0 and item.mouse.flags == 0xE001
        last[:] = [
            desktop.left + item.mouse.x * geometry[2] // 65536,
            desktop.top + item.mouse.y * geometry[3] // 65536,
        ]
        return 1

    api.SendInput.side_effect = submitted
    backend = WindowsInput(api, Mock(), ready=lambda: True, desktop=desktop)
    backend.pointer_desktop = desktop
    point = [geometry[0] + geometry[2] // 2, geometry[1] + geometry[3] // 2]
    incorrect, largest, previous_mismatches = 0, 0, 0
    for coordinate in range(geometry[axis], geometry[axis] + geometry[axis + 2]):
        point[axis] = coordinate
        backend.inject(Packet(Kind.MOVE, 1, tuple(point)))
        error = max(abs(a - b) for a, b in zip(last, point, strict=True))
        incorrect += bool(error)
        largest = max(largest, error)
        previous_mismatches += backend.previous != tuple(last)
    for name in ("EnumDisplayMonitors", "GetSystemMetrics", "SetThreadDpiAwarenessContext", "SetWindowsHookExW"):
        getattr(api, name).assert_not_called()
    return dict(
        geometry=list(geometry), axis="horizontal" if axis == 0 else "vertical",
        pixels_checked=geometry[axis + 2], incorrect_pixels=incorrect,
        maximum_error_pixels=largest, previous_position_mismatches=previous_mismatches,
        native_input=False, geometry_queries_per_report=0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-errors", action="store_true")
    args = parser.parse_args()
    geometries = [
        (0, 0, 1280, 720), (0, 0, 1920, 1080), (0, 0, 2560, 1440),
        (0, 0, 3840, 2160), (0, 0, 5120, 2880), (0, 0, 7680, 4320),
        (-1920, -1080, 3840, 2160), (-3840, -2160, 7680, 4320),
    ]
    cases = [scan(geometry, axis) for geometry in geometries for axis in (0, 1)]
    errors = sum(case["incorrect_pixels"] for case in cases)
    if bool(errors) != args.expect_errors:
        raise RuntimeError("Unexpected absolute pointer precision")
    print(json.dumps(dict(
        scope="production_WindowsInput_with_fake_SendInput_and_documented_pixel_range_decoder",
        native_input=False, cases=cases,
        pixels_checked=sum(case["pixels_checked"] for case in cases), incorrect_pixels=errors,
    ), indent=2))


if __name__ == "__main__":
    main()
