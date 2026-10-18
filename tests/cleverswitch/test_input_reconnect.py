"""Repeated stream recovery through real TLS/UDP with fake native OS APIs."""

import threading

import pytest

from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import enter, key, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button
from .test_input_network import Relay
from .test_input_stream import until


@pytest.mark.parametrize("source", [0, 1], ids=["windows_first", "mac_first"])
@pytest.mark.parametrize("cut", [0, 1], ids=["source_closes", "destination_closes"])
@pytest.mark.parametrize("release_failure", [False, True], ids=["released", "release_retry"])
def test_repeated_stream_reconnect_releases_input_and_recovers_both_directions(
    native_peers, source, cut, release_failure,
):
    peers, windows_api, mac_api = native_peers
    hybrid = peers[0].channel.lane is not None
    delivered = [[], []]
    failed_release = [False, False]

    def recorder(index, inject):
        def record(packet):
            if failed_release[index] and packet.kind == Kind.KEY and packet.values == (0xE1, 0):
                failed_release[index] = False
                raise PermissionError("simulated release refusal")
            inject(packet)
            if packet.kind in (Kind.KEY, Kind.BUTTON):
                delivered[index].append((packet.kind, packet.values))

        return record

    for index, peer in enumerate(peers):
        peer.backend.inject = recorder(index, peer.backend.inject)

    for cycle in range(4):
        origin = (source + cycle) % 2
        sender, receiver = peers[origin], peers[1 - origin]
        channels = [peer.channel for peer in peers]
        sessions = [peer.session for peer in peers]
        monitors = [peer.backend.device_monitor for peer in peers]
        workers = [(peer.backend.worker, peer.backend.health.worker) for peer in peers]
        delivered[1 - origin].clear()
        enter(sender)
        expected = []
        for usage in (4, 0xE1, 0xE5):
            key(sender, usage, 1)
            expected.append((Kind.KEY, (usage, 1)))
        for code in (0, 4):
            button(sender, code, 1)
            expected.append((Kind.BUTTON, (code, 1)))
        until(lambda receiver=receiver: receiver.session.keys == {4, 0xE1, 0xE5} and receiver.session.buttons == {0, 4})
        x, y = sender.backend.anchor
        point = (500, 420)
        previous = sender.remote_position
        motion(sender, int(x + point[0] - previous[0]), int(y + point[1] - previous[1]))
        until(lambda receiver=receiver, point=point: receiver.backend.previous == point)
        assert delivered[1 - origin] == expected
        failed_release[1 - origin] = release_failure

        # Closing either TLS endpoint interrupts a live chord and drag. The
        # peer observes EOF; no native key/button-up is sent by the source.
        peers[(origin + cut) % 2].channel.request_close()
        until(lambda channels=channels: all(channel.finished.is_set() for channel in channels))
        assert all(session.closed and session.phase == "local" for session in sessions)
        assert all(not session.keys and not session.buttons for session in sessions)
        assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
        assert all(not peer.started and not peer._available() for peer in peers)
        assert all(not worker.is_alive() for pair in workers for worker in pair)
        assert all(not observer.active for observer in monitors)
        assert all(peer.deferred.empty() for peer in peers)
        assert not any(failed_release)
        assert receiver.session.unreleased == ({(Kind.KEY, 0xE1)} if release_failure else set())
        if hybrid:
            assert all(channel.inbox.closed and not channel.inbox.take() for channel in channels)
            assert all(peer.link.motion_transport.motion.current is None for peer in peers)

        releases = [(Kind.KEY, (usage, 0)) for usage in (4, 0xE1, 0xE5)]
        releases += [(Kind.BUTTON, (code, 0)) for code in (0, 4)]
        if release_failure:
            releases.remove((Kind.KEY, (0xE1, 0)))
        assert delivered[1 - origin] == expected + releases

        # Only the fake CFRunLoop's stop latch needs resetting between calls.
        # Real backends and services are reused; Link performs a fresh pinned
        # TLS upgrade and, when selected, a fresh UDP key negotiation.
        mac_api.stop.clear()
        assert peers[0].connect(peers[1].capabilities() if hybrid else None)
        until(lambda: all(peer._available() for peer in peers))
        if hybrid:
            until(lambda: all(peer.channel.lane.probe.state == "ready" for peer in peers))
        if release_failure:
            releases.append((Kind.KEY, (0xE1, 0)))
        assert delivered[1 - origin] == expected + releases
        assert all(not session.unreleased for session in sessions)
        assert all(peer.channel is not channel for peer, channel in zip(peers, channels, strict=True))
        assert all(peer.session is not session for peer, session in zip(peers, sessions, strict=True))
        assert all(peer.backend.device_monitor.active for peer in peers)
        assert not mac_api.flags & (1 << 17)

        # Late device notifications from the retired observer must not clear
        # fresh physical state or release a press in the replacement stream.
        enter(receiver)
        key(receiver, 5, 1)
        button(receiver, 4, 1)
        until(lambda sender=sender: sender.session.keys == {5} and sender.session.buttons == {4})
        for observer in monitors:
            observer.changed("keyboard")
            observer.changed("mouse")
        peers[0].backend.dispatch.call(lambda: None)
        assert receiver.keys == receiver.backend.held_keys == sender.session.keys == {5}
        assert receiver.buttons == receiver.backend.held_buttons == sender.session.buttons == {4}
        key(receiver, 5, 0)
        button(receiver, 4, 0)
        until(lambda sender=sender: not sender.session.keys and not sender.session.buttons)
        receiver.session.leave()
        until(lambda: all(peer.session.phase == "local" for peer in peers))
        assert all(peer._available() and not peer.backend.hidden for peer in peers)

    assert windows_api.SetWindowsHookExW.call_count == 10  # Two hooks per stream.
    assert windows_api.UnhookWindowsHookEx.call_count == 8
    assert mac_api.CGEventTapCreate.call_count == 5
    assert mac_api.CFMachPortInvalidate.call_count == 4


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("blocked", [0, 1], ids=["windows_to_mac_lost", "mac_to_windows_lost"])
def test_one_way_outage_recovers_native_input_without_replaying_retired_udp(native_peers, source, blocked):
    peers, _, mac_api = native_peers
    hybrid = peers[0].channel.lane is not None
    # Establish the production adapters over an encrypted TCP relay. Only the
    # test socket's destination port changes; neither host networking nor the
    # application's saved configuration is touched.
    peers[0].channel.close()
    until(lambda: all(peer.channel.finished.is_set() for peer in peers))
    target = peers[0].link.port
    relay = Relay(target, delay=0)
    peers[0].link.port = relay.port
    try:
        mac_api.stop.clear()
        assert peers[0].connect(peers[1].capabilities() if hybrid else None)
        until(lambda: all(peer._available() for peer in peers))
        if hybrid:
            until(lambda: all(peer.channel.lane.probe.state == "ready" for peer in peers))
        sender, receiver = peers[source], peers[1 - source]
        channels = [peer.channel for peer in peers]
        enter(sender)
        retired_epoch = sender.session.epoch
        raw_motion = []
        if hybrid:
            send = sender.channel.lane.datagram

            def observe(raw):
                raw_motion.append(raw)
                send(raw)

            sender.channel.lane.datagram = observe
        key(sender, 0xE1, 1)
        button(sender, 0, 1)
        until(lambda: receiver.session.keys == {0xE1} and receiver.session.buttons == {0})
        x, y = sender.backend.anchor
        direction = 1 if source == 0 else -1
        motion(sender, int(x + direction * 100), int(y))
        until(lambda: receiver.backend.previous == sender.remote_position)
        if hybrid:
            assert raw_motion
        relay.resume[blocked].clear()
        assert relay.blocked[blocked].wait(1)
        until(lambda: all(channel.finished.is_set() for channel in channels))
        failures = {channel.failure for channel in channels}
        assert "timeout" in failures and failures <= {"timeout", "connection"}
        assert all(not peer.backend.captured and not peer.backend.hidden for peer in peers)
        assert all(not peer.session.keys and not peer.session.buttons for peer in peers)
        assert not mac_api.flags & (1 << 17)
    finally:
        relay.close()
        peers[0].link.port = target

    mac_api.stop.clear()
    assert peers[0].connect(peers[1].capabilities() if hybrid else None)
    until(lambda: all(peer._available() for peer in peers))
    if hybrid:
        until(lambda: all(peer.channel.lane.probe.state == "ready" for peer in peers))
    enter(sender)
    assert sender.session.epoch != retired_epoch
    key(sender, 5, 1)
    button(sender, 4, 1)
    until(lambda: receiver.session.keys == {5} and receiver.session.buttons == {4})

    if hybrid:
        retired = raw_motion[-1]
        observed, admitted = threading.Event(), []
        mux = receiver.link.motion_transport.motion
        receive = mux.receive

        def receive_late(raw, address):
            result = receive(raw, address)
            if raw == retired:
                admitted.append(result)
                observed.set()
            return result

        mux.receive = receive_late
        try:
            point, epoch = receiver.backend.previous, receiver.session.epoch
            transport = sender.link.motion_transport
            transport.send_motion(retired, (sender.link.peer, transport.peer_port))
            assert observed.wait(1)  # Prove the old ciphertext reached the listener.
            assert admitted == [False]
            assert receiver.backend.previous == point and receiver.session.epoch == epoch
            assert receiver.session.keys == {5} and receiver.session.buttons == {4}
        finally:
            mux.receive = receive

    key(sender, 5, 0)
    button(sender, 4, 0)
    until(lambda: not receiver.session.keys and not receiver.session.buttons)
    sender.session.leave()
    until(lambda: all(peer.session.phase == "local" for peer in peers))
    assert all(peer._available() and not peer.backend.hidden for peer in peers)
