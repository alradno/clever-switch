"""OS topology contracts only: observers never open or read HID devices."""

import ctypes as c
import sys
import uuid
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_devices import (
    InputDeviceMonitor,
    MacInputMonitor,
    MacKeyboardMonitor,
    MacMouseMonitor,
    NotificationFilter,
    WindowsInputMonitor,
    WindowsKeyboardMonitor,
    WindowsMouseMonitor,
)
from cleverswitch.flow.input_health import Readiness


def test_native_input_observers_register_and_close_without_capturing_input():
    # Real native ABI in each build environment; no hooks, event taps, HID open,
    # input reports or synthetic events. Other OSes exercise the doubles below.
    factory = {"win32": WindowsInputMonitor, "darwin": MacInputMonitor}.get(sys.platform)
    if factory is None:
        return
    monitor = factory(lambda kind: None)
    try:
        monitor.start()
        assert monitor.active
    finally:
        monitor.close()
    assert not monitor.active


@pytest.mark.parametrize("factory,interface", [
    (WindowsKeyboardMonitor, "884b96c3-56ef-11d1-bc8c-00a0c91405dd"),
    (WindowsMouseMonitor, "378de44c-56ef-11d1-bc8c-00a0c91405dd"),
])
def test_windows_observer_filters_device_type_and_ignores_late_callbacks(factory, interface):
    api, changed = Mock(), Mock()

    def register(info, context, callback, handle):
        info = c.cast(info, c.POINTER(NotificationFilter)).contents
        assert info.size == c.sizeof(NotificationFilter) == 416
        assert info.kind == info.flags == info.reserved == 0
        assert bytes(info.payload[:16]) == uuid.UUID(interface).bytes_le
        c.cast(handle, c.POINTER(c.c_void_p)).contents.value = 100
        return 0

    api.CM_Register_Notification.side_effect = register
    api.CM_Unregister_Notification.return_value = 0
    monitor = factory(changed, api)
    monitor.start()
    for action in (0, 1, 2, 3, 7):
        monitor.callback(None, None, action, None, 0)
    assert changed.call_count == 2
    monitor.close()
    monitor.callback(None, None, 0, None, 0)
    assert changed.call_count == 2
    monitor.close()
    api.CM_Unregister_Notification.assert_called_once()


def test_windows_observer_failure_does_not_claim_it_is_active():
    api = Mock()
    api.CM_Register_Notification.return_value = 1
    monitor = WindowsKeyboardMonitor(Mock(), api)
    with pytest.raises(OSError):
        monitor.start()
    assert not monitor.active
    monitor.close()
    api.CM_Unregister_Notification.assert_not_called()


def mac_monitor(factory=MacKeyboardMonitor):
    io, cf, changed = Mock(), Mock(), Mock()
    io.IOHIDManagerCreate.return_value = 10
    io.IOHIDManagerCopyDevices.return_value = 20
    cf.CFStringCreateWithCString.side_effect = [30, 31]
    cf.CFNumberCreate.side_effect = [40, 41]
    cf.CFDictionaryCreate.return_value = 50
    cf.CFSetGetCount.return_value = 1
    cf.CFSetGetValues.side_effect = lambda value, target: target.__setitem__(0, 60)
    cf.CFRunLoopGetCurrent.return_value = 70
    monitor = factory(changed, io, cf, mode=80, dictionary_callbacks=(90, 91))
    return monitor, io, cf, changed


