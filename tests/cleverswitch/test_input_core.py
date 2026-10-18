import collections
import random
import struct

import pytest

from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import HEADER, Decoder, Kind, Outbox, Packet
from cleverswitch.flow.input_session import Session


class Backend:
    def __init__(self):
        self.available = True
        self.captured = False
        self.events = []
        self.fail = False

    def ready(self):
        return self.available

    def capture(self, value):
        self.captured = value

    def inject(self, packet):
        self.events.append(packet)
        if self.fail:
            raise OSError("synthetic failure")


def pair():
    desktop = Desktop([Display(0, 0, 1920, 1080)])
    queues = [collections.deque(), collections.deque()]
    backends = [Backend(), Backend()]
    sessions = [
        Session(role, desktop, backends[i], queues[1 - i].append, token=lambda i=i: 100 + i)
        for i, role in enumerate(("windows", "mac"))
    ]

    def drain():
        for _ in range(1024):
            progress = False
            for session, queue in zip(sessions, queues, strict=True):
                if queue:
                    session.receive(queue.popleft())
                    progress = True
            if not progress:
                return
        raise AssertionError("message loop")

    return sessions, backends, drain


@pytest.mark.parametrize("kind", list(Kind))
def test_frames_roundtrip_in_every_fragment_size(kind):
    values = (
        (17.5, -4.25)
        if kind in (Kind.ENTER, Kind.MOVE, Kind.WHEEL)
        else (4, 1)
        if kind in (Kind.KEY, Kind.BUTTON, Kind.LOCKS)
        else ()
    )
    packet = Packet(kind, 23, values)
    encoded = packet.encode(1)
    for fragment in range(1, len(encoded) + 1):
        decoder = Decoder()
        result = []
        for start in range(0, len(encoded), fragment):
            result += decoder.feed(encoded[start : start + fragment])
        assert result == [packet]
        assert not decoder.buffer


@pytest.mark.parametrize("part,value", [(0, b"FAIL"), (1, 99), (2, 0), (3, 9999), (4, 0), (5, 0)])
def test_invalid_headers_rejected_without_input_contents_in_error(part, value):
    header = [b"CSFI", 1, Kind.KEY, 3, 1, 10]
    header[part] = value
    with pytest.raises(ValueError) as error:
        Decoder().feed(HEADER.pack(*header) + struct.pack("!HB", 4, 1))
    assert "9999" not in str(error.value)


@pytest.mark.parametrize("values", [(float("nan"), 1), (0, float("inf")), (True, 0), (1e9, 0), (0,)])
def test_malformed_positions_rejected(values):
    with pytest.raises(ValueError):
        Packet(Kind.MOVE, 1, values)


@pytest.mark.parametrize(
    "kind,values", [(Kind.KEY, (0, 1)), (Kind.KEY, (4, 2)), (Kind.KEY, (4, True)), (Kind.BUTTON, (5, 1))]
)
def test_only_bounded_physical_inputs_accepted(kind, values):
    with pytest.raises(ValueError):
        Packet(kind, 1, values)


def test_replays_and_missing_frames_rejected():
    decoder = Decoder()
    raw = Packet(Kind.KEY, 1, (4, 1)).encode(1)
    assert len(decoder.feed(raw)) == 1
    with pytest.raises(ValueError):
        decoder.feed(raw)
    with pytest.raises(ValueError):
        Decoder().feed(Packet(Kind.RESET, 1).encode(2))


def test_packet_repr_does_not_disclose_keys_or_coordinates():
    packet = Packet(Kind.KEY, 9827, (94, 1))
    assert repr(packet) == object.__repr__(packet)


def test_motion_coalesces_only_until_next_action_and_preserves_click_position():
    queue = Outbox(4)
    queue.put(Packet(Kind.MOVE, 1, (1, 2)))
    queue.put(Packet(Kind.MOVE, 1, (50, 60)))
    queue.put(Packet(Kind.BUTTON, 1, (0, 1)))
    queue.put(Packet(Kind.MOVE, 1, (75, 80)))
    queue.put(Packet(Kind.MOVE, 2, (90, 100)))
    with pytest.raises(BufferError):
        queue.put(Packet(Kind.KEY, 2, (4, 1)))
    assert queue.coalesced == 1
    assert queue.pop().values == (50, 60)
    assert queue.pop().kind == Kind.BUTTON
    assert queue.pop().values == (75, 80)
    assert queue.pop().epoch == 2
    assert queue.pop() is None
    queue.close()
    with pytest.raises(ConnectionError):
        queue.put(Packet(Kind.RESET, 1))


