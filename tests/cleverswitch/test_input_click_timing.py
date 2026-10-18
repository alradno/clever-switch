"""Click intent through native API doubles and real TLS/UDP loopback only."""

import ctypes as c
import threading
import time

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import (
    HEADER,
    MAGIC,
    ROUTED_TIMED,
    TIMED,
    TIMING_CAPABILITY,
    VERSION,
    Decoder,
    Kind,
    Packet,
    Routed,
    supports_timing,
)
from cleverswitch.flow.input_session import Session
from cleverswitch.flow.input_windows import Input

from .test_flow import paired as paired
from .test_flow import paired_tls as paired_tls
from .test_input_core import Backend
from .test_input_device_continuity import busy_session, device_change
from .test_input_end_to_end import enter, motion
from .test_input_end_to_end import native_peers as native_peers
from .test_input_mixed_reports import button
from .test_input_native import mac as mac
from .test_input_native import win as win
from .test_input_service import services as services
from .test_input_stream import until


@pytest.mark.parametrize("stamp", [0, 123456789, 2**64 - 1])
@pytest.mark.parametrize("form", ["accept", "button", "routed"])
def test_timed_frames_survive_bytewise_fragmentation(stamp, form):
    packet = Packet(Kind.ACCEPT if form == "accept" else Kind.BUTTON, 5,
                    () if form == "accept" else (0, 1), stamp)
    frame = Routed(packet, 1, 3, (300, 400)) if form == "routed" else packet
    decoder = Decoder(routed=form == "routed", timed=True)
    result = []
    for byte in frame.encode(1):
        result.extend(decoder.feed(bytes((byte,))))
    assert result == [frame] and not decoder.buffer


@pytest.mark.parametrize("stamp", [-1, 2**64, True, 1.5, "1"])
def test_invalid_timestamps_are_not_serialized(stamp):
    with pytest.raises(ValueError):
        Packet(Kind.BUTTON, 1, (0, 1), stamp)


@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("timed", [False, True])
def test_extensions_require_each_independent_negotiation(routed, timed):
    packet = Packet(Kind.BUTTON, 1, (0, 1), 100)
    frame = Routed(packet, 1, 0, (10, 20))
    if not routed or not timed:
        with pytest.raises(ValueError):
            Decoder(routed=routed, timed=timed).feed(frame.encode(1))
    else:
        assert Decoder(routed=True, timed=True).feed(frame.encode(1)) == [frame]
    if not timed:
        with pytest.raises(ValueError):
            Decoder(routed=routed).feed(packet.encode(1))


@pytest.mark.parametrize("form", [TIMED, ROUTED_TIMED])
@pytest.mark.parametrize("mutation", ["short", "long", "wrong_kind"])
def test_timed_frames_reject_wrong_lengths_and_non_button_actions(form, mutation):
    packet = Packet(Kind.BUTTON, 1, (0, 1), 100)
    frame = Routed(packet, 1, 0, (10, 20)) if form == ROUTED_TIMED else packet
    payload = bytearray(frame.encode(1)[HEADER.size:])
    if mutation == "short":
        payload.pop()
    elif mutation == "long":
        payload.append(0)
    else:
        payload[0] = Kind.LOCKS  # Same value size, but not a timed action.
    raw = HEADER.pack(MAGIC, VERSION, form, len(payload), 1, 1) + payload
    with pytest.raises(ValueError):
        Decoder(routed=True, timed=True).feed(raw)


@pytest.mark.parametrize("value", [None, {}, {TIMING_CAPABILITY: True}, {TIMING_CAPABILITY: "1"},
                                  {TIMING_CAPABILITY: 2}, {TIMING_CAPABILITY: 1}])
def test_capability_requires_exact_version_integer(value):
    assert supports_timing(value) is (type(value) is dict and type(value.get(TIMING_CAPABILITY)) is int
                                     and value[TIMING_CAPABILITY] == 1)


