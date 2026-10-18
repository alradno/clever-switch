import dataclasses
import random
import socket

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import Kind, Packet
from cleverswitch.flow.input_session import Session
from experiments.benchmark_motion import run_case
from experiments.motion_lane import (
    HEADER,
    MAX_COUNTER,
    Codec,
    Motion,
    PathProbe,
    Probe,
    Receipt,
    Receiver,
    Reliable,
    Sender,
    lab_codecs,
)


class Lab:
    def __init__(self, source=0):
        self.codecs = lab_codecs()
        self.moves, self.receipts, self.actions, self.delivered = [], [], [], []
        self.receiver = Receiver(self.codecs[1 - source], self.receipts.append, self.delivered.append)
        self.probe = PathProbe(self.codecs[source], self.moves.append)
        self.probe.start(0)
        self.receiver.receive(self.moves.pop())
        self.probe.receive(self.receipts.pop(), 0.004)
        self.sender = Sender(
            self.codecs[source], 7, (100, 100), self.moves.append, self.actions.append, probe=self.probe
        )
        self.receiver.begin(7, (100, 100))

    def udp(self, now=0):
        moves, self.moves[:] = self.moves[:], []
        for raw in moves:
            self.receiver.receive(raw)
        receipts, self.receipts[:] = self.receipts[:], []
        for raw in receipts:
            self.sender.receive(raw, now)

    def reliable(self):
        actions, self.actions[:] = self.actions[:], []
        for frame in actions:
            self.receiver.receive_reliable(frame)


@pytest.fixture(params=[0, 1], ids=["windows_to_mac", "mac_to_windows"])
def lab(request):
    return Lab(request.param)


def test_reordered_duplicate_and_lost_motion_never_block_newer_position(lab):
    for index in range(1, 5):
        lab.sender.move((100 + index, 100), index * 0.001)
    lab.receiver.receive(lab.moves[3])
    lab.receiver.receive(lab.moves[0])
    lab.receiver.receive(lab.moves[3])
    assert [packet.values for packet in lab.delivered] == [(104, 100)]
    assert lab.receiver.serial == 4


def test_future_motion_waits_for_action_and_exact_click_position(lab):
    lab.sender.move((120, 130), 0)
    lab.sender.send_action(Kind.BUTTON, 0, 1)
    lab.sender.move((140, 150), 0.001)
    lab.sender.send_action(Kind.BUTTON, 0, 0)
    lab.sender.move((160, 170), 0.002)
    # Deliver the latest UDP first and lose all preceding motion datagrams.
    lab.receiver.receive(lab.moves[-1])
    assert lab.delivered == [] and lab.receiver.pending.serial == 3
    lab.reliable()
    assert [(packet.kind, packet.values) for packet in lab.delivered] == [
        (Kind.MOVE, (120, 130)),
        (Kind.BUTTON, (0, 1)),
        (Kind.MOVE, (140, 150)),
        (Kind.BUTTON, (0, 0)),
        (Kind.MOVE, (160, 170)),
    ]
    assert lab.receiver.pending is None


def test_old_motion_cannot_undo_position_embedded_in_a_key(lab):
    lab.sender.move((120, 130), 0)
    lab.sender.send_action(Kind.KEY, 4, 1)
    lab.sender.move((140, 150), 0.001)
    lab.sender.send_action(Kind.KEY, 4, 0)
    lab.reliable()
    lab.udp()
    assert [packet.values for packet in lab.delivered if packet.kind == Kind.MOVE] == [(120, 130), (140, 150)]
    assert lab.receiver.position == (140, 150)


def test_pending_motion_memory_is_one_sample_while_action_is_stalled(lab):
    lab.sender.send_action(Kind.BUTTON, 0, 1)
    for index in range(1, 2001):
        lab.sender.move((100 + index, 100), index * 0.001)
        lab.udp(index * 0.001)
    assert lab.delivered == []
    assert lab.receiver.pending.serial == 2000
    lab.reliable()
    assert [(packet.kind, packet.values) for packet in lab.delivered] == [
        (Kind.BUTTON, (0, 1)),
        (Kind.MOVE, (2100, 100)),
    ]
    assert lab.receiver.pending is None


def test_final_lost_motion_is_retried_with_fresh_nonce_without_polling(lab):
    lab.sender.move((120, 130), 0)
    original = lab.moves.pop()
    assert lab.sender.deadline == 0.008
    lab.sender.tick(0.008)
    assert lab.moves[-1] != original
    lab.udp(0.008)
    assert lab.receiver.position == (120, 130)
    assert lab.sender.deadline is None
    for now in (1, 10, 86400):
        lab.sender.tick(now)
    assert not lab.moves and not lab.actions


