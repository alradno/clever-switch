"""Native API contracts with doubles only: no OS hooks, taps or injection."""

import ctypes as c
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_dispatch import Dispatch
from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_keys import EVENT_TAG, MAC, MAC_DEVICE_FLAGS, MAC_MODIFIER_FLAGS, WINDOWS
from cleverswitch.flow.input_macos import MacInput
from cleverswitch.flow.input_protocol import Kind, Packet
from cleverswitch.flow.input_windows import (
    Input,
    KeyHook,
    KeyInput,
    Message,
    MouseHook,
    MouseInput,
    Point,
    WindowsInput,
)


def event(kind, *values):
    return Packet(kind, 1, values)


def sink():
    result = Mock()
    for name in ("motion", "key", "button", "wheel"):
        getattr(result, name).return_value = False
    return result


class User32:
    """Fake message pump: maintains native thread affinity and ABI copies."""

    def __init__(self):
        self.messages = queue.Queue()
        self.inputs, self.visibility = [], []
        self.next_hook = 10
        for name in (
            "SetWindowsHookExW",
            "CallNextHookEx",
            "UnhookWindowsHookEx",
            "SendInput",
            "GetCursorPos",
            "ShowCursor",
            "GetMessageW",
            "PeekMessageW",
            "PostThreadMessageW",
            "TranslateMessage",
            "DispatchMessageW",
            "GetSystemMetrics",
            "SetThreadDpiAwarenessContext",
            "EnumDisplayMonitors",
            "MapVirtualKeyW",
            "GetAsyncKeyState",
            "GetKeyState",
        ):
            setattr(self, name, Mock(return_value=1))
        self.GetCursorPos.side_effect = self.position
        self.SendInput.side_effect = self.submit
        self.GetSystemMetrics.side_effect = {76: -1920, 77: -100, 78: 3840, 79: 1180}.__getitem__
        self.GetMessageW.side_effect = self.message
        self.PostThreadMessageW.side_effect = self.post
        self.ShowCursor.side_effect = lambda value: self.visibility.append((threading.get_ident(), bool(value))) or 0
        self.SetThreadDpiAwarenessContext.return_value = None
        self.GetAsyncKeyState.return_value = 0
        self.GetKeyState.return_value = 0
        self.MapVirtualKeyW.return_value = 0

    def position(self, ptr):
        point = c.cast(ptr, c.POINTER(Point)).contents
        point.x, point.y = 400, 300
        return 1

    def submit(self, count, ptr, size):
        assert count == 1 and size == c.sizeof(Input)
        self.inputs.append(Input.from_buffer_copy(c.string_at(ptr, size)))
        return 1

    def message(self, ptr, window, low, high):
        value = self.messages.get(timeout=2)
        c.cast(ptr, c.POINTER(Message)).contents.message = value
        return 0 if value == 0x12 else 1

    def post(self, thread, message, wparam, lparam):
        self.messages.put(message)
        return 1


@pytest.fixture
def win():
    api = User32()
    backend = WindowsInput(
        api,
        Mock(),
        ready=lambda: True,
        desktop=Desktop([Display(-1920, -100, 1920, 1080), Display(0, 0, 1920, 1080)]),
    )
    backend.sink = sink()
    yield backend, api
    backend.close()


def test_windows_64bit_abi_has_real_win32_widths_even_on_mac():
    assert c.sizeof(Point) == 8
    assert c.sizeof(MouseHook) == 32
    assert c.sizeof(KeyHook) == 24
    assert c.sizeof(MouseInput) == 32
    assert c.sizeof(KeyInput) == 24
    assert c.sizeof(Input) == 40 and Input.value.offset == 8
    assert c.sizeof(Message) == 48


