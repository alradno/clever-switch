"""Local and remote click sequences through fake OS APIs and loopback only."""

import ctypes as c
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_keys import EVENT_TAG
from cleverswitch.flow.input_protocol import Kind, Packet
from cleverswitch.flow.input_windows import MouseHook, Point

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button
from .test_input_native import mac as mac
from .test_input_stream import until


def remote_click(backend, point=(500, 300), code=0):
    backend.inject(Packet(Kind.MOVE, 1, point))
    backend.inject(Packet(Kind.BUTTON, 1, (code, 1)))
    backend.inject(Packet(Kind.BUTTON, 1, (code, 0)))


def local_click(backend, point=(900, 700), code=0, count=1, tag=0):
    q = backend.q
    raw = {
        "position": point,
        q.kCGMouseEventButtonNumber: code,
        q.kCGMouseEventClickState: count,
        q.kCGEventSourceUserData: tag,
    }
    kinds = (
        (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp),
        (q.kCGEventRightMouseDown, q.kCGEventRightMouseUp),
        (q.kCGEventOtherMouseDown, q.kCGEventOtherMouseUp),
    )[min(code, 2)]
    return [backend._callback(None, kind, raw, None) for kind in kinds]


@pytest.mark.parametrize("local_button", range(5))
def test_local_click_elsewhere_breaks_the_previous_remote_double_click(mac, local_button):
    backend, q = mac
    backend.double_click = lambda: 30  # Deterministic even on a slow CI worker.
    remote_click(backend)
    local_click(backend, code=local_button)
    remote_click(backend)
    assert [e[q.kCGMouseEventClickState] for e in q.events if q.kCGMouseEventClickState in e] == [1, 1, 1, 1]


@pytest.mark.parametrize("native_count", [1, 2, 3])
def test_remote_click_continues_the_actual_local_click_count(mac, native_count):
    backend, q = mac
    local_click(backend, point=(500, 300), count=native_count)
    remote_click(backend)
    assert q.events[-1][q.kCGMouseEventClickState] == min(3, native_count + 1)


@pytest.mark.parametrize("ignored", ["tagged", "forwarded"])
def test_events_not_delivered_locally_do_not_replace_local_click_history(mac, ignored):
    backend, q = mac
    remote_click(backend)
    if ignored == "forwarded":
        backend.sink.button.return_value = True
    local_click(backend, tag=EVENT_TAG if ignored == "tagged" else 0)
    backend.sink.button.return_value = False
    remote_click(backend)
    assert q.events[-1][q.kCGMouseEventClickState] == 2


@pytest.mark.parametrize("previous", ["local", "remote"])
def test_click_sequence_expires_using_the_same_monotonic_clock(mac, previous, monkeypatch):
    backend, q = mac
    now = [1000.0]
    backend.clock = lambda: now[0]
    monkeypatch.setattr("cleverswitch.flow.input_macos.time", SimpleNamespace(monotonic=backend.clock))
    local_click(backend, point=(500, 300)) if previous == "local" else remote_click(backend)
    now[0] += backend.double_click() + 0.01
    remote_click(backend)
    assert q.events[-1][q.kCGMouseEventClickState] == 1


def test_closing_observation_discards_clicks_before_the_unobserved_interval(mac):
    backend, q = mac
    remote_click(backend)
    backend.close()
    remote_click(backend)
    assert q.events[-1][q.kCGMouseEventClickState] == 1


