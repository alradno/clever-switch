"""Continuous edge return over real TLS/UDP and synthetic input only."""

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import Kind

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_hybrid import connect
from .test_input_hybrid import transports as transports
from .test_input_service import connected, enter
from .test_input_service import services as services
from .test_input_stream import until


@pytest.fixture(params=[False, True], ids=["reliable", "hybrid"])
def route(request, transports):
    peers, _ = transports

    def start(source, *, gap=False):
        sender, receiver = peers[source], peers[1 - source]
        sender.backend.desktop = Desktop([Display(0, 0, 1920, 1001)])
        displays = [Display(0, 0, 1440, 501)]
        if gap:
            displays.append(Display(0, 800, 1440, 501))
        receiver.backend.desktop = Desktop(displays)
        connect(peers) if request.param else connected(peers)
        enter(sender)
        return sender, receiver

    return start


def approach_return(sender, receiver, y):
    direction = -1 if sender.role == "windows" else 1
    x = 100 if direction == -1 else sender.remote.right - 100
    previous = sender.remote_position
    assert sender.motion(500, 400, x - previous[0], y - previous[1])
    until(lambda: receiver.backend.events[-1].values == (x, y))
    return direction


@pytest.mark.parametrize("source", [0, 1])
def test_return_preserves_remaining_motion_and_maps_height_at_the_crossing(route, source):
    sender, receiver = route(source)
    direction = approach_return(sender, receiver, 200)
    # Cross after 100 of 300 horizontal units: seam y=250/500 -> local y=500;
    # keep the remaining 200 horizontal and 100 vertical units on the source.
    assert sender.motion(500, 400, direction * 300, 150)
    until(lambda: receiver.session.phase == "local")
    expected_x = sender.local.right - 1 - 200 if source == 0 else sender.local.left + 1 + 200
    assert sender.backend.restored[-1] == pytest.approx((expected_x, 600))
    assert sender.session.phase == "local" and not sender.backend.captured


@pytest.mark.parametrize("source", [0, 1])
def test_fast_diagonal_returns_when_it_crosses_the_edge_before_leaving_the_vertical_range(route, source):
    sender, receiver = route(source)
    direction = approach_return(sender, receiver, 250)
    assert sender.motion(500, 400, direction * 300, 400)
    assert sender.session.phase == "local"
    until(lambda: receiver.session.phase == "local")
    expected_x = sender.local.right - 201 if source == 0 else sender.local.left + 201
    assert sender.backend.restored[-1] == pytest.approx((expected_x, sender.local.bottom))


@pytest.mark.parametrize("source", [0, 1])
def test_diagonal_cannot_transfer_through_a_gap_even_if_its_endpoint_aligns_with_a_display(route, source):
    sender, receiver = route(source, gap=True)
    direction = approach_return(sender, receiver, 450)
    # The trajectory meets the external edge at y=600, inside the gap. Its
    # endpoint y=900 is on the lower display; that must not authorize a return.
    assert sender.motion(500, 400, direction * 300, 450)
    assert sender.session.phase == "sending" and sender.backend.captured
    assert not sender.backend.restored
    point = (sender.remote.left if direction == -1 else sender.remote.right, 900)
    until(lambda: receiver.backend.events[-1].values == point)


@pytest.mark.parametrize("source", [0, 1])
def test_return_that_travels_into_local_screen_can_reverse_again_in_one_report(route, source):
    sender, receiver = route(source)
    direction = approach_return(sender, receiver, 200)
    assert sender.motion(500, 400, direction * 300, 150)
    until(lambda: receiver.session.phase == "local")
    x = sender.local.right if source == 0 else sender.local.left
    assert sender.motion(x, 600, -direction * 201, 0)
    until(lambda: sender.session.phase == "sending")
    assert receiver.session.phase == "receiving"


@pytest.mark.parametrize("source", [0, 1])
def test_small_return_keeps_parked_cursor_and_warp_from_bouncing_back(route, source):
    sender, receiver = route(source)
    direction = approach_return(sender, receiver, 200)
    assert sender.motion(500, 400, direction * 100, 0)
    until(lambda: receiver.session.phase == "local")
    x = sender.local.right if source == 0 else sender.local.left
    assert not sender.motion(x, 400, 0, 0)
    assert not sender.motion(x, 400, -direction, 0)
    assert sender.session.phase == "local" and not sender.backend.captured


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("held", [Kind.KEY, Kind.BUTTON], ids=["key", "button"])
def test_return_waits_until_local_key_or_drag_is_released(route, source, held):
    sender, receiver = route(source)
    direction = approach_return(sender, receiver, 250)
    emit = sender.key if held == Kind.KEY else sender.button
    code = 4 if held == Kind.KEY else 0
    assert emit(code, 1)
    assert sender.motion(500, 400, direction * 300, 400)
    assert sender.session.phase == "sending"
    assert emit(code, 0)
    assert sender.motion(500, 400, direction, 0)
    until(lambda: receiver.session.phase == "local")
    assert not receiver.session.keys and not receiver.session.buttons
    assert not sender.backend.captured
