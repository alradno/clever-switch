"""Current input streams outrank older readiness RPCs; native APIs stay fake."""

import pytest

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button
from .test_input_runtime import online
from .test_input_stream import until


def runtime_for(peer, tmp_path):
    runtime = online(tmp_path)
    runtime.input_service = peer
    runtime.input_peer = True
    runtime.input_last_ready = True
    runtime.input_seen_channel = peer.channel
    runtime.peer_info["input"]["available"] = False
    return runtime


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("stale_side", [0, 1])
@pytest.mark.parametrize("active", [False, True])
def test_authenticated_stream_survives_stale_readiness_and_keeps_input_in_both_directions(
    native_peers, tmp_path, source, stale_side, active
):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    runtime = runtime_for(peers[stale_side], tmp_path)
    channels = [peer.channel for peer in peers]
    observers = [peer.backend.device_monitor for peer in peers]
    if active:
        enter(sender)
        key(sender, 0xE1, 1)
        button(sender, 0, 1)
        until(lambda: receiver.session.keys == {0xE1} and receiver.session.buttons == {0})
    for _ in range(4):
        wait = runtime._input_step()
        assert wait is None or wait > 0  # No immediate polling or retry loop.
        assert all(peer._available() for peer in peers)
        assert runtime.input_error is None
        assert runtime.input_budget.next is None
    assert all(peer.channel is channel for peer, channel in zip(peers, channels, strict=True))
    assert all(peer.backend.device_monitor is observer for peer, observer in zip(peers, observers, strict=True))
    if active:
        assert receiver.session.keys == {0xE1} and receiver.session.buttons == {0}
        button(sender, 0, 0)
        key(sender, 0xE1, 0)
    else:
        enter(sender)
    key(sender, 4, 1)
    key(sender, 4, 0)
    x, y = sender.backend.anchor
    motion(sender, int(x + (50 if source == 0 else -50)), int(y))
    until(lambda: not receiver.session.keys and not receiver.session.buttons)
    until(lambda: receiver.backend.previous == pytest.approx(sender.remote_position))
    assert sender.session.phase == "sending" and receiver.session.phase == "receiving"
    assert sender.session.leave()
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert all(peer._available() for peer in peers)


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("unavailable_side", [0, 1])
def test_current_local_readiness_failure_still_releases_both_devices(
    native_peers, tmp_path, source, unavailable_side
):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    key(sender, 0xE1, 1)
    button(sender, 0, 1)
    until(lambda: receiver.session.keys == {0xE1} and receiver.session.buttons == {0})
    peer = peers[unavailable_side]
    runtime = runtime_for(peer, tmp_path)
    peer.backend.health.check = lambda: False
    peer.backend.health._refresh()
    runtime._input_step()
    until(lambda: all(endpoint.channel.finished.is_set() for endpoint in peers))
    assert runtime.input_last_ready is False and runtime.input_budget.next is None
    assert "este equipo" in runtime.input_error
    assert all(not endpoint.session.keys and not endpoint.session.buttons for endpoint in peers)
    assert all(not endpoint.backend.captured and not endpoint.backend.hidden for endpoint in peers)


@pytest.mark.parametrize("role", ["windows", "mac"])
def test_stale_unavailable_snapshot_without_ready_stream_never_starts_input(tmp_path, role):
    runtime = online(tmp_path)
    runtime.input_service.role = role
    runtime.peer_info["input"]["available"] = False
    for _ in range(4):
        runtime._input_step()
    runtime.input_service.connect.assert_not_called()
    assert runtime.input_budget.next is None
    assert "el otro equipo" in runtime.input_error
