"""Recover a proven UDP path after a transient receiver pause, without polling."""

import threading

import pytest

from cleverswitch.flow.input_hybrid import MotionLane
from cleverswitch.flow.input_motion import Motion, Probe, Receipt
from cleverswitch.flow.input_protocol import Kind, Packet, Routed
from experiments.motion_lane import lab_codecs

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_device_continuity import busy_session
from .test_input_hybrid import hybrid as hybrid
from .test_input_hybrid import transports as transports
from .test_input_service import enter
from .test_input_service import services as services
from .test_input_stream import until


@pytest.fixture(params=[0, 1], ids=["windows_source", "mac_source"])
def path(request):
    codecs = lab_codecs()
    codec, peer = codecs[request.param], codecs[1 - request.param]
    datagrams, reliable = [], []
    focus = ["requesting", 9]
    lane = MotionLane(codec, datagrams.append, reliable.append, lambda packet: None, lambda: tuple(focus))
    try:
        yield lane, peer, datagrams, reliable, focus
    finally:
        lane.close()
        peer.close()


def proven(path):
    lane, peer, datagrams, reliable, focus = path
    lane.start(0)
    lane.receive_message(Probe(True, lane.probe.challenge), 0.001)
    lane.outgoing(Packet(Kind.ENTER, 9, (100, 200)), 0.002)
    focus[0] = "sending"
    datagrams.clear()
    reliable.clear()
    return lane


def lost(path):
    lane = proven(path)
    lane.outgoing(Packet(Kind.MOVE, 9, (120, 200)), 0.010)
    lane.tick(0.111)
    assert lane.sender.mode == "reliable" and lane.probe.state == "unavailable"
    path[2].clear()
    return lane


def test_initial_probe_completion_upgrades_existing_focus_without_overtaking_accept(path):
    lane, peer, datagrams, reliable, focus = path
    lane.start(0)
    lane.outgoing(Packet(Kind.ENTER, 9, (100, 200)), 0.001)
    assert lane.sender.mode == "reliable"
    lane.receive_message(Probe(True, lane.probe.challenge), 0.002)
    assert lane.sender.mode == "datagram"
    lane.outgoing(Packet(Kind.MOVE, 9, (110, 200)), 0.003)
    assert isinstance(reliable[-1], Routed) and reliable[-1].position == (110, 200)
    focus[0] = "sending"
    lane.outgoing(Packet(Kind.MOVE, 9, (120, 200)), 0.004)
    assert peer.open(datagrams[-1]) == Motion(9, 2, 0, (120, 200))


def test_late_receipt_checks_current_reachability_before_restoring_fast_motion(path):
    lane = lost(path)
    old_challenge = lane.probe.challenge
    lane.receive_message(Receipt(9, 1), 0.150)
    assert lane.probe.state == "pending" and lane.probe.challenge != old_challenge
    assert lane.sender.mode == "reliable"
    lane.outgoing(Packet(Kind.MOVE, 9, (130, 200)), 0.151)
    lane.outgoing(Packet(Kind.BUTTON, 9, (0, 1)), 0.152)
    assert isinstance(path[3][-1], Routed) and path[3][-1].position == (130, 200)
    lane.receive_message(Probe(True, old_challenge), 0.153)
    assert lane.sender.mode == "reliable"
    lane.receive_message(Probe(True, lane.probe.challenge), 0.154)
    assert lane.sender.mode == "datagram"
    lane.outgoing(Packet(Kind.MOVE, 9, (140, 200)), 0.155)
    assert path[1].open(path[2][-1]) == Motion(9, 3, 1, (140, 200))
    lane.receive_message(Receipt(9, 3), 0.156)
    assert lane.deadline is None


@pytest.mark.parametrize("receipt", [Receipt(8, 1), Receipt(9, 2)])
def test_wrong_epoch_or_unsent_serial_cannot_start_a_recheck(path, receipt):
    lane = lost(path)
    lane.receive_message(receipt, 0.150)
    assert lane.probe.state == "unavailable" and not path[2]
    assert lane.deadline is None


def test_already_acknowledged_receipt_cannot_start_a_recheck(path):
    lane = proven(path)
    lane.outgoing(Packet(Kind.MOVE, 9, (110, 200)), 0.010)
    lane.receive_message(Receipt(9, 1), 0.011)
    lane.outgoing(Packet(Kind.MOVE, 9, (120, 200)), 0.012)
    lane.tick(0.113)
    path[2].clear()
    lane.receive_message(Receipt(9, 1), 0.150)
    assert lane.probe.state == "unavailable" and not path[2]