@pytest.mark.parametrize("source", [0, 1], ids=["windows_to_mac", "mac_to_windows"])
def test_handoff_injects_only_after_destination_acceptance_and_cleans_held_keys(source):
    sessions, backends, drain = pair()
    sender, receiver = sessions[source], sessions[1 - source]
    assert sender.enter(10, 20)
    assert sender.input(Kind.KEY, 0xE0, 1)
    assert sender.input(Kind.BUTTON, 0, 1)
    assert not backends[1 - source].events
    drain()
    assert sender.phase == "sending" and receiver.phase == "receiving"
    assert receiver.keys == {0xE0} and receiver.buttons == {0}
    assert sender.leave()
    drain()
    assert not receiver.keys and not receiver.buttons
    assert not any(b.captured for b in backends)
    assert [p.values for p in backends[1 - source].events[-2:]] == [(0xE0, 0), (0, 0)]


@pytest.mark.parametrize("source", [0, 1])
def test_rejected_handoff_discards_pipelined_input(source):
    sessions, backends, drain = pair()
    sender, receiver = sessions[source], sessions[1 - source]
    backends[1 - source].available = False
    assert sender.enter(10, 20)
    assert sender.input(Kind.MOVE, 30, 40)
    assert sender.input(Kind.KEY, 4, 1)
    assert sender.input(Kind.BUTTON, 0, 1)
    drain()
    assert sender.phase == receiver.phase == "local"
    assert not any(backend.captured or backend.events for backend in backends)
    assert not receiver.keys and not receiver.buttons


def test_simultaneous_sources_choose_exactly_one_focus():
    sessions, backends, drain = pair()
    assert sessions[0].enter(10, 20)
    assert sessions[1].enter(1900, 20)
    drain()
    assert [s.phase for s in sessions] == ["sending", "receiving"]
    assert [b.captured for b in backends] == [True, False]


def test_simultaneous_sources_only_inject_the_winning_pipelined_input():
    sessions, backends, drain = pair()
    assert sessions[0].enter(10, 20)
    assert sessions[1].enter(1900, 20)
    assert sessions[0].input(Kind.KEY, 4, 1)
    assert sessions[1].input(Kind.KEY, 5, 1)
    drain()
    assert [s.phase for s in sessions] == ["sending", "receiving"]
    assert not backends[0].events
    assert sessions[1].keys == {4}
    assert not any(p.kind == Kind.KEY and p.values[0] == 5 for backend in backends for p in backend.events)
    sessions[0].leave()
    drain()
    assert not sessions[1].keys


def test_early_pointer_never_overtakes_an_unconfirmed_action():
    desktop = Desktop([Display(0, 0, 1920, 1080)])
    sent = []
    session = Session("windows", desktop, Backend(), sent.append)
    assert session.enter(100, 200)
    assert session.input(Kind.MOVE, 120, 220)
    actions = [(Kind.BUTTON, (0, 1)), (Kind.MOVE, (140, 240)), (Kind.BUTTON, (0, 0))]
    for kind, values in actions:
        assert session.input(kind, *values)
    assert [(p.kind, p.values) for p in sent] == [(Kind.ENTER, (100, 200)), (Kind.MOVE, (120, 220))]
    session.receive(Packet(Kind.ACCEPT, session.epoch))
    assert [(p.kind, p.values) for p in sent[2:]] == actions


def test_permission_loss_releases_input_and_restores_source():
    sessions, backends, drain = pair()
    sessions[0].enter(10, 20)
    drain()
    sessions[0].input(Kind.KEY, 0xE1, 1)
    drain()
    backends[1].available = False
    sessions[0].input(Kind.MOVE, 15, 25)
    drain()
    assert sessions[1].keys == set()
    assert [s.phase for s in sessions] == ["local", "local"]
    assert backends[1].events[-1].values == (0xE1, 0)