@pytest.mark.parametrize("old_peer", ["initiator", "receiver"])
def test_legacy_peer_uses_unchanged_frames(services, old_peer):
    sender, receiver = services
    if old_peer == "receiver":
        accept = receiver.link.input_accept
        receiver.link.input_accept = lambda value: accept({k: v for k, v in value.items() if k != "click_timing"})
    assert sender.connect(receiver.capabilities() if old_peer == "receiver" else None)
    until(lambda: all(peer._available() for peer in services))
    assert all(not peer.session.timed_clicks and not peer.channel.timed_clicks for peer in services)
    assert sender.session.enter(20, 30)
    until(lambda: sender.session.phase == "sending")
    sender.button(0, 1)
    sender.button(0, 0)
    until(lambda: len(receiver.backend.events) == 3)
    assert all(packet.stamp is None for packet in receiver.backend.events)
    for packet in receiver.backend.events:
        assert Decoder().feed(packet.encode(1)) == [packet]


@pytest.mark.parametrize("value", [True, 0, 2, "1"])
def test_bad_timing_offer_is_rejected_before_native_start(services, value):
    offer = services[0].offer()
    offer["click_timing"] = value
    with pytest.raises(ValueError):
        services[1].accept(offer)
    assert not services[1].started


def test_unsolicited_timing_response_is_rejected_before_native_start(services):
    with pytest.raises(ValueError, match="no solicitado"):
        services[0].attach(None, services[1].offer(timed=True))
    assert not services[0].started and services[0].channel is None


@pytest.mark.parametrize("role", ["windows", "mac"])
@pytest.mark.parametrize("source_origin,target_origin", [(1000.0, 800000.0), (800000.0, 1000.0)])
def test_accept_maps_queued_clicks_without_round_trip_or_clock_origin_assumption(role, source_origin, target_origin):
    source_time, target_time = [source_origin], [target_origin]
    outgoing, replies = [], []
    desktop = Desktop([Display(0, 0, 1920, 1080)])
    sender = Session(role, desktop, Backend(), outgoing.append, clock=lambda: source_time[0], timed_clicks=True)
    receiver = Session("mac" if role == "windows" else "windows", desktop, Backend(), replies.append,
                       clock=lambda: target_time[0], timed_clicks=True)
    assert sender.enter(30, 40)
    sender.input(Kind.MOVE, 40, 40)
    assert [p.kind for p in outgoing] == [Kind.ENTER, Kind.MOVE]
    # A click occurs before ACCEPT; another remains distinct after acceptance.
    sender.input(Kind.BUTTON, 0, 1, captured_at=source_origin + .02)
    sender.input(Kind.BUTTON, 0, 0, captured_at=source_origin + .03)
    sender.input(Kind.MOVE, 50, 40)
    target_time[0] += .04
    receiver.receive(outgoing[0])
    receiver.receive(outgoing[1])
    source_time[0] += .10  # Asymmetric return delay, no clock synchronization.
    sender.receive(replies[0])
    sender.input(Kind.BUTTON, 0, 1, captured_at=source_origin + .32)
    sender.input(Kind.BUTTON, 0, 0, captured_at=source_origin + .33)
    clicks = [p for p in outgoing if p.kind == Kind.BUTTON]
    assert clicks[0].stamp == pytest.approx((target_origin - .04) * 1e6, abs=2)
    assert clicks[2].stamp - clicks[0].stamp == pytest.approx(300000, abs=2)
    # Compress delivery, then expand it: injection retains the captured times.
    for index, packet in enumerate(outgoing[2:]):
        target_time[0] += .001 if index < 3 else .3
        receiver.receive(packet)
    assert [p for p in receiver.backend.events if p.kind == Kind.BUTTON] == clicks
    assert not receiver.buttons
    assert sender.leave()
    receiver.receive(outgoing[-1])
    assert sender.click_offset is None and receiver.last_click_stamp is None
    source_time[0] += 100
    target_time[0] += 101
    assert sender.enter(30, 40)
    receiver.receive(outgoing[-1])
    sender.receive(replies[-1])
    assert sender.click_offset == int(target_time[0] * 1e6) - int(source_time[0] * 1e6)