@pytest.mark.parametrize("factory,usage", [(MacKeyboardMonitor, 6), (MacMouseMonitor, 2)])
def test_mac_observer_ignores_initial_enumeration_and_only_announces_real_changes(factory, usage):
    monitor, io, cf, changed = mac_monitor(factory)
    values = []

    def number(allocator, kind, value):
        values.append(c.cast(value, c.POINTER(c.c_int32)).contents.value)
        return 40 + len(values)

    cf.CFNumberCreate.side_effect = number
    monitor.start()
    assert monitor.active and monitor.devices == {60}
    monitor.arrived(None, 0, None, 60)
    changed.assert_not_called()  # Initial callbacks must preserve held local keys.
    monitor.removed(None, 0, None, 60)
    monitor.removed(None, 0, None, 60)
    monitor.arrived(None, 0, None, 60)
    assert changed.call_count == 2
    assert [call.args[1] for call in cf.CFStringCreateWithCString.call_args_list] == [
        b"DeviceUsagePage",
        b"DeviceUsage",
    ]
    assert cf.CFDictionaryCreate.call_args.args[3:] == (2, 90, 91)
    assert values == [1, usage]
    io.IOHIDManagerOpen.assert_not_called()
    io.IOHIDManagerScheduleWithRunLoop.assert_called_once_with(10, 70, 80)
    monitor.close()
    monitor.removed(None, 0, None, 60)
    assert changed.call_count == 2
    io.IOHIDManagerUnscheduleFromRunLoop.assert_called_once_with(10, 70, 80)
    assert not monitor.active and not monitor.devices
    assert len(cf.CFRelease.call_args_list) == 7


def test_combined_observer_keeps_device_type_and_discards_late_notifications():
    changed = Mock()
    keyboard, mouse = Mock(), Mock()
    monitor = InputDeviceMonitor(changed, keyboard, mouse)
    monitor.start()
    keyboard.call_args.args[0]()
    mouse.call_args.args[0]()
    assert [call.args for call in changed.call_args_list] == [("keyboard",), ("mouse",)]
    with pytest.raises(RuntimeError):
        monitor.start()
    monitor.close()
    keyboard.call_args.args[0]()
    mouse.call_args.args[0]()
    assert changed.call_count == 2
    keyboard.return_value.close.assert_called_once()
    mouse.return_value.close.assert_called_once()
    assert not monitor.active and not monitor.registered


@pytest.mark.parametrize("failed", [0, 1])
def test_combined_observer_cleans_both_after_partial_registration(failed):
    factories = [Mock(), Mock()]
    factories[failed].return_value.start.side_effect = OSError
    monitor = InputDeviceMonitor(Mock(), *factories)
    with pytest.raises(OSError):
        monitor.start()
    for factory in factories:
        factory.return_value.close.assert_called_once()
    assert not monitor.active and not monitor.registered


@pytest.mark.parametrize("failed", [0, 1])
def test_failed_observer_removal_still_closes_other_type_and_blocks_duplicate_registration(failed):
    factories = [Mock(), Mock()]
    monitor = InputDeviceMonitor(Mock(), *factories)
    monitor.start()
    factories[failed].return_value.close.side_effect = OSError
    with pytest.raises(OSError):
        monitor.close()
    assert not monitor.active and monitor.registered
    for factory in factories:
        factory.return_value.close.assert_called_once()
    with pytest.raises(RuntimeError):
        monitor.start()
    factories[failed].return_value.close.side_effect = None
    monitor.close()
    assert not monitor.registered


@pytest.mark.parametrize("failure", ["manager", "key", "dictionary", "count"])
def test_mac_observer_releases_partial_registration(failure):
    monitor, io, cf, changed = mac_monitor()
    if failure == "manager":
        io.IOHIDManagerCreate.return_value = None
    elif failure == "key":
        cf.CFStringCreateWithCString.side_effect = [None, 31]
    elif failure == "dictionary":
        cf.CFDictionaryCreate.return_value = None
    else:
        cf.CFSetGetCount.return_value = 257
    with pytest.raises(OSError):
        monitor.start()
    assert not monitor.active and monitor.manager is None
    io.IOHIDManagerScheduleWithRunLoop.assert_not_called()


def test_readiness_does_not_run_a_worker_without_an_enabled_backend():
    now = [1.0]
    check = Mock(return_value=True)
    health = Readiness(check, lambda: now[0])
    assert health.worker is None
    assert health.ready()
    check.assert_called_once()
    for _ in range(100):
        assert health.ready()
    check.assert_called_once()
    health.start()
    assert health.worker.is_alive()
    with pytest.raises(RuntimeError):
        health.start()
    health.close()
    assert not health.worker.is_alive()
    now[0] += 1
    check.side_effect = OSError
    assert not health.ready()