def test_lost_receipt_retries_without_replaying_motion(lab):
    lab.sender.move((120, 130), 0)
    lab.receiver.receive(lab.moves.pop())
    lab.receipts.clear()
    lab.sender.tick(0.008)
    lab.udp(0.008)
    assert len(lab.delivered) == 1 and lab.sender.deadline is None


def test_blocked_udp_has_finite_retries_then_reliable_final_position(lab):
    lab.sender.move((120, 130), 0)
    for now in (0.008, 0.024, 0.056, 0.100):
        lab.sender.tick(now)
    assert len(lab.moves) == 4
    assert lab.sender.mode == "reliable" and lab.sender.deadline is None
    assert len(lab.actions) == 3 and all(frame.position == (120, 130) for frame in lab.actions)
    lab.reliable()
    assert lab.receiver.position == (120, 130)
    lab.sender.move((140, 150), 1)
    lab.reliable()
    assert lab.receiver.position == (140, 150) and len(lab.moves) == 4
    # Old datagrams cannot pull the cursor back after reliable fallback.
    lab.udp(1)
    assert lab.receiver.position == (140, 150)


def test_continuous_movement_does_not_extend_missing_ack_deadline(lab):
    for index in range(100):
        lab.sender.move((101 + index, 100), index * 0.001)
        lab.sender.tick(index * 0.001)
    lab.sender.tick(0.100)
    assert lab.sender.mode == "reliable"
    assert lab.actions[-1].position == (200, 100)


def test_ack_progress_and_stale_receipts_have_distinct_effects(lab):
    lab.sender.move((101, 100), 0)
    lab.receiver.receive(lab.moves.pop())
    first = lab.receipts.pop()
    lab.sender.move((102, 100), 0.02)
    lab.sender.receive(first, 0.04)
    assert lab.sender.unacknowledged_since == 0.04
    lab.sender.receive(first, 0.08)
    assert lab.sender.unacknowledged_since == 0.04
    lab.sender.tick(0.141)
    assert lab.sender.mode == "reliable"


def test_action_barrier_survives_retries_and_fallback(lab):
    lab.sender.move((120, 130), 0)
    lab.sender.send_action(Kind.KEY, 4, 1)
    lab.sender.tick(0.008)
    lab.receiver.receive(lab.moves[-1])
    assert not lab.delivered
    lab.sender.tick(0.101)
    lab.reliable()
    assert [(packet.kind, packet.values) for packet in lab.delivered] == [(Kind.MOVE, (120, 130)), (Kind.KEY, (4, 1))]
    assert lab.receiver.pending is None


def test_socket_failure_falls_back_without_losing_position(lab):
    def fail(raw):
        raise OSError("Synthetic UDP failure")

    lab.sender.datagram = fail
    lab.sender.move((120, 130), 0)
    lab.reliable()
    assert lab.sender.mode == "reliable" and lab.receiver.position == (120, 130)


def test_no_focus_no_injection_no_ack_and_late_packets_do_not_reactivate(lab):
    lab.sender.move((120, 130), 0)
    lab.receiver.end()
    lab.receiver.receive(lab.moves[-1])
    assert not lab.receipts and not lab.delivered
    lab.receiver.begin(8, (200, 200))
    lab.sender.move((140, 150), 0.01)
    lab.receiver.receive(lab.moves[-1])
    lab.sender.send_action(Kind.KEY, 4, 1)
    lab.reliable()
    assert lab.receiver.position == (200, 200) and not lab.delivered


def test_focus_end_discards_buffered_future_motion_and_stops_timers(lab):
    lab.sender.send_action(Kind.BUTTON, 0, 1)
    lab.sender.move((120, 130), 0)
    lab.receiver.receive(lab.moves[-1])
    lab.receiver.end()
    lab.sender.stop()
    lab.sender.tick(100)
    lab.sender.move((140, 150), 101)
    lab.sender.send_action(Kind.BUTTON, 0, 0)
    lab.reliable()
    assert lab.sender.deadline is None and lab.receiver.pending is None
    assert not lab.delivered


def test_every_datagram_byte_is_authenticated_and_cannot_poison_replay_state(lab):
    lab.sender.move((120, 130), 0)
    raw = lab.moves.pop()
    for index in range(len(raw)):
        altered = bytearray(raw)
        altered[index] ^= 1
        lab.receiver.receive(bytes(altered))
    assert not lab.delivered and not lab.receipts
    lab.receiver.receive(raw)
    assert lab.receiver.position == (120, 130) and len(lab.delivered) == 1


def test_reflection_wrong_session_and_wrong_role_are_rejected(lab):
    lab.sender.move((120, 130), 0)
    raw = lab.moves.pop()
    assert lab.sender.codec.open(raw) is None
    assert all(codec.open(raw) is None for codec in lab_codecs())
    assert lab.receiver.codec.open(raw) is not None