@pytest.mark.parametrize("usage", [4, 40, 70, 72, 79, 88, 100, 0xE0, 0xE1, 0xE4, 0xE5, 0xE6])
def test_windows_injects_physical_codes_and_keeps_extended_key_and_release(usage, win):
    backend, api = win
    backend.inject(event(Kind.KEY, usage, 1))
    backend.inject(event(Kind.KEY, usage, 0))
    down, up = [entry.key for entry in api.inputs]
    assert down.extra == up.extra == EVENT_TAG
    assert up.flags == down.flags | 2
    if usage == 72:
        assert (down.vk, down.scan, down.flags) == (0x13, 0, 0)
    else:
        assert down.vk == 0
        assert down.scan == WINDOWS[usage] & 0xFF
        assert down.flags == 8 | (1 if WINDOWS[usage] & 0x100 else 0)


def test_windows_preserves_physical_local_key_and_button_on_release(win):
    backend, api = win
    backend.held_keys.add(0xE1)
    backend.held_buttons.add(0)
    backend.inject(event(Kind.KEY, 0xE1, 0))
    backend.inject(event(Kind.BUTTON, 0, 0))
    assert not api.inputs


def test_windows_rejected_input_has_one_attempt_and_no_privilege_workaround(win):
    backend, api = win
    api.SendInput.side_effect = None
    api.SendInput.return_value = 0
    with pytest.raises(PermissionError):
        backend.inject(event(Kind.KEY, 4, 1))
    assert api.SendInput.call_count == 1


def test_windows_maps_virtual_desktop_and_preserves_fractional_wheel(win):
    backend, api = win
    backend.inject(event(Kind.MOVE, -1920, -100))
    backend.inject(event(Kind.MOVE, 1919, 1079))
    assert [(entry.mouse.x, entry.mouse.y) for entry in api.inputs] == [(0, 0), (65535, 65535)]
    assert all(entry.mouse.flags == 0xE001 for entry in api.inputs)
    for _ in range(4):
        backend.inject(event(Kind.WHEEL, -0.25, 0.25))
    assert sum(c.c_int32(e.mouse.data).value for e in api.inputs if e.mouse.flags == 0x800) == 120
    assert sum(c.c_int32(e.mouse.data).value for e in api.inputs if e.mouse.flags == 0x1000) == -120


def test_windows_hook_ignores_injected_events_and_tracks_right_modifier(win):
    backend, api = win
    key = KeyHook(0xA3, 29, 1, 0, 0)
    backend._key(0, 0x100, c.addressof(key))
    backend.sink.key.assert_called_once_with(0xE4, 1)
    key.flags |= 0x80
    backend.sink.key.return_value = True
    assert backend._key(0, 0x101, c.addressof(key)) == 1
    backend.sink.key.assert_called_with(0xE4, 0)
    before = backend.sink.key.call_count
    key.extra = EVENT_TAG
    backend._key(0, 0x100, c.addressof(key))
    key.extra, key.flags = 0, 0x10
    backend._key(0, 0x100, c.addressof(key))
    assert backend.sink.key.call_count == before


def test_windows_mouse_callback_uses_anchor_and_filters_own_warps(win):
    backend, api = win
    backend.captured, backend.anchor = True, (500, 300)
    mouse = MouseHook(Point(510, 295), 0, 0, 0, 0)
    backend._mouse(0, 0x200, c.addressof(mouse))
    backend.sink.motion.assert_called_once_with(510, 295, 10, -5)
    mouse.extra = EVENT_TAG
    backend._mouse(0, 0x200, c.addressof(mouse))
    assert backend.sink.motion.call_count == 1
    backend.captured = False


@pytest.mark.parametrize("platform", ["win", "mac"])
@pytest.mark.parametrize("warp_notification", [False, True])
def test_first_physical_motion_after_restore_uses_the_restored_position(platform, warp_notification, request):
    backend, api = request.getfixturevalue(platform)
    backend.previous = (500, 300)
    backend.restore((100, 100))
    if platform == "win":
        if warp_notification:
            warp = MouseHook(Point(100, 100), 0, 0, 0, EVENT_TAG)
            backend._mouse(0, 0x200, c.addressof(warp))
        physical = MouseHook(Point(120, 105), 0, 0, 0, 0)
        backend._mouse(0, 0x200, c.addressof(physical))
    else:
        if warp_notification:
            backend._physical(api.kCGEventMouseMoved, {"position": (100, 100)})
        backend._physical(api.kCGEventMouseMoved, {"position": (120, 105)})
    backend.sink.motion.assert_called_with(120, 105, 20, 5)


