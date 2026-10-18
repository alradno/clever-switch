"""Work already spent this turn must not extend a pending motion retry."""

import socket
from types import SimpleNamespace

import pytest

from cleverswitch.flow import input_stream
from cleverswitch.flow.input_stream import Channel


@pytest.mark.parametrize("work_ms", [4, 24, 80, 140])
def test_socket_wait_uses_time_after_queue_and_packet_processing(mocker, work_ms):
    now = [10.0]
    mocker.patch.object(input_stream.time, "perf_counter", side_effect=lambda: now[0])
    a, b = socket.socketpair()
    channel = Channel(a, lambda packet: None, lambda: None)
    channel.inbox = SimpleNamespace(take=lambda: ())
    # Model a pending retry eight milliseconds after processing this turn.
    # Real on_tick may drain callbacks and lane.receive_message may inject.
    lane = SimpleNamespace(
        start=lambda stamp: None,
        decode_datagrams=lambda packets: (),
        close=lambda: None,
        deadline=None,
    )

    def work():
        now[0] += work_ms / 1000

    def schedule(stamp):
        lane.deadline = stamp + 0.008

    channel.on_tick = work
    lane.tick = schedule
    channel.lane = lane
    waits = []

    def select_once(readers, writers, errors, timeout):
        waits.append(timeout)
        channel.stop.set()
        return [], [], []

    mocker.patch.object(input_stream.select, "select", side_effect=select_once)
    # This isolated loop owns no discovery registration.
    channel.transport = SimpleNamespace(motion=SimpleNamespace(unregister=lambda inbox: None))
    try:
        channel._run()
        assert waits == pytest.approx([0.008])
        assert channel.failure is None
    finally:
        channel.close()
        b.close()
