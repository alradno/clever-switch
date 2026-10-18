import threading
import time
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import Kind
from cleverswitch.flow.input_service import Service
from cleverswitch.flow.input_session import Session
from cleverswitch.flow.network import Link

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_core import Backend
from .test_input_stream import until


class DesktopBackend(Backend):
    def __init__(self, width=1920):
        super().__init__()
        self.desktop = Desktop([Display(0, 0, width, 1080)])
        self.starts = self.closes = 0
        self.restored = []
        self.sink = None

    def start(self, sink):
        self.starts += 1
        self.sink = sink

    def close(self):
        self.closes += 1
        self.capture(False)

    def restore(self, point):
        self.restored.append(point)


@pytest.fixture
def services(paired_tls):
    links = [Link(p, lambda op, args: {"ready": True}, 0) for p in paired_tls]
    peers = [Service(link, DesktopBackend(1920 if i == 0 else 1440), enabled=True) for i, link in enumerate(links)]
    for link in links:
        link.start()
    links[0].port, links[1].port = links[1].port, links[0].port
    try:
        yield peers
    finally:
        for peer in peers:
            peer.close()
        for link in links:
            link.close()


def connected(services):
    assert services[0].connect()
    until(lambda: all(peer._available() for peer in services))


def enter(peer):
    width = peer.backend.desktop.right
    if peer.role == "windows":
        assert not peer.motion(width - 100, 250, 10, 0)
        assert peer.motion(width, 250, 100, 0)
    else:
        assert not peer.motion(100, 250, -10, 0)
        assert peer.motion(0, 250, -100, 0)
    until(lambda: peer.session.phase == "sending")


@pytest.mark.parametrize("source", [0, 1])
def test_continuous_border_cross_and_return_without_bluetooth_or_dwell(services, source):
    connected(services)
    peer, target = services[source], services[1 - source]
    enter(peer)
    inward = 100 if peer.role == "windows" else -100
    assert peer.motion(100, 250, inward, 0)
    until(lambda: len(target.backend.events) >= 2)
    assert peer.motion(100, 250, -inward * 2, 0)
    until(lambda: target.session.phase == "local")
    assert peer.session.phase == "local"
    assert len(peer.backend.restored) == 1
    # Check the actual return position, which now includes the remaining motion.
    x, y = peer.backend.restored[-1]
    step = 1 if peer.role == "windows" else -1
    assert not peer.motion(x, y, 0, 0)
    assert not peer.motion(x + step, y, step, 0)


def test_held_mouse_button_or_key_does_not_start_a_remote_drag(services):
    connected(services)
    peer = services[0]
    assert not peer.button(0, 1)
    peer.motion(1800, 250, 10, 0)
    assert not peer.motion(1919, 250, 119, 0)
    assert peer.session.phase == "local"
    peer.button(0, 0)
    peer.key(0xE0, 1)
    assert not peer.motion(1919, 250, 1, 0)
    peer.key(0xE0, 0)
    assert peer.motion(1919, 250, 1, 0)


def test_remote_keys_wheel_and_escape_chord(services):
    connected(services)
    peer, target = services
    enter(peer)
    assert peer.key(4, 1)
    assert peer.key(4, 0)
    assert peer.wheel(0, 1.5)
    for key in (0xE0, 0xE1, 0xE2):
        assert peer.key(key, 1)
    until(lambda: target.session.keys == {0xE0, 0xE1, 0xE2})
    assert peer.key(0x29, 1)
    until(lambda: target.session.phase == "local")
    assert not target.session.keys and not peer.backend.captured
    assert any(p.kind == Kind.WHEEL and p.values == (0, 1.5) for p in target.backend.events)


@pytest.mark.parametrize("action", ["motion", "key", "wheel", "button"])
def test_physical_input_at_destination_takes_local_control(services, action):
    connected(services)
    peer, target = services
    enter(peer)
    if action == "motion":
        assert not target.motion(100, 250, 1, 0)
    elif action == "key":
        assert not target.key(4, 1)
    elif action == "wheel":
        assert not target.wheel(0, 1)
    else:
        assert not target.button(0, 1)
    until(lambda: peer.session.phase == "local")
    assert not peer.backend.captured


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("kind,code", [("key", 0xE0), ("button", 0)])
def test_incoming_focus_does_not_interrupt_a_local_held_key_or_drag(services, source, kind, code):
    connected(services)
    peer, target = services[source], services[1 - source]
    assert not getattr(target, kind)(code, 1)
    assert peer.session.enter(*target.local.entry(target.side, 0.5))
    until(lambda: peer.session.phase != "requesting")
    assert peer.session.phase == target.session.phase == "local"
    assert not peer.backend.captured
    assert target.backend.events == []
    assert getattr(target, "keys" if kind == "key" else "buttons") == {code}
    assert not getattr(target, kind)(code, 0)
    enter(peer)
    assert target.session.phase == "receiving"


@pytest.mark.parametrize("source", [0, 1])
def test_permission_cadence_tracks_both_sides_of_focus_and_returns_to_idle(services, source):
    for peer in services:
        peer.backend.set_active = Mock()
    connected(services)
    peer, target = services[source], services[1 - source]
    enter(peer)
    for candidate in services:
        candidate.backend.set_active.assert_called_with(True)
    peer.session.leave()
    until(lambda: target.session.phase == "local")
    for candidate in services:
        candidate.backend.set_active.assert_called_with(False)


