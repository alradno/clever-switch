"""Authenticated burst selection without sockets, native input or extra waits."""

import pytest

from cleverswitch.flow.input_hybrid import MotionLane
from cleverswitch.flow.input_motion import Motion, Probe, Receipt
from cleverswitch.flow.input_protocol import Kind, Packet, Routed
from experiments.motion_lane import lab_codecs


@pytest.fixture(params=[0, 1], ids=["windows_to_mac", "mac_to_windows"])
def batch(request):
    codecs = lab_codecs()
    source, target = codecs[request.param], codecs[1 - request.param]
    events, replies = [], []
    focus = ["receiving", 9]
    lane = MotionLane(target, replies.append, lambda packet: None, events.append, lambda: tuple(focus))
    lane.receiver.begin(9, (100, 200))
    try:
        yield source, lane, events, replies, focus
    finally:
        source.close()
        lane.close()


def deliver(lane, raw):
    messages = lane.decode_datagrams(raw)
    for message in messages:
        lane.receive_message(message, 0.004)
    return messages


def test_backlog_keeps_latest_position_and_sends_one_receipt(batch):
    codec, lane, events, replies, _ = batch
    packets = [codec.seal(Motion(9, index, 0, (100 + index, 200))) for index in range(1, 65)]
    messages = deliver(lane, packets)
    assert len(messages) == 1
    assert [event.values for event in events] == [(164, 200)]
    assert [codec.open(raw) for raw in replies] == [Receipt(9, 64)]
    assert lane.received == 64 and lane.received_coalesced == 63
    assert lane.deadline is None


def test_latest_position_does_not_cross_click_key_or_epoch_boundaries(batch):
    codec, lane, events, _, _ = batch
    packets = [
        Motion(9, 1, 0, (110, 200)), Motion(9, 2, 0, (120, 200)),
        Motion(9, 3, 1, (130, 200)), Motion(9, 4, 1, (140, 200)),
        Motion(9, 5, 2, (150, 200)), Motion(9, 6, 2, (160, 200)),
        Motion(10, 1, 0, (170, 200)),
    ]
    messages = deliver(lane, [codec.seal(packet) for packet in packets])
    assert [message.serial for message in messages] == [2, 4, 6, 1]
    assert [event.values for event in events] == [(120, 200)]
    lane.receive_reliable(Routed(Packet(Kind.BUTTON, 9, (0, 1)), 1, 2, (120, 200)))
    lane.receive_reliable(Routed(Packet(Kind.KEY, 9, (4, 1)), 2, 4, (140, 200)))
    assert [(event.kind, event.values) for event in events] == [
        (Kind.MOVE, (120, 200)), (Kind.BUTTON, (0, 1)),
        (Kind.MOVE, (140, 200)), (Kind.KEY, (4, 1)), (Kind.MOVE, (160, 200)),
    ]


def test_invalid_or_replayed_ciphertext_cannot_replace_verified_motion(batch):
    codec, lane, events, _, _ = batch
    first = codec.seal(Motion(9, 1, 0, (110, 200)))
    second = codec.seal(Motion(9, 2, 0, (120, 200)))
    invalid = bytearray(codec.seal(Motion(9, 100, 0, (999, 200))))
    invalid[-1] ^= 1
    deliver(lane, [first, bytes(invalid), second, first])
    assert [event.values for event in events] == [(120, 200)]
    assert lane.received == 2 and lane.received_coalesced == 1


def test_lower_serial_retry_does_not_override_newest_authenticated_sample(batch):
    codec, lane, events, _, _ = batch
    packets = [codec.seal(Motion(9, serial, 0, (100 + serial, 200))) for serial in (3, 2, 1)]
    deliver(lane, packets)
    assert [event.values for event in events] == [(103, 200)]


def test_probe_and_receipt_are_not_dropped_or_moved_across_motion(batch):
    codec, lane, _, _, _ = batch
    packets = [
        Motion(9, 1, 0, (110, 200)), Probe(False, b"a" * 16),
        Motion(9, 2, 0, (120, 200)), Receipt(8, 42), Motion(9, 3, 0, (130, 200)),
    ]
    messages = lane.decode_datagrams([codec.seal(packet) for packet in packets])
    assert messages == packets and lane.received_coalesced == 0


def test_focus_cancelled_after_decoding_does_not_apply_buffered_movement(batch):
    codec, lane, events, replies, focus = batch
    messages = lane.decode_datagrams([codec.seal(Motion(9, 1, 0, (110, 200)))])
    focus[:] = ["local", 9]
    lane.receive_message(messages[0], 0.004)
    assert events == replies == [] and lane.receiver.epoch is None