@pytest.mark.parametrize("source", [0, 1])
def test_local_click_takeover_then_remote_click_keeps_native_action_and_same_link(native_peers, source):
    peers, windows_api, mac_api = native_peers
    sender, receiver = peers[source], peers[1 - source]

    def count():
        if source == 0:
            return sum(e.get("kind") == mac_api.kCGEventLeftMouseUp for e in mac_api.events)
        return sum(e.kind == 0 and e.mouse.flags == 4 for e in windows_api.inputs)

    if source == 0:
        receiver.backend.double_click = lambda: 30
    enter(sender)
    channels = [peer.channel for peer in peers]
    for down in (1, 0):
        button(sender, 0, down)
    until(lambda: count() == 1)
    if source == 0:
        assert all(result is not None for result in local_click(receiver.backend))
    else:
        for down in (1, 0):
            assert not button(receiver, 0, down)
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    enter(sender)
    for down in (1, 0):
        button(sender, 0, down)
    until(lambda: count() == 2)
    if source == 0:
        clicks = [e for e in mac_api.events if e.get("kind") in (1, 2)]
        assert [e[mac_api.kCGMouseEventClickState] for e in clicks] == [1, 1, 1, 1]
    else:
        clicks = [e for e in windows_api.inputs if e.kind == 0 and e.mouse.flags in (2, 4)]
        assert [e.mouse.flags for e in clicks] == [2, 4, 2, 4]
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))
    assert not receiver.session.buttons and not receiver.backend.held_buttons


def physical_takeover(peer, action, point=(900, 700)):
    if action == "motion":
        return motion(peer, *point)
    if peer.role == "windows":
        raw = MouseHook(Point(*point), 120 << 16 if action == "wheel" else 0, 0, 0, 0)
        return peer.backend._mouse(0, 0x204 if action == "button" else 0x20A, c.addressof(raw))
    q = peer.backend.q
    kind = q.kCGEventRightMouseDown if action == "button" else q.kCGEventScrollWheel
    return peer.backend._callback(None, kind, {"position": point, q.kCGScrollWheelEventFixedPtDeltaAxis1: 1}, None)