@pytest.mark.parametrize("fault", ["missing", "backwards"])
def test_invalid_button_timing_cancels_focus_and_releases_its_input(fault):
    backend, sent = Backend(), []
    session = Session("mac", Desktop([Display(0, 0, 1920, 1080)]), backend, sent.append, timed_clicks=True)
    session.receive(Packet(Kind.ENTER, 1, (20, 30)))
    session.receive(Packet(Kind.KEY, 1, (4, 1)))
    session.receive(Packet(Kind.BUTTON, 1, (0, 1), 100))
    session.receive(Packet(Kind.BUTTON, 1, (0, 0), None if fault == "missing" else 99))
    assert session.phase == "local" and session.failure == "timing"
    assert not session.keys and not session.buttons
    assert [p.values for p in backend.events[-2:]] == [(4, 0), (0, 0)]
    assert sent[-1].kind == Kind.RESET


def test_missing_accept_timestamp_retires_pending_clicks():
    sent = []
    session = Session("windows", Desktop([Display(0, 0, 1920, 1080)]), Backend(), sent.append, timed_clicks=True)
    assert session.enter(20, 30)
    session.input(Kind.BUTTON, 0, 1)
    session.receive(Packet(Kind.ACCEPT, session.epoch))
    assert session.phase == "local" and session.failure == "timing"
    assert not session.pending.pending() and all(p.kind != Kind.BUTTON for p in sent)


@pytest.mark.parametrize("ticks", [10000, 2**32 + 250, 2**32 + 300, 2**33 + 250])
def test_windows_backdates_clicks_and_handles_tick_wrap_without_zero(win, ticks):
    backend, api = win
    backend.clock = lambda: 10.0
    backend.kernel.GetTickCount64.return_value = ticks
    backend.inject(Packet(Kind.BUTTON, 1, (0, 1), 9750000))
    expected = (ticks - 250) & 0xFFFFFFFF
    assert api.inputs[-1].mouse.time == (expected or 0xFFFFFFFF)
    assert backend.kernel.GetTickCount64.restype is c.c_uint64


@pytest.mark.parametrize("stamp", [0, 11000000, 2**64 - 1])
def test_native_timestamps_never_schedule_future_or_negative_events(win, mac, stamp):
    windows, api = win
    windows.clock = lambda: 10.0
    windows.kernel.GetTickCount64.return_value = 10000
    windows.inject(Packet(Kind.BUTTON, 1, (0, 1), stamp))
    assert 0 < api.inputs[-1].mouse.time <= 10000
    backend, q = mac
    backend.clock = lambda: 10.0
    q.CGEventGetTimestamp = lambda event: 5000000000
    backend.inject(Packet(Kind.BUTTON, 1, (0, 1), stamp))
    assert 0 <= q.events[-1]["timestamp"] <= 5000000000


def test_mac_backdates_from_quartz_origin_and_rejects_negative_click_intervals(mac):
    backend, q = mac
    backend.clock = lambda: 1000.0
    q.CGEventGetTimestamp = lambda event: 5000000000
    backend.inject(Packet(Kind.BUTTON, 1, (0, 1), 998750000))
    assert q.events[-1]["timestamp"] == 3750000000
    backend.inject(Packet(Kind.BUTTON, 1, (0, 0), 998760000))
    backend.inject(Packet(Kind.BUTTON, 1, (0, 1), 998700000))
    assert q.events[-1][q.kCGMouseEventClickState] == 1