@pytest.mark.parametrize("platform", ["win", "mac"])
@pytest.mark.parametrize("notification", [False, True])
def test_first_local_motion_after_remote_input_uses_the_injected_position(platform, notification, request):
    backend, api = request.getfixturevalue(platform)
    backend.previous = (500, 300)
    backend.inject(event(Kind.MOVE, 100, 100))
    if platform == "win":
        if notification:
            injected = MouseHook(Point(100, 100), 0, 1, 0, EVENT_TAG)
            backend._mouse(0, 0x200, c.addressof(injected))
        physical = MouseHook(Point(120, 105), 0, 0, 0, 0)
        backend._mouse(0, 0x200, c.addressof(physical))
    else:
        if notification:
            backend._callback(None, api.kCGEventMouseMoved, api.events[-1], None)
        backend._callback(None, api.kCGEventMouseMoved, {"position": (120, 105)}, None)
    backend.sink.motion.assert_called_once_with(120, 105, 20, 5)


def test_windows_hide_restore_and_unhook_run_on_same_owner_thread(win):
    backend, api = win
    backend.start(backend.sink)
    backend.capture(True)
    assert backend.captured
    backend.close()
    assert not backend.captured and not backend.worker.is_alive()
    assert len(api.visibility) == 2
    assert api.visibility[0][0] == api.visibility[1][0] != threading.get_ident()
    assert [value for _, value in api.visibility] == [False, True]
    assert api.UnhookWindowsHookEx.call_count == 2


def test_windows_partial_hook_failure_removes_already_installed_hook(win):
    backend, api = win
    api.SetWindowsHookExW.side_effect = [10, 0]
    with pytest.raises(PermissionError):
        backend.start(backend.sink)
    api.UnhookWindowsHookEx.assert_called_once_with(10)


def test_dispatch_never_executes_a_timed_out_action_later():
    dispatch = Dispatch(lambda: None)
    action = Mock()
    with pytest.raises(TimeoutError):
        dispatch.call(action, timeout=0.001)
    dispatch.drain()
    action.assert_not_called()
    dispatch.close()
    with pytest.raises(ConnectionError):
        dispatch.call(action)