@pytest.mark.parametrize("action", ["motion", "button", "wheel"])
@pytest.mark.parametrize("held_button", range(5))
def test_mac_local_mouse_input_releases_remote_buttons_at_the_local_position(mac, action, held_button):
    backend, q = mac
    backend.inject(Packet(Kind.MOVE, 1, (500, 300)))
    backend.inject(Packet(Kind.BUTTON, 1, (held_button, 1)))

    def cancel(*args):
        backend.inject(Packet(Kind.BUTTON, 1, (held_button, 0)))
        return False

    getattr(backend.sink, action).side_effect = cancel
    if action == "motion":
        kind = q.kCGEventMouseMoved
    elif action == "wheel":
        kind = q.kCGEventScrollWheel
    else:
        kind = q.kCGEventRightMouseDown if held_button == 0 else q.kCGEventLeftMouseDown
    point = (900, 700)
    raw = {"position": point, q.kCGScrollWheelEventFixedPtDeltaAxis1: 1}
    assert backend._callback(None, kind, raw, None) is raw
    assert q.events[-1]["position"] == point
    assert q.events[-1]["kind"] in (q.kCGEventLeftMouseUp, q.kCGEventRightMouseUp, q.kCGEventOtherMouseUp)
    assert not backend.injected_buttons


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("action", ["motion", "button", "wheel"])
def test_local_mouse_takeover_releases_drag_without_warping_back_over_both_transports(native_peers, source, action):
    peers, windows_api, mac_api = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    button(sender, 0, 1)
    until(lambda: receiver.session.buttons == {0})
    windows_moves = len([e for e in windows_api.inputs if e.kind == 0 and e.mouse.flags & 1])
    point = (900, 700)
    physical_takeover(receiver, action, point)
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    if source == 0:
        releases = [e for e in mac_api.events if e.get("kind") == mac_api.kCGEventLeftMouseUp]
        assert releases[-1]["position"] == point
    else:
        # Windows button-up has no coordinates. Cancellation must not add a
        # mouse move at the previous remote position to this local event.
        assert len([e for e in windows_api.inputs if e.kind == 0 and e.mouse.flags & 1]) == windows_moves
        assert windows_api.inputs[-1].mouse.flags == 4
    assert not receiver.session.buttons
    assert all(peer._available() for peer in peers)


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("action", ["motion", "button", "wheel"])
def test_queued_local_takeover_restores_its_position_after_an_inflight_remote_move(native_peers, source, action):
    peers, windows_api, mac_api = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    button(sender, 0, 1)
    key(sender, 0xE1, 1)
    until(lambda: receiver.session.buttons == {0} and receiver.session.keys == {0xE1})
    started, release = threading.Event(), threading.Event()
    inject = receiver.backend.inject
    restore = receiver.backend.restore
    restored = []

    def after_release(point):
        if point == (900, 700):
            assert not receiver.session.buttons and not receiver.session.keys
            restored.append(point)
        return restore(point)

    receiver.backend.restore = after_release

    def paused(packet):
        if packet.kind == Kind.MOVE and not started.is_set():
            started.set()
            assert release.wait(1)
        return inject(packet)

    receiver.backend.inject = paused
    try:
        x, y = sender.backend.anchor
        motion(sender, int(x + (100 if source == 0 else -100)), int(y))
        assert started.wait(1)
        physical_takeover(receiver, action)
        assert receiver.deferred.qsize() == 1
    finally:
        release.set()
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    receiver.backend.inject = inject
    receiver.backend.restore = restore
    if source == 0:
        releases = [e for e in mac_api.events if e.get("kind") == mac_api.kCGEventLeftMouseUp]
        assert releases[-1]["position"] == (900, 700)
        assert mac_api.position == (900, 700)
    else:
        moves = [e for e in windows_api.inputs if e.kind == 0 and e.mouse.flags & 1]
        assert (moves[-1].mouse.x * 1920 // 65536, moves[-1].mouse.y * 1080 // 65536) == (900, 700)
    assert receiver.backend.previous == (900, 700)
    assert restored[-1] == (900, 700)
    assert not receiver.session.buttons
    assert all(peer._available() for peer in peers)


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("stage", ["prepare", "restore"])
def test_takeover_failure_still_releases_remote_input_and_local_capture(native_peers, source, stage):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    button(sender, 0, 1)
    key(sender, 0xE1, 1)
    until(lambda: receiver.session.buttons == {0} and receiver.session.keys == {0xE1})

    def refused(*args):
        raise OSError("Simulated native refusal")

    if stage == "prepare":
        receiver.backend.local_takeover = refused
    else:
        receiver.backend.local_takeover = lambda: (900, 700)
        receiver.backend.restore = refused
    physical_takeover(receiver, "motion")
    until(lambda: all(peer.channel.finished.is_set() for peer in peers))
    assert receiver.channel.failure == "native"
    assert all(not peer.session.keys and not peer.session.buttons for peer in peers)
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)


@pytest.mark.parametrize("source", [0, 1])
def test_failed_button_release_prevents_pointer_restoration_until_cleanup(native_peers, source):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    button(sender, 0, 1)
    until(lambda: receiver.session.buttons == {0})
    inject, failed = receiver.backend.inject, []

    def refused_once(packet):
        if packet.kind == Kind.BUTTON and packet.values == (0, 0) and not failed:
            failed.append(True)
            raise OSError("Simulated release refusal")
        return inject(packet)

    receiver.backend.inject = refused_once
    receiver.backend.local_takeover = lambda: (900, 700)
    receiver.backend.restore = Mock()
    physical_takeover(receiver, "motion")
    until(lambda: all(peer.channel.finished.is_set() for peer in peers))
    receiver.backend.restore.assert_not_called()
    assert receiver.channel.failure == "cleanup"
    assert failed and receiver.session.unreleased == {(Kind.BUTTON, 0)}
    # The runtime retries this retained ownership before admitting a new
    # stream. A closed channel must not silently forget the failed button-up.
    assert receiver.session.retry_releases()
    assert not receiver.session.unreleased and not receiver.session.buttons
    receiver.backend.restore.assert_not_called()
    assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
