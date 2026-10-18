"""Local motion must survive a short lock without crossing a retired focus."""

import threading
import time
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_protocol import Kind, Packet
from cleverswitch.flow.input_service import Service
from cleverswitch.flow.input_session import Session

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_end_to_end import motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_service import DesktopBackend
from .test_input_stream import until


@pytest.mark.parametrize("source", [0, 1])
def test_local_inward_and_edge_reports_survive_a_busy_session(native_peers, source):
    peers, _, _ = native_peers
    peer, receiver = peers[source], peers[1 - source]
    held, release = threading.Event(), threading.Event()

    def hold_session():
        with peer.session.lock:
            held.set()
            assert release.wait(1)

    worker = threading.Thread(target=hold_session)
    worker.start()
    try:
        assert held.wait(1)
        edge = 1919 if source == 0 else 0
        started = time.monotonic()
        motion(peer, edge + (-100 if source == 0 else 100), 400)
        motion(peer, edge, 400)
        assert time.monotonic() - started < 0.1
        assert peer.session.phase == "local"
    finally:
        release.set()
        worker.join(1)
    until(lambda: peer.session.phase == "sending", timeout=0.5)
    assert receiver.session.phase == "receiving"
    assert all(candidate._available() for candidate in peers)


@pytest.mark.parametrize("role", ["windows", "mac"])
@pytest.mark.parametrize("next_focus", [False, True], ids=["local", "new_outgoing"])
def test_local_motion_queued_before_an_entire_focus_cannot_rearm_or_cancel_a_later_focus(role, next_focus):
    backend = DesktopBackend()
    service = Service(Mock(config={"role": role}), backend, enabled=True)
    service.local = service.remote = backend.desktop
    service.started = True
    service.channel = Mock()
    service.channel.ready.is_set.return_value = True
    service.channel.stop.is_set.return_value = False
    sent = []
    service.session = Session(
        role, backend.desktop, backend, sent.append,
        on_active=lambda active: service._active(service.channel, active),
    )
    edge = 1919 if role == "windows" else 0
    inward = -100 if role == "windows" else 100
    service.draining.set()
    service.motion(edge + inward, 400, 1, 0)
    service.motion(edge, 400, -inward, 0)
    assert service.deferred.qsize() == 2
    service.session.receive(Packet(Kind.ENTER, 9001, (900, 400)))
    service.session.cancel("physical")
    if next_focus:
        assert service.session.enter(900, 400)
        epoch = service.session.epoch
        service.session.receive(Packet(Kind.ACCEPT, epoch))
    service._drain()
    assert not service.armed and service.deferred.empty()
    if next_focus:
        assert service.session.phase == "sending" and service.session.epoch == epoch
        assert backend.captured
        assert [packet.epoch for packet in sent if packet.kind == Kind.ENTER] == [epoch]
    else:
        assert service.session.phase == "local" and not backend.captured
        assert not any(packet.kind == Kind.ENTER for packet in sent)