def test_broken_network_and_pending_handoff_are_bounded():
    desktop = Desktop([Display(0, 0, 1920, 1080)])
    now = [0.0]
    sent = []
    backend = Backend()
    session = Session("mac", desktop, backend, sent.append, clock=lambda: now[0])
    assert session.enter(100, 100)
    session.input(Kind.KEY, 4, 1)
    now[0] = 0.401
    session.tick()
    assert session.phase == "local" and not backend.captured
    assert sent[-1].kind == Kind.RESET
    before = len(sent)
    session.receive(Packet(Kind.ACCEPT, sent[0].epoch))
    assert session.phase == "local" and not backend.captured
    assert not session.input(Kind.KEY, 4, 0)
    assert len(sent) == before


def test_repeated_and_stale_responses_do_not_warp_cursor_or_reset_new_focus():
    sessions, backends, drain = pair()
    sessions[0].enter(10, 20)
    drain()
    sessions[1].receive(Packet(Kind.ENTER, 100, (10, 20)))
    assert len(backends[1].events) == 1
    sessions[1].receive(Packet(Kind.RESET, 999))
    assert sessions[1].phase == "receiving"
    sessions[1].receive(Packet(Kind.KEY, 999, (4, 1)))
    assert not sessions[1].keys


def test_injection_failure_releases_all_keys_without_retry_loop():
    sessions, backends, drain = pair()
    sessions[0].enter(10, 20)
    drain()
    sessions[0].input(Kind.KEY, 0xE0, 1)
    sessions[0].input(Kind.KEY, 0xE1, 1)
    drain()
    backends[1].fail = True
    before = len(backends[1].events)
    with pytest.raises(ConnectionError):
        sessions[1].receive(Packet(Kind.KEY, 100, (4, 1)))
    assert len(backends[1].events) - before == 4  # One failed event, three releases; no loops.
    assert sessions[1].phase == "local" and not sessions[1].keys
    assert len(sessions[1].unreleased) == 3
    backends[1].fail = False
    assert sessions[1].retry_releases()
    assert len(backends[1].events) - before == 7
    assert not sessions[1].unreleased


def test_retired_focus_cannot_reenter_after_delayed_retry():
    sessions, backends, drain = pair()
    sessions[0].enter(10, 20)
    drain()
    sessions[0].leave()
    drain()
    before = len(backends[1].events)
    sessions[1].receive(Packet(Kind.ENTER, 100, (10, 20)))
    assert sessions[1].phase == "local"
    assert len(backends[1].events) == before


def test_receiver_never_releases_a_key_it_did_not_press():
    sessions, backends, drain = pair()
    sessions[0].enter(10, 20)
    drain()
    sessions[0].input(Kind.KEY, 0xE0, 0)
    drain()
    assert len(backends[1].events) == 1  # Entry pointer only.


def test_fuzz_fragmented_ordered_input_preserves_actions():
    rng = random.Random(215)
    packets = []
    for _ in range(2000):
        code = rng.choice([4, 5, 44, 0xE0, 0xE1])
        packets.extend((Packet(Kind.KEY, 123, (code, 1)), Packet(Kind.KEY, 123, (code, 0))))
    wire = b"".join(packet.encode(i) for i, packet in enumerate(packets, 1))
    decoder = Decoder()
    result = []
    while wire:
        length = rng.randint(1, 1024)
        result.extend(decoder.feed(wire[:length]))
        wire = wire[length:]
    assert result == packets


def test_multiple_displays_negative_coordinates_gaps_and_entry():
    desktop = Desktop([Display(-1920, 0, 1920, 1080), Display(0, 100, 2560, 1440)])
    assert desktop.entry("left", 0.5) == (-1919, 769.5)
    assert desktop.entry("right", 0) == (2558, 100)
    assert desktop.clamp(200, 0) == (200, 100)
    assert desktop.crossing("left", -1920, 500, -1) == pytest.approx(500 / 1539)
    assert desktop.crossing("left", -1920, 500, 1) is None
    assert desktop.crossing("right", 2559, 0, 1) is None
    assert Desktop.from_wire(desktop.wire()).displays == desktop.displays


@pytest.mark.parametrize("value", [[], [None], [{"x": 0}], [dict(x=0, y=0, width=0, height=1080)]])
def test_malformed_desktop_refused(value):
    with pytest.raises(ValueError):
        Desktop.from_wire(value)