class Quartz:
    kCGEventLeftMouseDown, kCGEventLeftMouseUp = 1, 2
    kCGEventRightMouseDown, kCGEventRightMouseUp = 3, 4
    kCGEventMouseMoved, kCGEventLeftMouseDragged, kCGEventRightMouseDragged = 5, 6, 7
    kCGEventKeyDown, kCGEventKeyUp, kCGEventFlagsChanged = 10, 11, 12
    kCGEventScrollWheel, kCGEventOtherMouseDown, kCGEventOtherMouseUp, kCGEventOtherMouseDragged = 22, 25, 26, 27
    kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput = -2, -1
    kCGKeyboardEventKeycode, kCGEventSourceUserData, kCGMouseEventButtonNumber, kCGMouseEventClickState = 9, 42, 3, 1
    kCGKeyboardEventAutorepeat = 8
    kCGEventSourceStateCombinedSessionState = 0
    kCGScrollWheelEventFixedPtDeltaAxis1, kCGScrollWheelEventFixedPtDeltaAxis2 = 93, 94
    kCGEventFlagMaskAlphaShift = 1 << 16
    kCGHIDEventTap, kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault = 0, 1, 0, 0
    kCFRunLoopCommonModes, kCGScrollEventUnitLine = "common", 1

    def __init__(self):
        self.events, self.warps, self.visibility = [], [], []
        self.position, self.flags = (500, 300), 0
        self.stop = threading.Event()
        self.CGPreflightListenEventAccess = Mock(return_value=True)
        self.CGEventSourceKeyState = Mock(return_value=False)
        self.CGEventSourceButtonState = Mock(return_value=False)
        self.CGEventSourceFlagsState = Mock(side_effect=lambda state: self.flags)
        self.CGPreflightPostEventAccess = Mock(return_value=True)
        self.CGEventTapCreate = Mock(return_value=object())
        self.CFMachPortCreateRunLoopSource = Mock(return_value=object())
        self.CFRunLoopGetCurrent = Mock(return_value=object())
        self.CFRunLoopAddSource = Mock()
        self.CFRunLoopRemoveSource = Mock()
        self.CFMachPortInvalidate = Mock()
        self.CGEventTapEnable = Mock()
        self.CFRunLoopWakeUp = Mock()
        self.CFRunLoopRun = lambda: self.stop.wait(2)
        self.CFRunLoopStop = lambda loop: self.stop.set()
        self.CGDisplayHideCursor = lambda display: self.visibility.append(False) or 0
        self.CGDisplayShowCursor = lambda display: self.visibility.append(True) or 0
        self.CGMainDisplayID = lambda: 1

    def CGEventCreate(self, source):
        return {"position": self.position, "flags": self.flags}

    def CGEventGetLocation(self, value):
        x, y = value.get("position", self.position)
        return SimpleNamespace(x=x, y=y)

    def CGWarpMouseCursorPosition(self, point):
        self.position = point
        self.warps.append(point)
        return 0

    def CGEventGetFlags(self, value):
        return value.get("flags", 0)

    def CGEventSetFlags(self, value, flags):
        value["flags"] = flags

    def CGEventSetType(self, value, kind):
        value["kind"] = kind

    def CGEventGetIntegerValueField(self, value, field):
        return value.get(field, 0)

    CGEventGetDoubleValueField = CGEventGetIntegerValueField

    def CGEventSetIntegerValueField(self, value, field, data):
        value[field] = data

    CGEventSetDoubleValueField = CGEventSetIntegerValueField

    def CGEventCreateKeyboardEvent(self, source, code, down):
        return {"kind": self.kCGEventKeyDown if down else self.kCGEventKeyUp, 9: code, "flags": self.flags}

    def CGEventCreateMouseEvent(self, source, kind, point, button):
        return {"kind": kind, "position": point, 3: button, "flags": self.flags}

    def CGEventCreateScrollWheelEvent(self, source, units, axes, vertical, horizontal):
        return {"kind": self.kCGEventScrollWheel, 93: vertical, 94: horizontal, "flags": self.flags}

    def CGEventPost(self, tap, value):
        self.events.append(value.copy())
        self.flags = value["flags"]
        if "position" in value:
            self.position = value["position"]


@pytest.fixture
def mac():
    q = Quartz()
    backend = MacInput(q, lambda: True, lambda: False, Desktop([Display(0, 0, 1920, 1080)]))
    backend.sink = sink()
    yield backend, q
    backend.close()


def test_mac_constructing_backend_never_installs_tap(mac):
    backend, q = mac
    q.CGEventTapCreate.assert_not_called()
    assert backend.worker is None


@pytest.mark.parametrize("usage", range(0xE0, 0xE8))
def test_mac_keyup_clears_inherited_modifier_flags(usage, mac):
    backend, q = mac
    backend.inject(event(Kind.KEY, usage, 1))
    mask = MAC_MODIFIER_FLAGS[usage] | MAC_DEVICE_FLAGS[usage]
    assert q.events[-1]["flags"] & mask == mask
    backend.inject(event(Kind.KEY, usage, 0))
    assert q.events[-1]["flags"] & mask == 0
    assert q.events[-1][q.kCGEventSourceUserData] == EVENT_TAG


def test_mac_left_and_right_shift_are_independent_and_local_keyup_is_preserved(mac):
    backend, q = mac
    for usage in (0xE1, 0xE5):
        backend.inject(event(Kind.KEY, usage, 1))
    backend.inject(event(Kind.KEY, 0xE1, 0))
    assert q.events[-1]["flags"] & (1 << 17)
    assert not q.events[-1]["flags"] & 2
    assert q.events[-1]["flags"] & 4
    backend.held_keys.add(0xE5)
    before = len(q.events)
    backend.inject(event(Kind.KEY, 0xE5, 0))
    assert len(q.events) == before