@pytest.mark.parametrize("raw", [b"", b"x", b"x" * 65536], ids=["empty", "truncated", "oversized"])
def test_invalid_datagram_lengths_are_discarded(lab, raw):
    lab.receiver.receive(raw)
    assert not lab.delivered and not lab.receipts


def test_counter_exhaustion_and_closed_key_cannot_reuse_nonce():
    codec, _ = lab_codecs()
    codec.sent = MAX_COUNTER - 1
    raw = codec.seal(Motion(7, 1, 0, (100, 100)))
    assert HEADER.unpack_from(raw)[-1] == MAX_COUNTER
    with pytest.raises(ConnectionError):
        codec.seal(Motion(7, 2, 0, (100, 100)))
    codec.close()
    with pytest.raises(ConnectionError):
        codec.seal(Receipt(7, 1))
    assert codec.open(raw) is None


def test_unprobed_path_uses_reliable_input_immediately(lab):
    sender = Sender(lab.sender.codec, 7, (100, 100), lab.moves.append, lab.actions.append)
    sender.move((120, 130), 0)
    lab.reliable()
    assert sender.mode == "reliable" and sender.deadline is None
    assert lab.receiver.position == (120, 130) and not lab.moves


def test_probe_can_validate_without_input_focus_and_stops_when_ready(lab):
    assert lab.probe.state == "ready" and lab.probe.deadline is None
    assert lab.delivered == [] and lab.sender.mode == "datagram"
    lab.probe.start(1)
    lab.probe.tick(86400)
    assert lab.moves == []


def test_blocked_probe_has_three_attempts_no_periodic_work_and_cannot_enable(lab):
    probe = PathProbe(lab.sender.codec, lab.moves.append)
    probe.start(0)
    for now in (0.016, 0.048, 0.100, 1, 86400):
        probe.tick(now)
    assert probe.state == "unavailable" and probe.deadline is None and len(lab.moves) == 3
    sender = Sender(lab.sender.codec, 7, (100, 100), lab.moves.append, lab.actions.append, probe=probe)
    assert sender.mode == "reliable"


def test_probe_rejects_wrong_challenge_late_reply_and_another_connection(lab):
    probe = PathProbe(lab.sender.codec, lab.moves.append)
    probe.start(0)
    probe.receive(lab.receiver.codec.seal(Probe(True, b"x" * 16)), 0.001)
    assert probe.state == "pending"
    probe.receive(lab.receiver.codec.seal(Probe(True, probe.challenge)), 0.100)
    assert probe.state == "unavailable"
    other, _ = lab_codecs()
    sender = Sender(other, 7, (100, 100), lab.moves.append, lab.actions.append, probe=lab.probe)
    assert sender.mode == "reliable"


def test_lost_probe_response_can_retry_without_enabling_input(lab):
    lab.receiver.end()
    probe = PathProbe(lab.sender.codec, lab.moves.append)
    probe.start(0)
    lab.receiver.receive(lab.moves.pop())
    lab.receipts.clear()
    probe.tick(0.016)
    lab.receiver.receive(lab.moves.pop())
    probe.receive(lab.receipts.pop(), 0.020)
    assert probe.state == "ready" and not lab.delivered and lab.receiver.epoch is None


def test_probe_socket_failure_and_late_response_keep_reliable_mode(lab):
    def fail(raw):
        raise OSError("Synthetic probe failure")

    probe = PathProbe(lab.sender.codec, fail)
    probe.start(0)
    assert probe.state == "unavailable" and probe.deadline is None
    probe.receive(lab.receiver.codec.seal(Probe(True, probe.challenge)), 0.001)
    assert probe.state == "unavailable"


def test_reply_socket_failure_does_not_break_valid_motion_or_open_focus(lab):
    def fail(raw):
        raise OSError("Synthetic reply failure")

    lab.receiver.datagram = fail
    probe = PathProbe(lab.sender.codec, lab.moves.append)
    probe.start(0)
    lab.receiver.receive(lab.moves.pop())
    assert probe.state == "pending" and not lab.delivered
    lab.sender.move((120, 130), 0)
    lab.receiver.receive(lab.moves.pop())
    assert lab.receiver.position == (120, 130) and not lab.receipts
    lab.sender.tick(0.101)
    lab.reliable()
    assert lab.sender.mode == "reliable" and lab.receiver.position == (120, 130)


