"""Display recovery through native adapters; OS APIs are fake, TLS/UDP are real."""

import ctypes as c
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_keys import MAC, WINDOWS
from cleverswitch.flow.input_windows import Rect

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_availability import runtime_for
from .test_input_displays import Observer
from .test_input_end_to_end import key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button
from .test_input_stream import until


def reconnect(peers, mac_api):
    # The fake CFRunLoop's stop latch must be reset before a new fake run.
    # Real Quartz run-loop restart is a separate physical acceptance check.
    mac_api.stop.clear()
    assert peers[0].connect(peers[1].capabilities())
    until(lambda: all(peer._available() for peer in peers))
    if peers[0].channel.lane is not None:
        until(lambda: all(peer.channel.lane.probe.state == "ready" for peer in peers))


@pytest.fixture
def observed_peers(native_peers):
    peers, windows_api, mac_api = native_peers
    for peer in peers:
        peer.suspend()
    until(lambda: all(peer.channel.finished.is_set() for peer in peers))
    screens = [[Display(0, 0, 1920, 1080)] for _ in peers]

    def enumerate_windows(dc, clip, callback, data):
        for index, display in enumerate(screens[0]):
            rect = Rect(display.x, display.y, display.x + display.width, display.y + display.height)
            assert callback(index + 1, None, c.pointer(rect), data)
        return 1

    def mac_bounds(identifier):
        display = screens[1][identifier - 1]
        return SimpleNamespace(
            origin=SimpleNamespace(x=display.x, y=display.y),
            size=SimpleNamespace(width=display.width, height=display.height),
        )

    windows_api.EnumDisplayMonitors.side_effect = enumerate_windows
    mac_api.CGGetActiveDisplayList = Mock(side_effect=lambda *args: (0, range(1, len(screens[1]) + 1), len(screens[1])))
    mac_api.CGDisplayBounds = Mock(side_effect=mac_bounds)
    for peer in peers:
        peer.backend._desktop = None  # Exercise both real geometry readers.
        peer.backend.display_monitor_factory = Observer
    reconnect(peers, mac_api)
    try:
        yield peers, windows_api, mac_api, screens
    finally:
        for peer in peers:
            peer.suspend()
            peer.backend.close()  # Reclaim resources even when testing a failed app cleanup.


