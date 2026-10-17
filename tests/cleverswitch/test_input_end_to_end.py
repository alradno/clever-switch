"""Real mutual TLS between public-API adapters, with only fake OS surfaces.

The whole callback -> routing -> TLS -> injection path runs in both directions.
No real hooks, event taps, SendInput or CGEventPost are called by these tests.
"""

import ctypes as c
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_keys import MAC
from cleverswitch.flow.input_macos import MacInput
from cleverswitch.flow.input_service import Service
from cleverswitch.flow.input_windows import KeyHook, MouseHook, Point, WindowsInput
from cleverswitch.flow.network import Link

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_native import Quartz, User32
from .test_input_stream import until


@pytest.fixture
def native_peers(paired_tls):
    windows_api, mac_api = User32(), Quartz()
    windows_api.CallNextHookEx.return_value = 0
    kernel = Mock()
    kernel.GetCurrentThreadId.return_value = 10
    kernel.GetModuleHandleW.return_value = 100
    desktop = Desktop([Display(0, 0, 1920, 1080)])
    backends = [
        WindowsInput(windows_api, kernel, ready=lambda: True, desktop=desktop),
        MacInput(mac_api, ready=lambda: True, secure_input=lambda: False, desktop=desktop),
    ]
    links = [Link(pairing, lambda op, args: {}, 0) for pairing in paired_tls]
    peers = [Service(link, backend, enabled=True) for link, backend in zip(links, backends, strict=True)]
    try:
        for link in links:
            link.start()
        links[0].port, links[1].port = links[1].port, links[0].port
        peers[0].connect()
        until(lambda: peers[1].channel is not None)
        yield peers, windows_api, mac_api
    finally:
        for peer in peers:
            peer.close()
        for link in links:
            link.close()


def motion(peer, x, y):
    backend = peer.backend
    if peer.role == "windows":
        raw = MouseHook(Point(x, y), 0, 0, 0, 0)
        return backend._mouse(0, 0x200, c.addressof(raw))
    q = backend.q
    if not backend.captured:
        q.position = x, y
    return backend._callback(None, q.kCGEventMouseMoved, {"position": (x, y)}, None)


def key(peer, usage, down):
    backend = peer.backend
    if peer.role == "windows":
        from cleverswitch.flow.input_keys import WINDOWS

        scan = WINDOWS[usage]
        raw = KeyHook(0, scan & 0xFF, (1 if scan & 0x100 else 0) | (0 if down else 0x80), 0, 0)
        return backend._key(0, 0x100 if down else 0x101, c.addressof(raw))
    q = backend.q
    return backend._callback(None, q.kCGEventKeyDown if down else q.kCGEventKeyUp, {9: MAC[usage]}, None)


def enter(peer):
    x = 1919 if peer.role == "windows" else 0
    inward = -100 if peer.role == "windows" else 100
    motion(peer, x + inward, 400)
    motion(peer, x, 400)
    until(lambda: peer.session.phase == "sending")


@pytest.mark.parametrize("source", [0, 1])
def test_full_public_api_adapters_cross_type_click_and_return_over_tls(native_peers, source):
    peers, windows_api, mac_api = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    assert sender.backend.captured and receiver.session.phase == "receiving"
    direction = 1 if source == 0 else -1
    x, y = sender.backend.anchor
    motion(sender, int(x + direction * 100), int(y))
    key(sender, 4, 1)
    key(sender, 4, 0)
    if source == 0:
        raw = MouseHook(Point(int(x), int(y)), 0, 0, 0, 0)
        sender.backend._mouse(0, 0x201, c.addressof(raw))
        sender.backend._mouse(0, 0x202, c.addressof(raw))
        until(lambda: any(e.get("kind") == mac_api.kCGEventLeftMouseUp for e in mac_api.events))
        assert [e[9] for e in mac_api.events if e.get("kind") == mac_api.kCGEventKeyDown] == [MAC[4]]
    else:
        sender.backend._callback(None, mac_api.kCGEventLeftMouseDown, {}, None)
        sender.backend._callback(None, mac_api.kCGEventLeftMouseUp, {}, None)
        until(lambda: any(e.kind == 0 and e.mouse.flags == 4 for e in windows_api.inputs))
        assert any(e.kind == 1 and e.key.scan == 30 and e.key.flags == 8 for e in windows_api.inputs)
        assert any(e.kind == 1 and e.key.scan == 30 and e.key.flags == 10 for e in windows_api.inputs)
    motion(sender, int(x - direction * 200), int(y))
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
    assert all(not peer.session.keys and not peer.session.buttons for peer in peers)


def test_full_path_secure_input_change_releases_modifier_and_restores_windows_cursor(native_peers):
    peers, windows_api, mac_api = native_peers
    sender, receiver = peers
    enter(sender)
    key(sender, 0xE1, 1)
    until(lambda: receiver.session.keys == {0xE1})
    receiver.backend.secure_input = lambda: True
    receiver.next_check = 0
    receiver.channel._wake()
    until(lambda: all(peer.channel.finished.is_set() for peer in peers))
    assert not receiver.session.keys
    assert not mac_api.flags & (1 << 17)
    assert not sender.backend.captured and not sender.backend.hidden
    assert [visible for _, visible in windows_api.visibility] == [False, True]


def test_full_path_windows_denied_injection_never_keeps_mac_captured(native_peers):
    peers, windows_api, mac_api = native_peers
    windows_api.SendInput.side_effect = None
    windows_api.SendInput.return_value = 0
    sender = peers[1]
    motion(sender, 100, 400)
    motion(sender, 0, 400)
    until(lambda: all(peer.channel.finished.is_set() for peer in peers))
    assert not sender.backend.captured and not sender.backend.hidden
    assert sender.session.phase == "local"
    assert windows_api.SendInput.call_count == 1