class LockBackend(Backend):
    def __init__(self, state=0, supported=7):
        super().__init__()
        self.state, self.supported = state, supported
        self.held = set()
        self.fail_up_once = False

    def lock_state(self):
        return self.state, self.supported

    def lock_keys(self, state, mask):
        difference = (state ^ self.state) & self.supported & mask
        return tuple(code for code, bit in ((57, 1), (83, 2), (71, 4)) if difference & bit)

    def inject(self, packet):
        super().inject(packet)
        if packet.kind == Kind.KEY:
            code, down = packet.values
            if not down and self.fail_up_once:
                self.fail_up_once = False
                raise PermissionError
            if down and code not in self.held:
                self.state ^= {57: 1, 83: 2, 71: 4}.get(code, 0)
            self.held.add(code) if down else self.held.discard(code)


def test_lock_state_sync_precedes_keys_and_returns_to_physical_source_without_retoggle():
    sessions, _, drain = pair()
    a, b = LockBackend(1), LockBackend(0)
    sessions[0].backend, sessions[1].backend = a, b
    assert sessions[0].enter(10, 20)
    sessions[0].input(Kind.KEY, 4, 1)
    drain()
    assert b.state == 1
    keys = [p.values for p in b.events if p.kind == Kind.KEY]
    assert keys[:3] == [(57, 1), (57, 0), (4, 1)]
    sessions[0].input(Kind.KEY, 4, 0)
    sessions[0].input(Kind.KEY, 57, 1)
    sessions[0].input(Kind.KEY, 57, 1)  # Typematic must not toggle twice.
    sessions[0].input(Kind.KEY, 57, 0)
    drain()
    assert b.state == 0 and a.state == 1
    sessions[0].leave()
    drain()
    assert a.state == b.state == 0
    assert not a.held and not b.held


def test_caps_only_source_does_not_clear_windows_numlock():
    sessions, _, drain = pair()
    sessions[0].backend = LockBackend(1, supported=1)
    sessions[1].backend = LockBackend(2)
    sessions[0].enter(10, 20)
    drain()
    assert sessions[1].backend.state == 3


def test_partial_lock_restoration_preserves_release_and_does_not_double_toggle():
    sessions, _, drain = pair()
    source, receiver = LockBackend(0), LockBackend(0)
    sessions[0].backend, sessions[1].backend = source, receiver
    sessions[0].enter(10, 20)
    drain()
    sessions[0].input(Kind.KEY, 57, 1)
    sessions[0].input(Kind.KEY, 57, 0)
    drain()
    source.fail_up_once = True
    sessions[0].leave()
    assert source.state == 1 and sessions[0].unreleased == {(Kind.KEY, 57)}
    assert sessions[0].pending_locks == (1, 7)
    assert sessions[0].retry_releases()
    assert source.state == 1 and not source.held
    assert sessions[0].pending_locks is None


@pytest.mark.parametrize("values", [(8, 1), (1, 8), (True, 1), (1,), (-1, 1)])
def test_invalid_lock_masks_are_rejected(values):
    with pytest.raises(ValueError):
        Packet(Kind.LOCKS, 1, values)


def test_failed_cursor_restoration_blocks_a_new_focus_until_released():
    sessions, backends, drain = pair()
    sessions[0].enter(10, 20)
    drain()
    capture = backends[0].capture

    def denied(value):
        if not value:
            raise PermissionError
        capture(value)

    backends[0].capture = denied
    sessions[0].leave()
    assert sessions[0].pending_capture
    assert not sessions[0].enter(15, 25)
    backends[0].capture = capture
    assert sessions[0].retry_releases()
    assert not sessions[0].pending_capture and not backends[0].captured


def test_collision_does_not_accept_new_owner_if_previous_capture_cannot_release():
    sessions, backends, drain = pair()
    assert sessions[0].enter(10, 20)
    assert sessions[1].enter(1900, 20)
    original = backends[1].capture

    def failed_release(value):
        if not value:
            raise PermissionError
        original(value)

    backends[1].capture = failed_release
    drain()
    assert sessions[1].pending_capture
    assert sessions[1].phase != "receiving"
    assert not backends[1].events  # No remote injection while old capture owns input.
