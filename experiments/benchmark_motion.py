"""Compare ordered motion and an encrypted motion lane in a fault model.

PYTHONPATH=src .venv/bin/python experiments/benchmark_motion.py

Times are simulated event times, not wall-clock measurements. Both variants see
the same transmission failure windows; the ordered model assumes 80 ms recovery
and holds later bytes. Datagram loss does not hold later independent messages.
This is neither an IP/TCP implementation nor a measurement of the home network.
Native input, focus negotiation, firewall and path probing are outside this lab.
"""

import collections
import heapq
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cleverswitch.flow.input_protocol import Kind, Packet  # noqa: E402
from experiments.motion_lane import Receiver, Sender, lab_codecs  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 0
        self.queue = []
        self.order = itertools.count()

    def call(self, when, callback):
        heapq.heappush(self.queue, (when, next(self.order), callback))

    def run(self, until):
        while self.queue and self.queue[0][0] <= until:
            self.now, _, callback = heapq.heappop(self.queue)
            callback()
        self.now = until


class Network:
    delay = 0.002
    recovery = 0.08

    def __init__(self, clock, profile):
        self.clock, self.profile = clock, profile
        self.datagrams = self.datagram_bytes = self.dropped = 0

    def due(self, *, unreliable=False, reverse=False):
        now = self.clock.now
        # Quantize only the profile lookup to avoid floating point boundaries.
        micros = round(now * 1_000_000)
        single = 200_000 <= micros < 201_000
        if self.profile == "udp_blocked" and unreliable:
            return None
        if self.profile == "receipts_blocked" and unreliable and reverse:
            return None
        if self.profile == "outage_80ms" and 200_000 <= micros < 280_000:
            return None if unreliable else 0.28 + self.delay
        loss = (
            self.profile == "one_lost_transmission"
            and single
            or self.profile == "four_lost_transmissions"
            and any(t <= micros < t + 1000 for t in (200_000, 400_000, 600_000, 800_000))
            or self.profile == "last_movement_lost"
            and 992_000 <= micros < 1_000_000
        )
        if loss:
            return None if unreliable else now + self.recovery + self.delay
        if self.profile == "one_delayed_transmission" and single:
            return now + self.recovery + self.delay
        if self.profile == "action_path_delayed" and not unreliable and 240_000 <= micros < 241_000:
            return now + self.recovery + self.delay
        return now + self.delay

    def datagram(self, raw, receive, *, reverse=False):
        self.datagrams += 1
        self.datagram_bytes += len(raw)
        due = self.due(unreliable=True, reverse=reverse)
        if due is None:
            self.dropped += 1
        else:
            self.clock.call(due, lambda: receive(raw))


class OrderedPath:
    def __init__(self, network, deliver):
        self.network, self.deliver = network, deliver
        self.queue = collections.deque()
        self.scheduled = False
        self.sent = self.coalesced = 0

    def send(self, packet):
        due = self.network.due()
        if self.queue:
            due = max(due, self.queue[-1][0])
        self.queue.append((due, packet))
        self.sent += 1
        self._schedule()

    def _schedule(self):
        if self.queue and not self.scheduled:
            self.scheduled = True
            self.network.clock.call(self.queue[0][0], self._deliver)

    def _deliver(self):
        self.scheduled = False
        batch = []
        while self.queue and self.queue[0][0] <= self.network.clock.now:
            _, packet = self.queue.popleft()
            # Give the ordered baseline receive-side coalescing too. Comparing
            # against every stale motion individually would exaggerate benefit.
            if (
                packet.kind == Kind.MOVE
                and batch
                and batch[-1].kind == Kind.MOVE
                and packet.epoch == batch[-1].epoch
                and getattr(packet, "action", None) == getattr(batch[-1], "action", None)
            ):
                batch[-1] = packet
                self.coalesced += 1
            else:
                batch.append(packet)
        for packet in batch:
            self.deliver(packet)
        self._schedule()


class Sink:
    def __init__(self, clock):
        self.clock = clock
        self.captured = {}
        self.position = (100, 100)
        self.ages, self.times, self.actions = [], [], []
        self.keys, self.buttons = set(), set()

    def inject(self, packet):
        if packet.kind == Kind.MOVE:
            self.position = packet.values
            self.ages.append((self.clock.now - self.captured[packet.values[0]]) * 1000)
            self.times.append(self.clock.now)
        else:
            held = self.keys if packet.kind == Kind.KEY else self.buttons
            code, down = packet.values
            held.add(code) if down else held.discard(code)
            self.actions.append((packet.kind, packet.values, self.position))


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[int((len(ordered) - 1) * fraction)], 3)


