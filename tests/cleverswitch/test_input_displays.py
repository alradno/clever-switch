"""Display notifications and lifetime: no real capture or screen reconfiguration."""

import ctypes as c
import sys
import threading
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_displays import MacDisplayMonitor, WindowClass, WindowsDisplayMonitor
from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_native import mac as mac
from .test_input_native import win as win
from .test_input_service import connected, enter
from .test_input_service import services as services
from .test_input_stream import until


def windows_monitor():
    api, kernel, changed = Mock(), Mock(), Mock()
    kernel.GetModuleHandleW.return_value = 10
    api.RegisterClassW.return_value = 20
    api.CreateWindowExW.return_value = 30
    api.DestroyWindow.return_value = api.UnregisterClassW.return_value = 1
    api.DefWindowProcW.return_value = 0
    return WindowsDisplayMonitor(changed, api, kernel), api, changed


def test_native_display_observer_registers_and_closes_without_changing_screens_or_input():
    factory = {"win32": WindowsDisplayMonitor, "darwin": MacDisplayMonitor}.get(sys.platform)
    if factory is None:
        return
    monitor = factory(lambda: None)
    try:
        monitor.start()
        assert monitor.active
    finally:
        monitor.close()
    assert not monitor.active


def test_windows_window_class_abi_and_hidden_top_level_broadcast_receiver():
    assert c.sizeof(WindowClass) == 72
    assert WindowClass.procedure.offset == 8
    assert WindowClass.instance.offset == 24
    assert WindowClass.name.offset == 64
    monitor, api, changed = windows_monitor()

    def register(pointer):
        info = c.cast(pointer, c.POINTER(WindowClass)).contents
        assert info.instance == 10 and info.name == monitor.name
        assert info.class_extra == info.window_extra == info.style == 0
        return 20

    api.RegisterClassW.side_effect = register
    monitor.start()
    args = api.CreateWindowExW.call_args.args
    assert args[3] == 0x80000000  # WS_POPUP, without WS_VISIBLE or activation.
    assert args[4:8] == (0, 0, 0, 0)
    assert args[8] is None  # HWND_MESSAGE would not receive broadcast changes.
    for message in (0x007E, 0x02E0, 0x000F, 0x0200):
        monitor.callback(30, message, 0, 0)
    assert changed.call_count == 2
    with pytest.raises(RuntimeError):
        monitor.start()
    monitor.close()
    monitor.callback(30, 0x007E, 0, 0)
    assert changed.call_count == 2
    monitor.close()
    api.DestroyWindow.assert_called_once_with(30)
    api.UnregisterClassW.assert_called_once_with(monitor.name, 10)


@pytest.mark.parametrize("stage", ["register", "create", "create_exception"])
def test_windows_registration_failure_keeps_partial_resources_until_owner_cleanup(stage):
    monitor, api, changed = windows_monitor()
    if stage == "register":
        api.RegisterClassW.return_value = 0
    elif stage == "create":
        api.CreateWindowExW.return_value = None
    else:
        api.CreateWindowExW.side_effect = OSError
    with pytest.raises(OSError):
        monitor.start()
    assert not monitor.active
    monitor.close()
    assert not monitor.atom and monitor.window is None
    api.DestroyWindow.assert_not_called()
    assert api.UnregisterClassW.call_count == (0 if stage == "register" else 1)
    changed.assert_not_called()


def test_windows_cleanup_requires_the_window_owner_thread():
    monitor, api, _ = windows_monitor()
    monitor.start()
    errors = []

    def other_thread():
        try:
            monitor.close()
        except RuntimeError as error:
            errors.append(error)

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join(1)
    assert len(errors) == 1
    api.DestroyWindow.assert_not_called()
    monitor.close()
    assert not monitor.window and not monitor.atom


@pytest.mark.parametrize("stage", ["window", "class"])
def test_windows_cleanup_failure_retains_callback_and_blocks_another_registration(stage):
    monitor, api, changed = windows_monitor()
    monitor.start()
    method = api.DestroyWindow if stage == "window" else api.UnregisterClassW
    method.return_value = 0
    with pytest.raises(OSError):
        monitor.close()
    assert not monitor.active and monitor.atom
    assert bool(monitor.window) == (stage == "window")
    monitor.callback(30, 0x007E, 0, 0)
    changed.assert_not_called()
    with pytest.raises(RuntimeError):
        monitor.start()
    method.return_value = 1
    monitor.close()
    assert not monitor.window and not monitor.atom