def test_failed_recheck_has_three_attempts_and_no_periodic_or_receipt_retries(path):
    lane = lost(path)
    lane.receive_message(Receipt(9, 1), 0.150)
    challenge = lane.probe.challenge
    for now in (0.167, 0.199, 0.251):
        lane.tick(now)
    assert len(path[2]) == 3 and lane.probe.state == "unavailable"
    lane.receive_message(Probe(True, challenge), 0.252)
    for now in (1, 10, 86400):
        lane.receive_message(Receipt(9, 1), now)
        lane.tick(now)
    assert lane.sender.mode == "reliable" and lane.deadline is None
    assert len(path[2]) == 3


@pytest.mark.parametrize("focus", [("local", 0), ("requesting", 10), ("receiving", 10)])
def test_focus_change_retires_recheck_before_late_response(path, focus):
    lane = lost(path)
    lane.receive_message(Receipt(9, 1), 0.150)
    challenge = lane.probe.challenge
    path[4][:] = focus
    lane.receive_message(Probe(True, challenge), 0.151)
    lane.tick(1)
    assert lane.probe.state == "unavailable" and lane.sender.mode == "reliable"
    assert len(path[2]) == 1 and lane.deadline is None


def test_second_failure_does_not_open_another_recheck_window(path):
    lane = lost(path)
    lane.receive_message(Receipt(9, 1), 0.150)
    lane.receive_message(Probe(True, lane.probe.challenge), 0.151)
    assert lane.sender.mode == "datagram"
    lane.outgoing(Packet(Kind.MOVE, 9, (130, 200)), 0.160)
    lane.tick(0.261)
    path[2].clear()
    lane.receive_message(Receipt(9, 2), 0.300)
    lane.tick(86400)
    assert lane.sender.mode == "reliable" and lane.deadline is None and not path[2]


def test_socket_failure_during_recheck_keeps_reliable_actions_available(path):
    lane = lost(path)
    attempts = []

    def unavailable(raw):
        attempts.append(True)
        raise OSError("Synthetic unavailable path")

    lane.datagram = unavailable
    lane.receive_message(Receipt(9, 1), 0.150)
    lane.tick(86400)
    lane.outgoing(Packet(Kind.KEY, 9, (4, 1)), 86400)
    assert lane.sender.mode == "reliable" and lane.deadline is None and attempts == [True]
    assert path[3][-1].packet.values == (4, 1)


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
def test_paused_receiver_recovers_udp_without_dropping_pressed_input_or_connection(hybrid, source):
    sender, receiver = hybrid[source], hybrid[1 - source]
    enter(sender)
    sender.key(4, 1)
    sender.button(0, 1)
    until(lambda: receiver.session.keys == {4} and receiver.session.buttons == {0})
    held, release = threading.Event(), threading.Event()
    original = receiver.channel.on_tick
    channels = [peer.channel for peer in hybrid]

    def pause_once():
        if not held.is_set():
            held.set()
            assert release.wait(1)
        original()

    receiver.channel.on_tick = pause_once
    receiver.channel._wake()
    try:
        assert held.wait(1)
        x, y = sender.remote_position
        sender.motion(900, 400, 300 - x, 400 - y)
        until(lambda: sender.channel.lane.sender.mode == "reliable", timeout=0.3)
    finally:
        release.set()
    until(lambda: sender.channel.lane.sender.mode == "datagram", timeout=0.3)
    assert receiver.session.keys == {4} and receiver.session.buttons == {0}
    # Use the same callback queue for releases and motion. Calling Session.input
    # directly could overtake deferred releases and make the final event a key-up
    # even though the pointer had arrived correctly. Exercise that queue here.
    with busy_session(sender, True):
        assert sender.key(4, 0)
        assert sender.button(0, 0)
        assert sender.motion(900, 400, 100, 100)
        assert sender.deferred.qsize() >= 2
    until(lambda: receiver.backend.events[-1].values == (400, 500))
    assert not receiver.session.keys and not receiver.session.buttons
    assert [peer.channel for peer in hybrid] == channels and all(peer._available() for peer in hybrid)
    until(lambda: sender.channel.lane.sender.latest is None)
    assert sender.channel.lane.deadline is None