def run_case(mode, source, rate, profile, actions):
    clock = Clock()
    network = Network(clock, profile)
    sink = Sink(clock)
    sender = receiver = None
    timer_version = 0

    def arm_timer():
        nonlocal timer_version
        timer_version += 1
        version = timer_version
        if sender.deadline is not None:

            def tick():
                if version == timer_version:
                    sender.tick(clock.now)
                    arm_timer()

            clock.call(sender.deadline, tick)

    if mode == "hybrid":
        codecs = lab_codecs()

        def receipt(raw):
            sender.receive(raw, clock.now)
            arm_timer()

        receiver = Receiver(codecs[1 - source], lambda raw: network.datagram(raw, receipt, reverse=True), sink.inject)
        receiver.begin(7, (100, 100))
        reliable = OrderedPath(network, receiver.receive_reliable)
        sender = Sender(
            codecs[source], 7, (100, 100), lambda raw: network.datagram(raw, receiver.receive), reliable.send
        )
    else:
        reliable = OrderedPath(network, sink.inject)

    expected = []
    action_schedule = (
        {
            int(rate * 0.240): (Kind.BUTTON, (0, 1)),
            int(rate * 0.248): (Kind.BUTTON, (0, 0)),
            int(rate * 0.256): (Kind.KEY, (4, 1)),
            int(rate * 0.264): (Kind.KEY, (4, 0)),
        }
        if actions
        else {}
    )
    for index in range(rate):
        clock.run(index / rate)
        position = (101 + index, 100)
        sink.captured[position[0]] = clock.now
        if sender is None:
            reliable.send(Packet(Kind.MOVE, 7, position))
        else:
            sender.move(position, clock.now)
            arm_timer()
        if index in action_schedule:
            kind, values = action_schedule[index]
            expected.append((kind, values, position))
            if sender is None:
                reliable.send(Packet(kind, 7, values))
            else:
                sender.send_action(kind, *values)
    clock.run(2)
    if sink.position != (100 + rate, 100) or sink.actions != expected or sink.keys or sink.buttons:
        raise AssertionError("Final position, action order/position or release mismatch")
    if sender is not None and (sender.deadline is not None or receiver.pending is not None):
        raise AssertionError("The experiment left unbounded work")
    gaps = [(end - start) * 1000 for start, end in itertools.pairwise([0, *sink.times])]
    result = {
        "mode": mode,
        "direction": "windows_to_mac" if source == 0 else "mac_to_windows",
        "rate_hz": rate,
        "profile": profile,
        "includes_actions": actions,
        "offered_moves": rate,
        "delivered_moves": len(sink.times),
        "simulated_sample_age_ms": {"p99": percentile(sink.ages, 0.99), "max": round(max(sink.ages), 3)},
        "simulated_max_delivery_gap_ms": round(max(gaps), 3),
        "simulated_first_delivery_delay_ms": round(sink.times[0] * 1000, 3),
        "simulated_final_position_delay_ms": round((sink.times[-1] - (rate - 1) / rate) * 1000, 3),
        "datagrams_sent_including_receipts_and_retries": network.datagrams,
        "datagram_payload_bytes": network.datagram_bytes,
        "datagrams_dropped": network.dropped,
        "ordered_frames": reliable.sent,
        "ordered_moves_coalesced": reliable.coalesced,
        "final_mode": sender.mode if sender is not None else "reliable",
        "verified_actions_and_positions": len(expected),
        "final_position_correct": True,
        "no_pressed_input_or_pending_work": True,
    }
    if sender is not None:
        sender.stop()
        receiver.end()
    return result


def main():
    results = []
    profiles = (
        "clean",
        "one_lost_transmission",
        "four_lost_transmissions",
        "one_delayed_transmission",
        "outage_80ms",
        "last_movement_lost",
        "udp_blocked",
        "receipts_blocked",
        "action_path_delayed",
    )
    for profile in profiles:
        for rate in (125, 1000):
            for source in (0, 1):
                for actions in (False, True):
                    for mode in ("ordered", "hybrid"):
                        results.append(run_case(mode, source, rate, profile, actions))
    print(
        json.dumps(
            {
                "scope": "deterministic_fault_model_with_real_AEAD_no_native_input",
                "time_basis": "simulated_event_time_not_wall_clock",
                "model_one_way_delay_ms": Network.delay * 1000,
                "model_ordered_recovery_ms": Network.recovery * 1000,
                "not_measured": [
                    "real_TCP_retransmission",
                    "Wi-Fi",
                    "native_input",
                    "display",
                    "battery",
                    "TLS_UDP_bootstrap",
                ],
                "cases": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