def test_mode_is_opt_in_and_constructing_it_does_not_start_capture():
    link = Mock(config={"role": "mac"})
    backend = DesktopBackend()
    service = Service(link, backend)
    assert backend.starts == 0
    with pytest.raises(ConnectionError):
        service.offer()
    assert not service.motion(0, 250, -1, 0)
    assert not service.connect()
    service.close()
    assert backend.starts == backend.closes == 0


def test_connect_is_idempotent_and_does_not_stop_a_healthy_backend(services):
    connected(services)
    peer = services[0]
    original = peer.channel
    assert peer.connect()
    assert peer.channel is original
    assert peer.backend.starts == 1 and peer.backend.closes == 0
    assert not services[1].connect()  # Only Windows initiates the link.


def test_native_failure_requests_shutdown_without_joining_in_callback(services):
    connected(services)
    enter(services[0])
    services[0].fail()
    until(lambda: services[0].channel.finished.is_set())
    until(lambda: services[1].channel.finished.is_set())
    assert not services[0].backend.captured
    assert all(peer.backend.closes == 1 for peer in services)


def test_malformed_motion_aborts_capture_without_forwarding(services):
    connected(services)
    enter(services[0])
    assert not services[0].motion(float("nan"), 0, 1, 0)
    assert services[0].channel.finished.wait(1)
    assert not services[0].backend.captured


def test_native_callback_does_not_wait_on_network_injection_lock(services):
    connected(services)
    peer = services[0]
    enter(peer)
    held, release = threading.Event(), threading.Event()

    def injector():
        with peer.session.lock:
            held.set()
            release.wait(1)

    worker = threading.Thread(target=injector)
    worker.start()
    try:
        assert held.wait(1)
        started = time.monotonic()
        assert peer.key(4, 1)
        assert time.monotonic() - started < 0.1
        assert not peer.channel.stop.is_set()
        assert peer.deferred.qsize() == 1
    finally:
        release.set()
        worker.join(1)
    until(lambda: services[1].session.keys == {4})
    assert peer.key(4, 0)
    until(lambda: not services[1].session.keys)


def test_display_change_cancels_focus_instead_of_using_stale_geometry(services):
    connected(services)
    peer = services[0]
    enter(peer)
    peer.backend.desktop = Desktop([Display(-1000, 0, 1000, 1080)])
    peer.next_geometry = 0
    peer._tick()
    assert peer.channel.finished.wait(1)
    assert not peer.backend.captured


def test_backend_is_not_started_before_authenticated_upgrade_and_disabled_gate():
    link = Mock(config={"role": "mac"})
    backend = DesktopBackend()
    enabled = [True]
    service = Service(link, backend, enabled=True, can_start=lambda: enabled[0])
    service.accept(service.offer())
    assert backend.starts == 0
    enabled[0] = False
    with pytest.raises(ConnectionError):
        service.accept(service.offer())
    assert backend.starts == 0
    service.close()


def test_deferred_epoch_is_checked_after_acquiring_the_session_lock():
    service = Service(Mock(config={"role": "windows"}), DesktopBackend())
    session = Session("windows", service.backend.desktop, service.backend, lambda packet: None)
    service.session = session
    session.epoch = 10
    delivered = []

    class CancelBeforeAcquiring:
        def __enter__(self):
            session.epoch = 0

        def __exit__(self, *args):
            pass

    def motion(peer, *args):
        delivered.append(True)

    session.lock = CancelBeforeAcquiring()
    service.deferred.put((motion, (1, 2, 3, 4), session, 10, session.local_generation, True, time.perf_counter()))
    service._drain()
    assert not delivered


@pytest.mark.parametrize("role", ["windows", "mac"])
def test_health_check_does_not_abort_focus_during_normal_lock_restoration(role):
    backend = DesktopBackend()
    backend.lock_state = lambda: (0, 7)
    service = Service(Mock(config={"role": role}), backend)
    service.session = Session(role, backend.desktop, backend, lambda packet: None)
    service.channel = Mock(failure=None)
    service.next_check = service.next_geometry = float("inf")
    entered, release = threading.Event(), threading.Event()

    def locks(state, mask):
        entered.set()
        assert release.wait(1)
        return ()

    backend.lock_keys = locks
    assert service.session.enter(100, 200)
    worker = threading.Thread(target=service.session.leave)
    worker.start()
    try:
        assert entered.wait(1)
        start = time.monotonic()
        service._tick()
        assert time.monotonic() - start < 0.1
        service.channel.request_close.assert_not_called()
    finally:
        release.set()
        worker.join(1)
    assert not worker.is_alive()
    service._tick()
    assert service.session.phase == "local" and service.session.pending_locks is None
    assert not backend.captured
    service.channel.request_close.assert_not_called()


@pytest.mark.parametrize("role", ["windows", "mac"])
def test_health_check_cancels_after_lock_restoration_actually_fails(role):
    backend = DesktopBackend()
    backend.lock_state = lambda: (0, 7)
    backend.lock_keys = Mock(side_effect=OSError("synthetic failure"))
    service = Service(Mock(config={"role": role}), backend)
    service.session = Session(role, backend.desktop, backend, lambda packet: None)
    service.channel = Mock(failure=None)
    service.next_check = service.next_geometry = float("inf")
    assert service.session.enter(100, 200)
    service.session.leave()
    service._tick()
    assert service.session.pending_locks == (0, 7)
    assert service.channel.failure == "cleanup"
    service.channel.request_close.assert_called_once()