def test_mac_flags_changed_uses_device_specific_bit_and_filters_own_events(mac):
    backend, q = mac
    raw = {9: MAC[0xE1], "flags": (1 << 17) | 4}  # Right shift still held, left released.
    backend._callback(None, q.kCGEventFlagsChanged, raw, None)
    backend.sink.key.assert_called_once_with(0xE1, 0)
    raw[42] = EVENT_TAG
    backend._callback(None, q.kCGEventFlagsChanged, raw, None)
    assert backend.sink.key.call_count == 1


def test_mac_tap_loss_cancels_focus_and_secure_input_prevents_start(mac):
    backend, q = mac
    backend._callback(None, q.kCGEventTapDisabledByTimeout, None, None)
    backend.sink.fail.assert_called_once()
    backend.secure_input = lambda: True
    assert not backend.ready()
    with pytest.raises(PermissionError):
        backend.start(backend.sink)
    q.CGEventTapCreate.assert_not_called()


def test_mac_click_drag_double_click_and_fractional_wheel(mac):
    backend, q = mac
    backend.inject(event(Kind.BUTTON, 0, 1))
    backend.inject(event(Kind.MOVE, 502, 301))
    assert q.events[-1]["kind"] == q.kCGEventLeftMouseDragged
    backend.inject(event(Kind.BUTTON, 0, 0))
    backend.inject(event(Kind.BUTTON, 0, 1))
    assert q.events[-1][q.kCGMouseEventClickState] == 2
    backend.inject(event(Kind.BUTTON, 0, 0))
    assert q.events[-1][q.kCGMouseEventClickState] == 2
    backend.inject(event(Kind.WHEEL, 0.25, -0.5))
    assert q.events[-1][93] == -0.5 and q.events[-1][94] == -0.25


def test_mac_capture_restores_cursor_and_removes_tap_on_close(mac):
    backend, q = mac
    backend.start(backend.sink)
    backend.capture(True)
    assert q.position == backend.anchor
    assert not backend._physical(q.kCGEventMouseMoved, {"position": backend.anchor})
    backend.sink.motion.assert_not_called()
    backend.close()
    assert q.position == (500, 300) and q.visibility == [False, True]
    q.CFMachPortInvalidate.assert_called_once()
    q.CFRunLoopRemoveSource.assert_called_once()
    assert not backend.worker.is_alive()


def test_mac_refused_event_tap_never_retries_or_changes_permissions(mac):
    backend, q = mac
    q.CGEventTapCreate.return_value = None
    with pytest.raises(PermissionError):
        backend.start(backend.sink)
    assert q.CGEventTapCreate.call_count == 1
    assert not q.events and not q.visibility


@pytest.mark.parametrize("message,values", [(0x20A, (0, -1)), (0x20E, (-1, 0))])
def test_windows_hook_decodes_signed_wheel_without_unsigned_wrap(win, message, values):
    backend, api = win
    raw = MouseHook(Point(500, 300), (0x10000 - 120) << 16, 0, 0, 0)
    backend._mouse(0, message, c.addressof(raw))
    backend.sink.wheel.assert_called_once_with(*values)


@pytest.mark.parametrize("message,button,down,data", [(0x201, 0, 1, 0), (0x208, 2, 0, 0), (0x20B, 4, 1, 2 << 16)])
def test_windows_hook_routes_mouse_buttons(win, message, button, down, data):
    backend, api = win
    raw = MouseHook(Point(500, 300), data, 0, 0, 0)
    backend.sink.button.return_value = True
    assert backend._mouse(0, message, c.addressof(raw)) == 1
    backend.sink.button.assert_called_once_with(button, down)


def test_windows_unknown_remote_key_and_callback_exception_end_focus(win):
    backend, api = win
    backend.captured = True
    raw = KeyHook(0, 0, 0, 0, 0)
    assert backend._key(0, 0x100, c.addressof(raw)) == 1
    backend.sink.fail.assert_called_once()
    backend.sink.key.side_effect = RuntimeError
    raw.scan = 30
    assert backend._key(0, 0x100, c.addressof(raw)) == 1
    assert backend.sink.fail.call_count == 2
    backend.captured = False


