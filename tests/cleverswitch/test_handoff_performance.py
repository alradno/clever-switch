"""Latency regressions without equating simulated time with real Bluetooth."""

import threading
import time

import pytest

from cleverswitch.flow.coordinator import Coordinator
from cleverswitch.flow.cursor import Edge, Sample
from cleverswitch.policy import PairPolicy

from .test_energy import rig
from .test_flow import LocalLink, machines, run


@pytest.mark.parametrize("host", [0, 1], ids=["windows", "mac"])
def test_cursor_arrives_before_keyboard_and_is_not_warped_again_on_commit(tmp_path, host):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    owners.update(mouse=host, keyboard=host)
    source, target = (da, db) if host == 0 else (db, da)
    original = source.hardware.switch

    def switch(role, destination):
        if role == "keyboard":
            assert len(target.cursor.placements) == 1, "Pointer waited for the keyboard"
        original(role, destination)

    source.hardware.switch = switch
    link = LocalLink(target)
    coordinator = Coordinator(source.hardware, source.cursor, link, source)
    run(coordinator)
    assert coordinator.phase == "committed", coordinator.last_error
    assert len(target.cursor.placements) == 1


@pytest.mark.parametrize("side", ["left", "right"])
def test_edge_requires_brief_intent_but_not_400ms(side):
    now = [0.0]
    edge = Edge(side, clock=lambda: now[0])
    sample = Sample(0 if side == "left" else 999, 400, 0, 0, 1000, 800)
    assert edge.poll(sample) is None
    now[0] = 0.06
    assert edge.poll(sample) is None
    now[0] = 0.081
    assert edge.poll(sample) == 0.5
    now[0] = 10
    assert edge.poll(sample) is None  # Faster intent must never bypass rearming.


def test_present_device_is_not_left_waiting_for_150ms_retry(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    now = [0.0]
    coordinator = Coordinator(a, c, LocalLink(db), da, clock=lambda: now[0])

    class Replies:
        def call(self, op, args, timeout):
            return {"present": now[0] >= 0.1}

    class Stop:
        def is_set(self):
            return False

        def wait(self, delay):
            now[0] += delay

    coordinator.link, coordinator.stop_event = Replies(), Stop()
    assert coordinator._confirmed("mouse", "a" * 32)
    assert now[0] <= 0.14


def test_arrival_acceleration_is_bounded_and_never_overrides_suspension():
    policy = PairPolicy()
    stop = threading.Event()
    policy.expect_arrival(0.15)
    start = time.monotonic()
    policy.wait_hardware(stop, 1)
    assert time.monotonic() - start < 0.15
    policy.expect_arrival(0)
    policy.set_background_suspended(True)
    finished = threading.Event()

    def work():
        policy.wait_background(stop)
        finished.set()

    thread = threading.Thread(target=work)
    thread.start()
    try:
        policy.expect_arrival(30)
        assert not finished.wait(0.05)
    finally:
        stop.set()
        policy.wake_background()
        thread.join(1)
    assert finished.is_set()


def test_arrival_acceleration_returns_to_normal_interval_and_expires(mocker):
    now = [0.0]
    mocker.patch("cleverswitch.policy.time.monotonic", side_effect=lambda: now[0])
    policy = PairPolicy()
    wait = mocker.patch.object(policy._background_condition, "wait")
    stop = threading.Event()
    policy.expect_arrival(30)
    policy.wait_hardware(stop, 1)
    wait.assert_called_with(0.05)
    now[0] = 30
    policy.wait_hardware(stop, 1)
    wait.assert_called_with(1)
    policy.expect_arrival(30)
    policy.expect_arrival(0)
    policy.wait_hardware(stop, 0.5)
    wait.assert_called_with(0.5)
    wait.reset_mock()
    policy.set_background_suspended(True)
    policy.expect_arrival(30)
    policy.wait_hardware(stop, 1)
    wait.assert_not_called()


@pytest.mark.parametrize("role", ["windows", "mac"])
def test_slow_follow_probe_cannot_block_cursor_or_survive_a_reconnect(tmp_path, mocker, role):
    runtime, cfg, clock, network, link, discovery, hardware, cursor = rig(tmp_path, mocker, role)
    runtime._connect()
    runtime.mode = "online"
    runtime.peer_ready = runtime.enabled = True
    runtime.budget.authenticated()
    runtime.edge = mocker.Mock(since=None, latched=False)
    runtime.edge.poll.return_value = None
    hardware.device.return_value = object()
    entered, release, done = threading.Event(), threading.Event(), threading.Event()

    def probe(device):
        entered.set()
        assert release.wait(2)
        return device == "keyboard"

    hardware.probe.side_effect = probe

    def monitor_step():
        try:
            runtime._step()
        finally:
            done.set()

    worker = threading.Thread(target=monitor_step)
    worker.start()
    try:
        assert entered.wait(1)
        assert done.wait(0.3), "Cursor monitor is stuck behind a device probe"
        runtime._step()
        assert cursor.sample.call_count == 2
        assert hardware.probe.call_count == 1  # No unbounded helper threads.
        runtime._mode("standby")
        runtime.peer_ready = False
    finally:
        release.set()
        worker.join(2)
        if getattr(runtime, "follow_worker", None):
            runtime.follow_worker.join(2)
    assert runtime.follow_candidate is None
    link.call.assert_not_called()


def test_transfer_timing_records_only_elapsed_phases(tmp_path):
    from cleverswitch.flow.identity import read_file

    a, b, c, d, da, db, owners, events = machines(tmp_path)
    coordinator = Coordinator(a, c, LocalLink(db), da)
    run(coordinator)
    times = read_file(da.journal.with_name("last-transfer.json"))["elapsed_ms"]
    assert set(times) == {"preflight", "mouse", "mouse_confirmed", "keyboard", "keyboard_confirmed", "committed"}
    assert all(isinstance(t, (int, float)) and t >= 0 for t in times.values())
    assert (
        times["mouse"]
        < times["mouse_confirmed"]
        <= times["keyboard"]
        < times["keyboard_confirmed"]
        < times["committed"]
    )


def test_lost_mouse_reply_does_not_reposition_a_moving_pointer(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    link = LocalLink(db)
    original = link.call
    lost = []

    def call(op, args=None, timeout=3):
        result = original(op, args, timeout)
        if op == "mouse" and not lost:
            lost.append(op)
            assert len(d.placements) == 1
            raise OSError("lost mouse confirmation")
        return result

    link.call = call
    coordinator = Coordinator(a, c, link, da)
    run(coordinator)
    assert coordinator.phase == "committed"
    assert len(d.placements) == 1


def test_no_pointer_placement_until_fresh_mouse_proof(tmp_path):
    a, b, c, d, da, db, owners, events = machines(tmp_path)
    tx = "a" * 32
    assert db.handle("prepare", {"tx": tx, "kind": "edge", "y": 0.5, "protocol": 2})["accepted"]
    assert not db.handle("mouse", {"tx": tx})["present"]
    assert not d.placements
    db.handle("abort", {"tx": tx})
