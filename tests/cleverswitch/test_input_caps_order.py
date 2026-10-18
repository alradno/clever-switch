"""Caps state across queued native posts and quick crossings; fake OS APIs only."""

import pytest

from cleverswitch.flow.input_keys import MAC, WINDOWS
from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key
from .test_input_end_to_end import native_peers as native_peers
from .test_input_native import event
from .test_input_native import mac as mac
from .test_input_stream import until


@pytest.mark.parametrize("initial", [False, True])
def test_mac_caps_sync_uses_ordered_state_before_window_server_applies_posts(mac, monkeypatch, initial):
    backend, api = mac
    api.flags = api.kCGEventFlagMaskAlphaShift if initial else 0
    monkeypatch.setattr(api, "CGEventPost", lambda tap, value: api.events.append(value.copy()))
    expected = initial
    for _ in range(4):
        expected = not expected
        for code in backend.lock_keys(int(expected), 1):
            backend.inject(event(Kind.KEY, code, 1))
            backend.inject(event(Kind.KEY, code, 0))
        assert backend.lock_state() == (int(expected), 1)
        assert backend.lock_keys(int(expected), 1) == ()
        backend.inject(event(Kind.KEY, 4, 1))
        backend.inject(event(Kind.KEY, 4, 0))
        assert bool(api.events[-1]["flags"] & api.kCGEventFlagMaskAlphaShift) == expected
        assert bool(api.flags & api.kCGEventFlagMaskAlphaShift) == initial


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("failure", ["empty", "create", "metadata", "post"])
@pytest.mark.parametrize("queued", [False, True])
def test_failed_mac_caps_event_creation_preserves_last_confirmed_state(mac, monkeypatch, initial, failure, queued):
    backend, api = mac
    api.flags = api.kCGEventFlagMaskAlphaShift if initial else 0
    backend.caps = initial
    if queued:
        monkeypatch.setattr(api, "CGEventPost", lambda tap, value: api.events.append(value.copy()))
        backend.inject(event(Kind.KEY, 57, 1))
        backend.inject(event(Kind.KEY, 57, 0))
    confirmed = backend.injected_caps, backend.caps
    state = backend.lock_state()
    posted = len(api.events)

    def fail(*args):
        raise OSError("Simulated native failure")

    method = {
        "empty": "CGEventCreateKeyboardEvent", "create": "CGEventCreateKeyboardEvent",
        "metadata": "CGEventSetType", "post": "CGEventPost",
    }[failure]
    monkeypatch.setattr(api, method, (lambda *args: None) if failure == "empty" else fail)
    with pytest.raises((PermissionError, OSError)):
        backend.inject(event(Kind.KEY, 57, 1))
    assert len(api.events) == posted
    assert (backend.injected_caps, backend.caps) == confirmed
    assert backend.lock_state() == state


@pytest.mark.parametrize("source", [0, 1], ids=["windows_first", "mac_first"])
@pytest.mark.parametrize("initial", [False, True])
def test_quick_crossing_preserves_caps_after_remote_toggle_with_queued_mac_posts(
    native_peers, monkeypatch, source, initial,
):
    peers, windows_api, mac_api = native_peers
    sender, receiver = peers[source], peers[1 - source]
    mac_peer = peers[1]
    mac_api.flags = mac_api.kCGEventFlagMaskAlphaShift if initial else 0
    mac_peer.backend.caps = initial
    monkeypatch.setattr(mac_api, "CGEventPost", lambda tap, value: mac_api.events.append(value.copy()))
    windows_caps, windows_held, typed_caps = [initial], set(), []
    submit = windows_api.submit

    def apply(count, pointer, size):
        result = submit(count, pointer, size)
        entry = windows_api.inputs[-1]
        if entry.kind == 1:
            scan, down = entry.key.scan, not bool(entry.key.flags & 2)
            if scan == WINDOWS[57] and down and scan not in windows_held:
                windows_caps[0] = not windows_caps[0]
            if scan == WINDOWS[4] and down:
                typed_caps.append(windows_caps[0])
            windows_held.add(scan) if down else windows_held.discard(scan)
        return result

    windows_api.SendInput.side_effect = apply
    windows_api.GetKeyState.side_effect = lambda vk: int(windows_caps[0]) if vk == 0x14 else 0
    enter(sender)
    expected = not initial
    if source == 0:
        key(sender, 57, 1)
        key(sender, 57, 0)
        until(lambda: mac_peer.backend.injected_caps == expected and not receiver.session.keys)
    else:
        flags = mac_api.kCGEventFlagMaskAlphaShift if expected else 0
        sender.backend._callback(None, mac_api.kCGEventFlagsChanged, {9: MAC[57], "flags": flags}, None)
        until(lambda: windows_caps[0] == expected and not receiver.session.keys)
    sender.session.leave()
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert windows_caps[0] == expected
    assert bool(mac_api.flags & mac_api.kCGEventFlagMaskAlphaShift) == initial
    # The Mac becomes the source before its earlier caps posts are applied.
    # Its ordered state must not be replaced by the old WindowServer snapshot.
    enter(mac_peer)
    key(mac_peer, 4, 1)
    key(mac_peer, 4, 0)
    until(lambda: bool(typed_caps) and not peers[0].session.keys)
    assert typed_caps == [expected]
    assert mac_peer.session.outbound_locks == (int(expected), 1)
    assert all(peer._available() for peer in peers)