def test_legacy_and_movement_paths_do_not_query_native_click_clocks(win, mac):
    windows, api = win
    for packet in (Packet(Kind.MOVE, 1, (30, 40)), Packet(Kind.WHEEL, 1, (1, 1)),
                   Packet(Kind.BUTTON, 1, (0, 1))):
        windows.inject(packet)
    windows.kernel.GetTickCount64.assert_not_called()
    assert all(item.mouse.time == 0 for item in api.inputs)
    backend, q = mac
    backend.inject(Packet(Kind.BUTTON, 1, (0, 1)))
    assert q.events[-1][q.kCGMouseEventClickState] == 1


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize("profile", ["compressed", "expanded"])
def test_click_intent_survives_receiver_pause(native_peers, source, profile):
    peers, windows, mac_api = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    channels = [peer.channel for peer in peers]
    assert all(peer.session.timed_clicks for peer in peers)
    receiver.backend.double_click = lambda: .2
    downs, captures, received = [], [], []
    submit = windows.SendInput.side_effect

    def window_manager(count, ptr, size):
        result = submit(count, ptr, size)
        entry = c.cast(ptr, c.POINTER(Input)).contents
        if entry.kind == 0 and entry.mouse.flags == 2:
            stamp = entry.mouse.time or (int(time.monotonic() * 1000) & 0xFFFFFFFF)
            received.append(2 if downs and ((stamp - downs[-1]) & 0xFFFFFFFF) <= 200 else 1)
            downs.append(stamp)
        return result

    windows.SendInput.side_effect = window_manager
    inject, down_count = receiver.backend.inject, [0]

    def delayed(packet):
        if packet.kind == Kind.BUTTON and packet.values == (0, 1):
            down_count[0] += 1
            if down_count[0] == (1 if profile == "compressed" else 2):
                time.sleep(.18)
        inject(packet)
        if source == 0 and packet.kind == Kind.BUTTON and packet.values == (0, 1):
            received.append(mac_api.events[-1][mac_api.kCGMouseEventClickState])

    receiver.backend.inject = delayed
    for index in range(2):
        if index:
            time.sleep(.28 if profile == "compressed" else .08)
        captures.append(time.monotonic())
        button(sender, 0, 1)
        button(sender, 0, 0)
    until(lambda: len(received) == 2)
    assert (captures[1] - captures[0] > .2) is (profile == "compressed")
    assert received == [1, 1 if profile == "compressed" else 2]
    until(lambda: not receiver.session.buttons)
    sender.session.leave()
    until(lambda: receiver.session.phase == "local")
    enter(sender)
    assert all(peer.channel is channel and peer._available() for peer, channel in zip(peers, channels, strict=True))


@pytest.mark.parametrize("source", [0, 1])
def test_deferred_click_and_device_release_keep_callback_time_and_order(native_peers, source):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    enter(sender)
    observed = []
    inject = receiver.backend.inject

    def record(packet):
        inject(packet)
        if packet.kind == Kind.BUTTON:
            observed.append(packet)

    receiver.backend.inject = record
    with busy_session(sender, True):
        button(sender, 0, 1)
        device_change(sender, "mouse")
        button(sender, 0, 1)
        button(sender, 0, 0)
        captured_before = time.monotonic()
        time.sleep(.03)
    until(lambda: len(observed) == 4)
    assert [p.values for p in observed] == [(0, 1), (0, 0), (0, 1), (0, 0)]
    assert [p.stamp for p in observed] == sorted(p.stamp for p in observed)
    assert all(p.stamp <= int(captured_before * 1e6) + sender.session.click_offset for p in observed)
    assert not receiver.session.buttons and all(peer._available() for peer in peers)


@pytest.mark.parametrize("source", [0, 1])
def test_click_waiting_for_accept_keeps_its_capture_time(native_peers, source):
    peers, _, _ = native_peers
    sender, receiver = peers[source], peers[1 - source]
    reached, resume = threading.Event(), threading.Event()
    accept, inject = receiver.session._accept, receiver.backend.inject
    observed = []

    def paused(epoch):
        reached.set()
        assert resume.wait(1)
        accept(epoch)

    def record(packet):
        inject(packet)
        if packet.kind == Kind.BUTTON:
            observed.append(packet)

    receiver.session._accept = paused
    receiver.backend.inject = record
    try:
        x = 1919 if source == 0 else 0
        motion(sender, x + (-100 if source == 0 else 100), 400)
        motion(sender, x, 400)
        assert reached.wait(1)
        assert sender.session.phase == "requesting"
        captured_after = time.monotonic()
        button(sender, 0, 1)
        button(sender, 0, 0)
        captured_before = time.monotonic()
        assert not observed
        time.sleep(.03)
    finally:
        resume.set()
    until(lambda: len(observed) == 2 and sender.session.phase == "sending")
    offset = sender.session.click_offset
    assert [p.values for p in observed] == [(0, 1), (0, 0)]
    assert all(int(captured_after * 1e6) + offset <= p.stamp <= int(captured_before * 1e6) + offset
               for p in observed)
    assert not receiver.session.buttons and all(peer._available() for peer in peers)