def test_mac_uses_identical_callback_for_removal_and_ignores_late_notifications():
    api, changed = Mock(), Mock()
    api.CGDisplayRegisterReconfigurationCallback.return_value = 0
    api.CGDisplayRemoveReconfigurationCallback.return_value = 0
    monitor = MacDisplayMonitor(changed, api)
    monitor.start()
    callback = api.CGDisplayRegisterReconfigurationCallback.call_args.args[0]
    callback(1, 1, None)  # Begin reconfiguration must already invalidate geometry.
    callback(1, 16, None)
    assert changed.call_count == 2
    with pytest.raises(RuntimeError):
        monitor.start()
    monitor.close()
    api.CGDisplayRemoveReconfigurationCallback.assert_called_once_with(callback, None)
    callback(1, 16, None)
    assert changed.call_count == 2
    monitor.close()
    api.CGDisplayRemoveReconfigurationCallback.assert_called_once()


@pytest.mark.parametrize("raises", [False, True])
def test_mac_registration_failure_does_not_claim_observation(raises):
    api = Mock()
    api.CGDisplayRegisterReconfigurationCallback.return_value = 1
    if raises:
        api.CGDisplayRegisterReconfigurationCallback.side_effect = OSError
    monitor = MacDisplayMonitor(Mock(), api)
    with pytest.raises(OSError):
        monitor.start()
    assert not monitor.active and not monitor.registered
    monitor.close()
    api.CGDisplayRemoveReconfigurationCallback.assert_not_called()


def test_mac_failed_removal_keeps_callback_but_disables_notifications():
    api, changed = Mock(), Mock()
    api.CGDisplayRegisterReconfigurationCallback.return_value = 0
    api.CGDisplayRemoveReconfigurationCallback.return_value = 1
    monitor = MacDisplayMonitor(changed, api)
    monitor.start()
    with pytest.raises(OSError):
        monitor.close()
    assert not monitor.active and monitor.registered
    monitor.callback(1, 1, None)
    changed.assert_not_called()
    with pytest.raises(RuntimeError):
        monitor.start()
    api.CGDisplayRemoveReconfigurationCallback.return_value = 0
    monitor.close()


class Observer:
    def __init__(self, changed):
        self.changed = changed
        self.active = False
        self.owner = None

    def start(self):
        self.owner = threading.get_ident()
        self.active = True

    def close(self):
        assert threading.get_ident() == self.owner
        self.active = False

    def notify(self):
        if self.active:
            self.changed()


@pytest.mark.parametrize("platform", ["win", "mac"])
def test_backend_invalidates_immediately_and_late_callback_cannot_affect_next_session(platform, request):
    backend, api = request.getfixturevalue(platform)
    backend.display_monitor_factory = Observer
    assert not backend.display_events
    backend.start(backend.sink)
    old = backend.display_monitor
    assert backend.display_events and backend.ready()
    old.notify()
    backend.sink.fail.assert_called_once_with("display")
    assert not backend.ready() and backend.display_invalidated
    backend.close()
    assert not backend.display_events and not backend.display_invalidated
    if platform == "mac":
        api.stop.clear()
    backend.start(backend.sink)
    assert backend.display_monitor is not old
    old.notify()
    old.changed()  # A callback that had already passed its observer active check.
    backend.sink.fail.assert_called_once()
    assert backend.ready() and not backend.display_invalidated


@pytest.mark.parametrize("platform", ["win", "mac"])
def test_display_registration_failure_prevents_input_hooks(platform, request):
    backend, api = request.getfixturevalue(platform)
    observer = Observer(lambda: None)
    observer.start = Mock(side_effect=OSError)
    observer.close = Mock()
    backend.display_monitor_factory = Mock(return_value=observer)
    with pytest.raises(PermissionError):
        backend.start(backend.sink)
    observer.close.assert_called_once()
    assert backend.display_monitor is None and not backend.worker.is_alive()
    (api.SetWindowsHookExW if platform == "win" else api.CGEventTapCreate).assert_not_called()


@pytest.mark.parametrize("platform", ["win", "mac"])
@pytest.mark.parametrize("failed", ["device_monitor", "display_monitor"])
def test_both_observers_and_input_are_cleaned_up_even_if_one_cannot_unregister(platform, failed, request):
    backend, api = request.getfixturevalue(platform)
    backend.display_monitor_factory = backend.monitor_factory = Observer
    backend.start(backend.sink)
    observer = getattr(backend, failed)
    observer.close = Mock(side_effect=OSError)
    backend.close()
    assert getattr(backend, failed) is observer
    other = "device_monitor" if failed == "display_monitor" else "display_monitor"
    assert getattr(backend, other) is None
    assert not backend.worker.is_alive() and not backend.captured
    assert api.UnhookWindowsHookEx.call_count == 2 if platform == "win" else api.CFMachPortInvalidate.called
    with pytest.raises(RuntimeError, match="observador"):
        backend.start(backend.sink)