def test_native_readiness_cache_does_not_query_os_per_mouse_report(win, mac):
    for backend, _ in (win, mac):
        now = [1.0]
        backend.clock = lambda now=now: now[0]
        backend._ready = Mock(return_value=True)
        for _ in range(1000):
            assert backend.ready()
        backend._ready.assert_called_once()
        now[0] += 0.101
        backend._ready.return_value = False
        assert not backend.ready()
        assert backend._ready.call_count == 2


@pytest.mark.parametrize("platform", ["win", "mac"])
def test_permission_query_cannot_stall_native_input(platform, request):
    backend, _ = request.getfixturevalue(platform)
    now = [1.0]
    backend.clock = lambda: now[0]
    backend.start(backend.sink)
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    result = []

    def slow_check():
        entered.set()
        release.wait(1)
        return True

    backend._ready = slow_check
    now[0] += 0.101

    def input_path():
        result.append(backend.ready())
        returned.set()

    thread = threading.Thread(target=input_path)
    thread.start()
    try:
        assert entered.wait(1)
        assert returned.wait(0.05), "The native/TLS input path waited for a permission API"
        assert result == [True]
        now[0] += 0.6
        assert not backend.ready()  # A stuck checker must fail closed.
    finally:
        release.set()
        thread.join(1)


def test_windows_move_and_drag_use_prepared_geometry_without_os_queries(win):
    backend, api = win
    backend.start(backend.sink)
    api.GetSystemMetrics.reset_mock()
    api.EnumDisplayMonitors.reset_mock()
    api.SetThreadDpiAwarenessContext.reset_mock()
    backend.inject(event(Kind.BUTTON, 0, 1))
    for x in range(100):
        backend.inject(event(Kind.MOVE, x, 100))
    backend.inject(event(Kind.BUTTON, 0, 0))
    api.GetSystemMetrics.assert_not_called()
    api.EnumDisplayMonitors.assert_not_called()
    api.SetThreadDpiAwarenessContext.assert_not_called()
    assert len(api.inputs) == 102
    assert all(entry.mouse.flags & 0x2000 for entry in api.inputs[1:-1])


def test_windows_device_notification_renews_only_keyboard_hook_on_owner_thread(win):
    from .test_input_stream import until

    backend, api = win
    monitor = Mock()
    backend.monitor_factory = Mock(return_value=monitor)
    backend.start(backend.sink)
    backend.held_keys.add(0xE1)
    backend._device_changed()
    until(lambda: backend.sink.keyboard_changed.called)
    assert [call.args[0] for call in api.SetWindowsHookExW.call_args_list] == [14, 13, 13]
    assert not backend.held_keys
    backend.close()
    monitor.start.assert_called_once()
    monitor.close.assert_called_once()
    calls = api.SetWindowsHookExW.call_count
    backend._device_changed()
    assert api.SetWindowsHookExW.call_count == calls


def test_mac_observer_lifetime_follows_enabled_tap(mac):
    backend, api = mac
    monitor = Mock()
    backend.monitor_factory = Mock(return_value=monitor)
    backend.start(backend.sink)
    changed = backend.monitor_factory.call_args.args[0]
    backend.held_keys.add(4)
    changed()
    assert not backend.held_keys
    backend.sink.keyboard_changed.assert_called_once()
    backend.close()
    monitor.start.assert_called_once()
    monitor.close.assert_called_once()


def test_mac_capture_failure_and_post_failure_are_reported_without_fake_success(mac):
    backend, q = mac
    q.CGDisplayHideCursor = Mock(return_value=1)
    with pytest.raises(OSError):
        backend.capture(True)
    assert not backend.captured
    assert q.position == (500, 300)
    q.CGEventCreateKeyboardEvent = Mock(return_value=None)
    with pytest.raises(PermissionError):
        backend.inject(event(Kind.KEY, 4, 1))


