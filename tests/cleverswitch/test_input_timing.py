"""Stage diagnostics retain aggregates only and never hold up real input."""

import json
import threading
import time

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_health import Readiness
from cleverswitch.flow.input_protocol import Kind, Packet
from cleverswitch.flow.input_session import Session
from cleverswitch.flow.input_timing import Duration, RoutingTiming

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_core import Backend
from .test_input_end_to_end import enter, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_stream import until


def test_duration_uses_fixed_storage_and_reports_slow_stages():
    metric = Duration()
    for value in [0, .001, .004, .008, .016, .033, .05, .1, .25, .6]:
        metric.observe(value)
    report = metric.snapshot()
    assert report['samples'] == 10
    assert report['maximum_ms'] == 600
    assert report['bucket_counts'] == [2, 1, 1, 1, 1, 1, 1, 1, 1]
    assert sum(report['bucket_counts']) == report['samples']
    assert len(metric.buckets) == 9
    assert set(vars(metric)) == {'lock', 'count', 'total', 'maximum', 'buckets'}
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf')])
def test_invalid_duration_is_not_retained(value):
    metric = Duration()
    metric.observe(value)
    assert metric.snapshot()['samples'] == 0


def test_diagnostic_contention_skips_measurement_without_blocking_input():
    metric = Duration()
    with metric.lock:
        # A blocking acquire here would deadlock on this same thread.
        metric.observe(.1)
    assert metric.snapshot()['samples'] == 0
    metric.observe(.008)
    assert metric.snapshot()['samples'] == 1


def test_motion_timing_contains_intervals_not_pointer_history():
    metrics = RoutingTiming()
    for stamp in (10.0, 10.008, 10.016, 10.120):
        metrics.motion(stamp)
    snapshot = metrics.snapshot()
    assert snapshot['motion_interval']['samples'] == 3
    assert snapshot['motion_interval']['maximum_ms'] == 104
    assert metrics.previous_motion == 10.120
    assert 'previous_motion' not in snapshot
    assert all(isinstance(value, Duration) for key, value in vars(metrics).items() if key != 'previous_motion')


@pytest.mark.parametrize('fail', [False, True])
def test_native_delivery_time_excludes_error_cleanup_and_contains_no_packet(fail):
    now, timing = [10.0], Duration()
    backend = Backend()
    desktop = Desktop([Display(0, 0, 1920, 1080)])
    session = Session('mac', desktop, backend, lambda packet: None, clock=lambda: now[0], injection_timing=timing)
    session.phase, session.epoch = 'receiving', 9

    def inject(packet):
        now[0] += .08
        if fail:
            raise OSError('synthetic native failure')

    def cleanup(reason):
        now[0] += .4

    backend.inject = inject
    session.cancel = cleanup
    packet = Packet(Kind.MOVE, 9, (1234.5, 987.25))
    if fail:
        with pytest.raises(ConnectionError):
            session.receive(packet)
    else:
        session.receive(packet)
    report = timing.snapshot()
    assert report['samples'] == 1 and report['maximum_ms'] == 80
    assert '1234.5' not in json.dumps(report) and '987.25' not in json.dumps(report)


def test_slow_readiness_is_visible_without_changing_the_permission_trust_limit():
    now = [10.0]

    def check():
        now[0] += .08
        return True

    health = Readiness(check, clock=lambda: now[0])
    assert health.ready()
    assert health.duration.snapshot()['maximum_ms'] == 80
    assert health.sample == (True, 10.0)
    assert health.max_age == .5


@pytest.mark.parametrize('source', [0, 1], ids=['windows_to_mac', 'mac_to_windows'])
def test_stage_metrics_localize_a_receiver_stall_without_changing_pointer_delivery(native_peers, source):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    original = receiver.backend.inject
    entered, release = threading.Event(), threading.Event()

    def inject(packet):
        if packet.kind == Kind.MOVE and not entered.is_set():
            entered.set()
            assert release.wait(1)
        original(packet)

    receiver.backend.inject = inject
    x, y = sender.backend.anchor
    direction = 1 if source == 0 else -1
    try:
        motion(sender, int(x + direction), int(y))
        assert entered.wait(1)
        time.sleep(.04)  # A controlled fake native API pause, never actual input.
        motion(sender, int(x + direction), int(y))
        latest = sender.remote_position
        release.set()
        until(lambda: receiver.backend.previous == latest)
        received = receiver.diagnostic()['performance']
        offered = sender.diagnostic()['performance']
        assert received['routing']['native_delivery']['maximum_ms'] >= 35
        assert offered['routing']['motion_callback']['samples'] >= 4
        assert offered['routing']['motion_interval']['samples'] >= 3
        assert received['transport']['routing_and_datagrams']['samples']
        assert received['transport']['tls_read_and_delivery']['samples']
        assert sender._available() and receiver._available()
        # These fields are numeric summaries; no transient event payload leaks.
        json.dumps(received, allow_nan=False)
    finally:
        release.set()
        receiver.backend.inject = original