@pytest.mark.parametrize("source", [0, 1])
def test_observed_geometry_does_not_poll_native_display_apis_per_tick(services, source, mocker):
    connected(services)
    peer = services[source]
    for endpoint in services:
        endpoint.backend.display_events = True
    geometry = mocker.PropertyMock(side_effect=AssertionError("No periodic geometry reads"))
    mocker.patch.object(type(peer.backend), "desktop", geometry, create=True)
    for _ in range(100):
        peer.next_geometry = 0
        peer._tick()
    geometry.assert_not_called()
    assert peer._available()


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("changed", [0, 1])
def test_display_event_releases_active_input_and_reconnects_in_both_directions(services, source, changed):
    connected(services)
    peer, target = services[source], services[1 - source]
    enter(peer)
    assert peer.key(0xE0, 1)
    assert peer.button(0, 1)
    until(lambda: target.session.keys == {0xE0} and target.session.buttons == {0})
    invalid = services[changed]
    invalid.backend.display_invalidated = True
    invalid.fail("display")
    assert not invalid._available()
    for endpoint in services:
        assert endpoint.channel.finished.wait(2)
        assert not endpoint.backend.captured
        assert not endpoint.session.keys and not endpoint.session.buttons
    assert invalid.channel.failure == "display"
    assert any(p.kind == Kind.KEY and p.values == (0xE0, 0) for p in target.backend.events)
    assert any(p.kind == Kind.BUTTON and p.values == (0, 0) for p in target.backend.events)
    # New geometry must reach both peers when the single initiator reconnects.
    invalid.backend.display_invalidated = False
    invalid.backend.desktop = Desktop([Display(-400, 0, 1600, 900)])
    connected(services)
    until(lambda: all(endpoint._available() for endpoint in services))
    assert services[1 - changed].remote.displays == invalid.local.displays
    edge = peer.local.right if peer.role == "windows" else peer.local.left
    direction = 1 if peer.role == "windows" else -1
    assert not peer.motion(edge - direction * 100, 250, direction * 10, 0)
    assert peer.motion(edge, 250, direction * 100, 0)
    until(lambda: peer.session.phase == "sending")
    assert target.session.phase == "receiving"


def test_early_display_invalidation_is_not_mislabeled_as_permission_failure(services):
    connected(services)
    peer = services[0]
    peer.backend.display_invalidated = True
    peer.backend.ready = lambda: False
    peer._tick()
    assert peer.channel.finished.wait(1)
    assert peer.channel.failure == "display"  # Never mislabeled as permission.


@pytest.mark.parametrize("changed", [0, 1])
def test_geometry_change_during_authenticated_upgrade_rejects_stale_offer(services, changed):
    peer = services[changed]
    start = peer.backend.start

    def reconfigure(sink):
        peer.backend.desktop = Desktop([Display(0, 0, 1280, 720)])
        start(sink)

    peer.backend.start = reconfigure
    try:
        services[0].connect()
    except ConnectionError:
        pass
    until(lambda: all(endpoint.backend.starts == endpoint.backend.closes for endpoint in services))
    assert not peer._available() and peer.channel is None
    peer.backend.start = start
    connected(services)
    until(lambda: all(endpoint._available() for endpoint in services))
    assert services[1 - changed].remote.displays == peer.local.displays


@pytest.mark.parametrize("platform", ["win", "mac"])
def test_display_invalidation_during_registration_survives_until_cleanup(platform, request):
    backend, _ = request.getfixturevalue(platform)

    class EarlyObserver(Observer):
        def start(self):
            super().start()
            self.notify()

    backend.display_monitor_factory = EarlyObserver
    backend.start(backend.sink)
    assert backend.display_events and backend.display_invalidated
    assert not backend.ready()
    backend.sink.fail.assert_called_once_with("display")
    backend.close()
    assert not backend.display_invalidated


@pytest.mark.parametrize("platform", ["win", "mac"])
def test_monitor_removal_restores_pointer_inside_remaining_screen_using_new_geometry(platform, request):
    backend, api = request.getfixturevalue(platform)
    backend.display_monitor_factory = Observer
    backend.start(backend.sink)
    backend.capture(True)
    assert backend.captured
    backend._desktop = Desktop([Display(-2000, 0, 1000, 900)])
    backend.display_monitor.notify()
    backend.capture(False)
    assert not backend.captured and not backend.hidden and backend.restore_pending is None
    if platform == "win":
        assert api.inputs[-1].mouse.x == 65535
        assert backend.pointer_desktop.displays == backend._desktop.displays
    else:
        assert api.warps[-1] == (-1001, 300)


@pytest.mark.parametrize("changed", [0, 1])
def test_display_event_wakes_a_channel_with_local_focus_without_waiting_for_heartbeat(services, changed):
    connected(services)
    until(lambda: all(endpoint.channel.worker is not None for endpoint in services))
    peer = services[changed]
    assert peer.session.phase == "local" and not peer.channel.active.is_set()
    peer.backend.display_invalidated = True
    peer.fail("display")
    assert peer.channel.finished.wait(1)  # The idle heartbeat otherwise waits up to 15 seconds.
    assert peer.channel.failure == "display"