def enter_here(peer):
    desktop = peer.local
    edge = desktop.right if peer.role == "windows" else desktop.left
    inward = -100 if peer.role == "windows" else 100
    point = desktop.entry(peer.side, 0.5)
    motion(peer, int(edge + inward), int(point[1]))
    motion(peer, int(edge), int(point[1]))
    until(lambda: peer.session.phase == "sending")


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("changed", [0, 1], ids=["windows_display", "mac_display"])
@pytest.mark.parametrize("layout", ["resize", "relocate", "add_monitor"])
def test_display_change_releases_native_input_and_reconnects_with_current_coordinates(
    observed_peers, source, changed, layout,
):
    peers, windows_api, mac_api, screens = observed_peers
    sender, receiver = peers[source], peers[1 - source]
    enter_here(sender)
    key(sender, 0xE0, 1)
    button(sender, 0, 1)
    until(lambda: receiver.session.keys == {0xE0} and receiver.session.buttons == {0})
    old_channels = [peer.channel for peer in peers]
    old_observers = [peer.backend.display_monitor for peer in peers]
    new_layout = {
        "resize": [Display(0, 0, 1280, 720)],
        "relocate": [Display(-1400, -500, 1000, 700)],
        "add_monitor": [Display(0, 0, 1920, 1080), Display(-1280, 100, 1280, 720)],
    }[layout]
    screens[changed] = new_layout
    old_observers[changed].notify()
    until(lambda: all(channel.finished.is_set() for channel in old_channels))
    assert old_channels[changed].failure == "display"
    assert all(not peer.session.keys and not peer.session.buttons for peer in peers)
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
    assert all(not peer.session.unreleased and not peer.session.pending_capture for peer in peers)
    if receiver.role == "mac":
        assert any(e.get("kind") == mac_api.kCGEventKeyUp and e[9] == MAC[0xE0] for e in mac_api.events)
        assert any(e.get("kind") == mac_api.kCGEventLeftMouseUp for e in mac_api.events)
    else:
        assert any(e.kind == 1 and e.key.scan == WINDOWS[0xE0] and e.key.flags & 2 for e in windows_api.inputs)
        assert any(e.kind == 0 and e.mouse.flags == 4 for e in windows_api.inputs)
    if changed == source:
        desktop = Desktop(new_layout)
        if source == 0:
            mouse = next(e.mouse for e in reversed(windows_api.inputs) if e.kind == 0 and e.mouse.flags == 0xE001)
            point = (
                desktop.left + mouse.x * (desktop.right - desktop.left + 1) // 65536,
                desktop.top + mouse.y * (desktop.bottom - desktop.top + 1) // 65536,
            )
        else:
            point = mac_api.warps[-1]
        assert desktop.clamp(*point) == point

    reconnect(peers, mac_api)
    assert peers[changed].local.displays == tuple(new_layout)
    assert peers[1 - changed].remote.displays == tuple(new_layout)
    for observer in old_observers:
        observer.changed()  # Notification already dispatched before unregister.
    assert all(peer._available() for peer in peers)
    assert all(peer.channel is not old for peer, old in zip(peers, old_channels, strict=True))
    # A fresh crossing must use both the new edge and the receiver's new units.
    enter_here(sender)
    x, y = sender.backend.anchor
    motion(sender, int(x + (80 if source == 0 else -80)), int(y))
    key(sender, 4, 1)
    until(lambda: receiver.session.keys == {4})
    key(sender, 4, 0)
    until(lambda: not receiver.session.keys)
    until(lambda: receiver.backend.previous == pytest.approx(sender.remote_position, abs=1))
    assert sender.session.leave()
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert all(peer._available() for peer in peers)


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("checkpoint", ["background_stopped", "readiness_recovers"])
@pytest.mark.parametrize("resume", ["availability", "handshake"])
def test_temporary_missing_displays_finish_cleanup_and_resume_on_next_availability(
    observed_peers, tmp_path, source, checkpoint, resume,
):
    peers, _, mac_api, screens = observed_peers
    sender, receiver = peers[source], peers[1 - source]
    runtime = runtime_for(sender, tmp_path)
    runtime.peer_info["input"]["available"] = True
    sender.on_change = Mock()
    enter_here(sender)
    key(sender, 0xE0, 1)
    button(sender, 0, 1)
    until(lambda: receiver.session.keys == {0xE0} and receiver.session.buttons == {0})
    try:
        # During mode changes the OS can temporarily report no active display.
        # Cursor restoration must remain pending, but health polling must stop.
        screens[source] = []
        sender.backend.display_monitor.notify()
        until(lambda: all(peer.channel.finished.is_set() for peer in peers))
        assert not sender.backend.captured and not sender.backend.hidden
        assert sender.backend.restore_pending is not None
        if checkpoint == "background_stopped":
            assert not sender.backend.health.worker.is_alive()
        assert sender.diagnostic()["release_pending"]
        assert not receiver.session.keys and not receiver.session.buttons
        wait = runtime._input_step()
        assert wait is None or wait > 0
        assert runtime.input_budget.next is None
        assert runtime.input_last_ready is False
    finally:
        screens[source] = [Display(-1400, -500, 1000, 700)]
    # A later availability check completes the retained cleanup. A failed close
    # must not be remembered as successful and strand display_invalidated.
    if resume == "availability":
        runtime._input_step()
    else:
        sender.offer()
    assert sender.backend.ready() and not sender.backend.display_invalidated
    assert sender.backend.restore_pending is None
    sender.on_change.assert_called_once()
    reconnect(peers, mac_api)
    assert not sender.diagnostic()["release_pending"]
    enter_here(sender)
    key(sender, 4, 1)
    until(lambda: receiver.session.keys == {4})
    key(sender, 4, 0)
    until(lambda: not receiver.session.keys)