@pytest.mark.parametrize(
    "kind,values", [(Kind.KEY, (4, 1)), (Kind.BUTTON, (0, 1)), (Kind.WHEEL, (0, 1)), (Kind.LOCKS, (1, 7))]
)
def test_all_action_kinds_gate_later_motion(lab, kind, values):
    lab.sender.move((120, 130), 0)
    lab.sender.send_action(kind, *values)
    lab.sender.move((140, 150), 0.001)
    lab.udp(0.001)
    assert lab.receiver.position == (120, 130)
    lab.reliable()
    assert lab.delivered[-2].kind == kind and lab.delivered[-1].values == (140, 150)


def test_reliable_reordering_or_conflicting_position_is_a_protocol_error(lab):
    lab.sender.send_action(Kind.BUTTON, 0, 1)
    frame = lab.actions[0]
    with pytest.raises(ValueError):
        lab.receiver.receive_reliable(dataclasses.replace(frame, action=2))
    with pytest.raises(ValueError):
        lab.receiver.receive_reliable(dataclasses.replace(frame, position=(200, 200)))
    assert not lab.delivered
    lab.reliable()
    with pytest.raises(ValueError):
        lab.receiver.receive_reliable(frame)


def test_datagrams_cannot_contain_actions_or_invalid_coordinates():
    codec, _ = lab_codecs()
    with pytest.raises(ValueError):
        codec.seal(Packet(Kind.KEY, 7, (4, 1)))
    with pytest.raises(ValueError):
        Motion(7, 1, 0, (float("nan"), 0))
    with pytest.raises(ValueError):
        Reliable(Kind.ENTER, 7, 1, 0, (0, 0))
    with pytest.raises(ValueError):
        Codec(b"x" * 32, b"x" * 16, "unknown")


def test_random_delivery_permutations_keep_action_positions_and_final_snapshot(lab):
    randomizer = random.Random(749)
    expected = []
    for index in range(1, 401):
        point = (100 + index, 100)
        lab.sender.move(point, index * 0.001)
        if index % 4 == 0:
            down = int(index % 8 == 4)
            lab.sender.send_action(Kind.BUTTON, 0, down)
            expected.append((point, (0, down)))
        if randomizer.random() < 0.3 and lab.moves:
            raw = lab.moves.pop(randomizer.randrange(len(lab.moves)))
            lab.receiver.receive(raw)
        if randomizer.random() < 0.2 and lab.actions:
            lab.receiver.receive_reliable(lab.actions.pop(0))
    randomizer.shuffle(lab.moves)
    for raw in lab.moves:
        lab.receiver.receive(raw)
    lab.reliable()
    lab.receiver.receive_reliable(Reliable(Kind.MOVE, 7, lab.sender.action, lab.sender.serial, lab.sender.position))
    observed = []
    point = (100, 100)
    for packet in lab.delivered:
        if packet.kind == Kind.MOVE:
            point = packet.values
        elif packet.kind == Kind.BUTTON:
            observed.append((point, packet.values))
    assert observed == expected
    assert lab.receiver.position == (500, 100) and lab.receiver.pending is None


def test_encrypted_datagram_over_real_loopback_udp(lab):
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as target,
    ):
        target.bind(("127.0.0.1", 0))
        target.settimeout(1)
        lab.sender.move((120, 130), 0)
        source.sendto(lab.moves.pop(), target.getsockname())
        raw, _ = target.recvfrom(128)
        lab.receiver.receive(raw)
    assert lab.receiver.position == (120, 130)


def test_existing_session_releases_pressed_input_when_hybrid_focus_ends(lab):
    class Backend:
        def __init__(self):
            self.events = []

        def ready(self):
            return True

        def inject(self, packet):
            self.events.append(packet)

        def capture(self, enabled):
            pass

    backend = Backend()
    session = Session("mac", Desktop([Display(0, 0, 1920, 1080)]), backend, lambda packet: None)
    session.receive(Packet(Kind.ENTER, 7, (100, 100)))
    lab.receiver.deliver = session.receive
    lab.sender.send_action(Kind.KEY, 4, 1)
    lab.sender.send_action(Kind.BUTTON, 0, 1)
    lab.reliable()
    assert session.keys == {4} and session.buttons == {0}
    lab.receiver.end()
    session.close()
    assert not session.keys and not session.buttons
    assert {(packet.kind, packet.values) for packet in backend.events[-2:]} == {
        (Kind.KEY, (4, 0)),
        (Kind.BUTTON, (0, 0)),
    }


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
@pytest.mark.parametrize("rate", [125, 1000])
def test_confirmed_path_outage_keeps_pointer_progress_before_mode_fallback(source, rate):
    result = run_case("hybrid", source, rate, "udp_fails_after_start", True)
    assert result["simulated_max_delivery_gap_ms"] <= 24
    assert result["final_mode"] == "reliable" and result["verified_actions_and_positions"] == 4
