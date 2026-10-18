"""Interactive clocks resolve work shorter than a Windows uptime-clock tick."""

import math
import queue
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.flow import input_service, input_stream
from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_health import Readiness
from cleverswitch.flow.input_macos import MacInput
from cleverswitch.flow.input_service import native_callback
from cleverswitch.flow.input_session import Session
from cleverswitch.flow.input_stream import Channel
from cleverswitch.flow.input_timing import RoutingTiming
from cleverswitch.flow.input_windows import WindowsInput

from .test_input_core import Backend
from .test_input_native import Quartz, User32


@pytest.mark.parametrize("component", ["session", "windows", "mac", "health"])
def test_default_input_clocks_share_a_submillisecond_elapsed_time_domain(component):
    desktop = Desktop([Display(0, 0, 1920, 1080)])
    objects = {
        "session": Session("mac", desktop, Backend(), lambda packet: None),
        "windows": WindowsInput(User32(), Mock(), ready=lambda: True, desktop=desktop),
        "mac": MacInput(Quartz(), ready=lambda: True, secure_input=lambda: False, desktop=desktop),
        "health": Readiness(lambda: True),
    }
    clock = objects[component].clock
    before = time.perf_counter()
    stamp = clock()
    after = time.perf_counter()
    assert before <= stamp <= after
    info = time.get_clock_info(clock.__name__)
    assert info.monotonic and not info.adjustable and info.resolution < .001


def clocks(now):
    # Reproduce Python 3.12 Windows uptime granularity, independently of host OS.
    return SimpleNamespace(
        monotonic=lambda: math.floor(now[0] / .015625) * .015625,
        perf_counter=lambda: now[0],
    )


@pytest.mark.parametrize("work_ms", [1, 3, 7])
def test_sub_tick_work_is_deducted_from_an_eight_millisecond_retry(monkeypatch, work_ms):
    now = [10.001]
    monkeypatch.setattr(input_stream, "time", clocks(now))
    a, b = socket.socketpair()
    channel = Channel(a, lambda packet: None, lambda: None)
    channel.inbox = SimpleNamespace(take=lambda: ())

    class Lane:
        def start(self, stamp):
            pass

        def decode_datagrams(self, packets):
            return ()

        def tick(self, stamp):
            self.due = stamp + .008
            self.work_pending = True

        @property
        def deadline(self):
            if self.work_pending:
                now[0] += work_ms / 1000
                self.work_pending = False
            return self.due

        def close(self):
            pass

    channel.lane = Lane()
    channel.transport = SimpleNamespace(motion=SimpleNamespace(unregister=lambda inbox: None))
    waits = []

    def select_once(readers, writers, errors, timeout):
        waits.append(timeout)
        channel.stop.set()
        return [], [], []

    monkeypatch.setattr(input_stream.select, "select", select_once)
    try:
        channel._run()
        assert channel.failure is None
        assert waits == pytest.approx([.008 - work_ms / 1000])
    finally:
        channel.close()
        b.close()


def test_four_millisecond_callbacks_remain_visible_with_a_coarse_uptime_clock(monkeypatch):
    now = [10.001]
    monkeypatch.setattr(input_service, "time", clocks(now))
    peer = SimpleNamespace(session=None, deferred=queue.Queue(), draining=threading.Event(),
                           performance=RoutingTiming())

    @native_callback
    def motion(self):
        now[0] += .004
        return False

    for _ in range(3):
        assert not motion(peer)
    report = peer.performance.snapshot()
    assert report["motion_callback"]["samples"] == 3
    assert report["motion_callback"]["average_ms"] == 4
    assert report["motion_callback"]["maximum_ms"] == 4
    assert report["motion_interval"]["average_ms"] == 4