def test_mac_callback_routes_buttons_motion_wheel_and_unknown_remote_keys(mac):
    backend, q = mac
    backend._physical(q.kCGEventLeftMouseDown, {})
    backend.sink.button.assert_called_with(0, 1)
    backend._physical(q.kCGEventOtherMouseUp, {q.kCGMouseEventButtonNumber: 4})
    backend.sink.button.assert_called_with(4, 0)
    backend._physical(q.kCGEventScrollWheel, {93: 0.5, 94: -0.25})
    backend.sink.wheel.assert_called_with(0.25, 0.5)
    backend._physical(q.kCGEventMouseMoved, {"position": (510, 300)})
    backend._physical(q.kCGEventMouseMoved, {"position": (515, 295)})
    backend.sink.motion.assert_called_with(515, 295, 5, -5)
    backend.captured = True
    assert backend._physical(q.kCGEventKeyDown, {9: 999})
    backend.sink.fail.assert_called_once()
    backend.captured = False


def test_mac_key_repeat_and_caps_changes_are_forwarded_once(mac):
    backend, q = mac
    backend.inject(event(Kind.KEY, 4, 1))
    backend.inject(event(Kind.KEY, 4, 1))
    assert [v[q.kCGKeyboardEventAutorepeat] for v in q.events] == [0, 1]
    raw = {9: 57, "flags": q.kCGEventFlagMaskAlphaShift}
    backend._physical(q.kCGEventFlagsChanged, raw)
    backend._physical(q.kCGEventFlagsChanged, raw)
    assert [call.args for call in backend.sink.key.call_args_list] == [(57, 1), (57, 0)]


def test_mac_preserves_held_keys_before_capture_is_installed(mac):
    backend, q = mac
    q.CGEventSourceKeyState.side_effect = lambda state, code: code == MAC[0xE1]
    q.CGEventSourceButtonState.side_effect = lambda state, button: button == 0
    backend.start(backend.sink)
    assert backend.held_keys == {0xE1} and backend.held_buttons == {0}


def test_mac_click_uses_latest_queued_move_even_before_window_server_applies_it(mac):
    backend, q = mac
    q.CGEventPost = lambda tap, value: q.events.append(value.copy())
    backend.inject(event(Kind.MOVE, 700, 600))
    assert q.position == (500, 300)  # Simulate asynchronous native delivery.
    backend.inject(event(Kind.BUTTON, 0, 1))
    assert q.events[-1]["position"] == (700, 600)


def test_mac_caps_toggle_applies_to_following_keys_and_respects_native_override(mac):
    backend, q = mac
    assert backend.lock_state() == (0, 1)
    assert backend.lock_keys(3, 7) == (57,)
    backend.inject(event(Kind.KEY, 57, 1))
    backend.inject(event(Kind.KEY, 57, 1))
    backend.inject(event(Kind.KEY, 57, 0))
    assert q.events[-1]["kind"] == q.kCGEventFlagsChanged
    assert backend.lock_state() == (1, 1)
    assert backend.lock_keys(3, 7) == ()
    backend.inject(event(Kind.KEY, 4, 1))
    assert q.events[-1]["flags"] & q.kCGEventFlagMaskAlphaShift
    backend._physical(q.kCGEventFlagsChanged, {9: 57, "flags": 0})
    assert backend.injected_caps is None
    backend.sink.key.assert_called_with(57, 0)


def test_windows_lock_masks_compare_only_supported_source_state(win):
    backend, api = win
    api.GetKeyState.side_effect = lambda vk: int(vk == 0x90)
    assert backend.lock_state() == (2, 7)
    assert backend.lock_keys(1, 1) == (57,)  # Do not change NumLock for a Mac source.
    assert backend.lock_keys(1, 7) == (57, 83)


def test_mac_failed_cursor_show_is_retained_for_a_finite_later_retry(mac):
    backend, q = mac
    backend.capture(True)
    q.CGDisplayShowCursor = Mock(return_value=1)
    with pytest.raises(OSError):
        backend.capture(False)
    assert not backend.captured and backend.hidden
    assert q.position == (500, 300)
    q.CGDisplayShowCursor.return_value = 0
    backend.capture(False)
    assert not backend.hidden
    assert q.CGDisplayShowCursor.call_count == 2


def test_native_backend_cannot_restart_while_previous_worker_is_closing(win, mac):
    for backend, _ in (win, mac):
        backend.worker = Mock()
        backend.worker.is_alive.return_value = True
        backend.stopping.set()
        try:
            with pytest.raises(RuntimeError):
                backend.start(backend.sink)
        finally:
            backend.worker = None  # The worker above was a double, never a native thread.


def test_dispatch_rolls_back_an_action_that_finishes_after_its_caller_timed_out():
    ready, running, finish = threading.Event(), threading.Event(), threading.Event()
    value = [False]
    dispatch = Dispatch(ready.set)

    def action():
        running.set()
        assert finish.wait(1)
        value[0] = True

    def owner():
        assert ready.wait(1)
        dispatch.drain()

    worker = threading.Thread(target=owner)
    worker.start()
    try:
        with pytest.raises(TimeoutError):
            dispatch.call(action, timeout=0.02, rollback=lambda: value.__setitem__(0, False))
        assert running.is_set()
        finish.set()
        worker.join(1)
        assert not worker.is_alive() and value == [False]
    finally:
        finish.set()
        ready.set()
        worker.join(1)
        dispatch.close()


@pytest.mark.parametrize("completed", [False, True])
def test_dispatch_arbitrates_wakeup_failure_against_completed_work(completed):
    action = Mock(return_value=42)
    dispatch = Dispatch(None)

    def wake():
        if completed:
            dispatch.drain()
        raise OSError("Worker wakeup failed")

    dispatch.wake = wake
    if completed:
        assert dispatch.call(action) == 42
        action.assert_called_once_with()
    else:
        with pytest.raises(OSError):
            dispatch.call(action)
        dispatch.drain()
        action.assert_not_called()
    dispatch.close()


def test_dispatch_close_rolls_back_running_work_on_its_owner_thread():
    queued, running, finish = threading.Event(), threading.Event(), threading.Event()
    dispatch = Dispatch(queued.set)
    calls, errors = [], []

    def action():
        running.set()
        assert finish.wait(2)
        calls.append(("action", threading.get_ident()))

    def caller():
        try:
            dispatch.call(action, timeout=2, rollback=lambda: calls.append(("rollback", threading.get_ident())))
        except Exception as error:
            errors.append(error)

    requester = threading.Thread(target=caller)
    owner = threading.Thread(target=dispatch.drain)
    requester.start()
    try:
        assert queued.wait(1)
        owner.start()
        assert running.wait(1)
        dispatch.close()
    finally:
        finish.set()
        requester.join(2)
        if owner.ident is not None:
            owner.join(2)
    assert not requester.is_alive() and not owner.is_alive()
    assert calls == [("action", owner.ident), ("rollback", owner.ident)]
    assert len(errors) == 1 and isinstance(errors[0], ConnectionError)


def test_windows_capture_cannot_hide_cursor_after_handoff_timed_out(win):
    backend, api = win
    finish = threading.Event()
    first = [True]
    submit = api.SendInput.side_effect

    def delayed(*args):
        if first[0]:
            first[0] = False
            assert finish.wait(2)
        return submit(*args)

    backend.start(backend.sink)
    api.SendInput.side_effect = delayed
    try:
        with pytest.raises(TimeoutError):
            backend.capture(True)
        finish.set()
        # Barrier on the native owner: the old capture has completed before
        # checking that its timeout cannot leave the source cursor hidden.
        backend.dispatch.call(lambda: None)
        assert not backend.captured and not backend.hidden
    finally:
        finish.set()


def test_windows_release_cannot_confirm_before_a_late_capture_has_finished(win):
    backend, api = win
    finish = threading.Event()
    position = api.GetCursorPos.side_effect

    def delayed(*args):
        assert finish.wait(3)
        return position(*args)

    backend.start(backend.sink)
    api.GetCursorPos.side_effect = delayed
    try:
        with pytest.raises(TimeoutError):
            backend.capture(True)
        # The worker has not yet set captured/hidden/restore_pending. A release
        # must still wait for that action, or cleanup could be confirmed early.
        assert not backend.captured and backend.restore_pending is None
        with pytest.raises(TimeoutError):
            backend.capture(False)
    finally:
        finish.set()
        backend.dispatch.call(lambda: None)
    assert not backend.captured and not backend.hidden and backend.restore_pending is None
