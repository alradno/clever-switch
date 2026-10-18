"""Measure synthetic input through end-to-end TLS and a delayed byte relay.

PYTHONPATH=src .venv/bin/python benchmarks/input_network.py

The relay accepts ciphertext before delaying delivery. This models ordered
stream queueing that is no longer replaceable in the application's outbox.
It does not emulate IP packet loss, retransmission, Wi-Fi or native input.
Only temporary test identities and loopback sockets are used.
"""

import collections
import contextlib
import itertools
import json
import select
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.input_stream import MemoryInput, percentile, until  # noqa: E402
from cleverswitch.flow.identity import Pairing  # noqa: E402
from cleverswitch.flow.input_protocol import Kind  # noqa: E402
from cleverswitch.flow.input_service import Service  # noqa: E402
from cleverswitch.flow.network import Discovery, Link  # noqa: E402


class Relay:
    """One bounded FIFO per direction; TLS remains between the actual peers."""

    capacity = 512 * 1024

    def __init__(self, target):
        self.target = target
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.profile = None
        self.failure = None
        self.held_bytes = self.peak_queued_bytes = 0
        self.worker = threading.Thread(target=self._run, name="benchmark-relay", daemon=True)
        self.worker.start()

    def arm(self, source, profile):
        self.profile = (time.perf_counter(), source, profile)

    def _due(self, direction, received, count, size):
        setting = self.profile
        if setting is None:
            return received
        start, source, profile = setting
        if direction != source:
            return received
        if profile in ("stall_80ms", "stall_after_recovery_80ms"):
            offset = 0.6 if profile == "stall_after_recovery_80ms" else 0.2
            if start + offset <= received < start + offset + 0.08:
                self.held_bytes += size
                return start + offset + 0.08
        elif profile == "variable_2_to_18ms":
            # Delay is assigned per ciphertext read, not per IP packet. FIFO
            # ordering holds faster later reads behind an earlier delayed one.
            return received + (0.002, 0.006, 0.018, 0.004)[count % 4]
        return received

    def _run(self):
        try:
            while not self.stop.is_set():
                try:
                    incoming, _ = self.listener.accept()
                    break
                except TimeoutError:
                    continue
            else:
                return
            with incoming, socket.create_connection(("127.0.0.1", self.target), timeout=2) as outgoing:
                sockets = [incoming, outgoing]
                for connection in sockets:
                    connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    connection.setblocking(False)
                queues = [collections.deque(), collections.deque()]
                buffered, counts = [0, 0], [0, 0]
                while not self.stop.is_set():
                    now = time.perf_counter()
                    writable = [sockets[1 - i] for i in range(2) if queues[i] and queues[i][0][0] <= now]
                    next_due = [queue[0][0] for queue in queues if queue and queue[0][0] > now]
                    wait = min(0.05, max(0, min(next_due) - now)) if next_due else 0.05
                    readable, writable, _ = select.select(sockets, writable, [], wait)
                    for i, connection in enumerate(sockets):
                        if connection in readable:
                            try:
                                raw = connection.recv(8192)
                            except BlockingIOError:
                                continue
                            if not raw:
                                return
                            buffered[i] += len(raw)
                            if buffered[i] > self.capacity:
                                raise BufferError("Relay capacity exceeded")
                            due = self._due(i, time.perf_counter(), counts[i], len(raw))
                            if queues[i]:
                                due = max(due, queues[i][-1][0])
                            queues[i].append((due, raw))
                            counts[i] += 1
                            self.peak_queued_bytes = max(self.peak_queued_bytes, sum(buffered))
                    for i in range(2):
                        target = sockets[1 - i]
                        while target in writable and queues[i] and queues[i][0][0] <= time.perf_counter():
                            due, raw = queues[i][0]
                            try:
                                count = target.send(raw)
                            except BlockingIOError:
                                break
                            if not count:
                                raise ConnectionError("Relay closed")
                            buffered[i] -= count
                            if count == len(raw):
                                queues[i].popleft()
                            else:
                                queues[i][0] = (due, raw[count:])
                                break
        except Exception as error:
            if not self.stop.is_set():
                self.failure = type(error).__name__
        finally:
            self.listener.close()

    def close(self):
        self.stop.set()
        self.worker.join(timeout=3)
        if self.worker.is_alive():
            raise RuntimeError("Relay did not terminate")


class ObservedInput(MemoryInput):
    def __init__(self):
        super().__init__()
        self.deliveries = []
        self.actions = []
        self.position = None

    def inject(self, packet):
        if packet.kind == Kind.MOVE:
            self.position = packet.values
            stamp = self.pending.get(round(packet.values[0]))
            if stamp is not None:
                self.deliveries.append((time.perf_counter(), stamp))
        elif packet.kind in (Kind.KEY, Kind.BUTTON):
            self.actions.append((packet.kind, packet.values, self.position))
        super().inject(packet)


@contextlib.contextmanager
def connected(directory, *, hybrid=False, prepare_transport=None):
    pairings = [Pairing(directory / role, testing=True) for role in ("windows", "mac")]
    invitation, response = directory / "invitation.json", directory / "response.json"
    pairings[0].create_invitation(invitation, "127.0.0.1", "127.0.0.0/8")
    pairings[1].accept_invitation(invitation, response, "127.0.0.2", "127.0.0.0/8")
    pairings[0].accept_response(response)
    for pairing in pairings:
        config = pairing.load()
        config["address"] = config["peer"]["address"] = "127.0.0.1"
        pairing.save(config)
    links = [Link(pairing, lambda op, args: {}, port=0) for pairing in pairings]
    services = [Service(link, ObservedInput(), enabled=True) for link in links]
    discoveries = [Discovery(link, 0) for link in links] if hybrid else []
    relay = None
    try:
        for link in links:
            link.start()
        for discovery in discoveries:
            discovery.start()
        if discoveries:
            discoveries[0].peer_port, discoveries[1].peer_port = discoveries[1].port, discoveries[0].port
            if prepare_transport is not None:
                prepare_transport(discoveries)
        relay = Relay(links[1].port)
        links[1].port, links[0].port = links[0].port, relay.port
        services[0].connect(services[1].capabilities())
        until(lambda: all(service._available() for service in services))
        if hybrid:
            until(lambda: all(service.channel.lane.probe.state == "ready" for service in services))
        if any(service.channel.connection.version() != "TLSv1.3" for service in services):
            raise RuntimeError("End-to-end TLS was not established")
        yield services, relay
    finally:
        for service in services:
            service.close()
        for discovery in discoveries:
            discovery.close()
        for link in links:
            link.close()
        if relay is not None:
            relay.close()


def run_case(
    directory, rate, source, profile, include_actions, *, hybrid=False, drop_every=0,
    configure=None, prepare_transport=None,
):
    with connected(directory, hybrid=hybrid, prepare_transport=prepare_transport) as (services, relay):
        sender, receiver = services[source], services[1 - source]
        fault_report = configure(services) if configure is not None else None
        datagrams = {"attempted": 0, "dropped": 0}
        if drop_every:
            transport = sender.channel.transport
            send_motion = transport.send_motion

            def lossy(raw, peer):
                datagrams["attempted"] += 1
                if datagrams["attempted"] % drop_every == 0:
                    datagrams["dropped"] += 1
                else:
                    send_motion(raw, peer)

            transport.send_motion = lossy
        direction = 1 if sender.role == "windows" else -1
        boundary = sender.backend.desktop.right if direction == 1 else 0
        sender.motion(boundary - direction * 100, 400, direction, 0)
        if not sender.motion(boundary, 400, direction * 100, 0):
            raise RuntimeError("Synthetic focus transfer failed")
        until(lambda: sender.session.phase == "sending")
        first_position = sender.remote_position[0]
        receiver.backend.deliveries.clear()
        relay.arm(source, profile)
        started = time.perf_counter()
        expected = []
        late = 0
        # Both edges of a click and a key occur during the simulated stall, at
        # different positions. No motion may be coalesced across these actions.
        actions = {
            int(rate * 0.216): (Kind.BUTTON, 0, 1),
            int(rate * 0.232): (Kind.BUTTON, 0, 0),
            int(rate * 0.248): (Kind.KEY, 4, 1),
            int(rate * 0.264): (Kind.KEY, 4, 0),
        }
        if not include_actions:
            actions.clear()
        for index in range(rate):
            wait = started + index / rate - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            elif wait < -1 / rate:
                late += 1
            if profile == "stall_after_recovery_80ms" and index == int(rate * 0.62):
                # Guarantee ciphertext during the second stall, without an
                # input action barrier that would intentionally pause motion.
                from cleverswitch.flow.input_protocol import Packet

                sender.channel.send(Packet(Kind.PING, 900000))
            position = round(first_position + direction * (index + 1))
            receiver.backend.pending[position] = time.perf_counter()
            if not sender.motion(500, 400, direction, 0):
                raise RuntimeError("Synthetic input focus was lost")
            if index in actions:
                kind, code, down = actions[index]
                emit = sender.button if kind == Kind.BUTTON else sender.key
                if not emit(code, down):
                    raise RuntimeError("Synthetic action was not sent")
                expected.append((kind, (code, down), (position, 400)))
        until(lambda: position not in receiver.backend.pending)
        until(lambda: len(receiver.backend.actions) == len(expected))
        if receiver.backend.actions != expected:
            raise RuntimeError("Action order or pointer position changed")
        if receiver.session.keys or receiver.session.buttons:
            raise RuntimeError("Synthetic input remained pressed")
        samples = receiver.backend.deliveries
        ages = [(received - captured) * 1000 for received, captured in samples]
        gaps = [(second[0] - first[0]) * 1000 for first, second in itertools.pairwise(samples)]
        if not samples or not gaps or relay.failure:
            raise RuntimeError("Incomplete relay measurement")
        # In the hybrid path a motion-only stream can bypass the relay entirely.
        if profile == "stall_80ms" and (not hybrid or include_actions) and relay.held_bytes == 0:
            raise RuntimeError("The relay did not exercise the requested stall")
        if profile == "stall_after_recovery_80ms" and relay.held_bytes == 0:
            raise RuntimeError("The post-recovery TLS stall was not exercised")
        result = {
            "direction": sender.role + "_to_" + receiver.role,
            "profile": profile,
            "includes_click_and_key": include_actions,
            "rate_hz": rate,
            "offered": rate,
            "delivered": len(samples),
            "sender_coalesced": sender.channel.outbox.coalesced,
            "receiver_coalesced": receiver.channel.received_coalesced,
            "scheduler_late": late,
            "sample_age_ms": {
                "p50": percentile(ages, 0.5),
                "p99": percentile(ages, 0.99),
                "max": round(max(ages), 3),
            },
            "delivery_gap_ms": {"p99": percentile(gaps, 0.99), "max": round(max(gaps), 3)},
            "samples_older_than_16_7ms": sum(age > 16.7 for age in ages),
            "held_ciphertext_bytes": relay.held_bytes,
            "relay_peak_queued_bytes": relay.peak_queued_bytes,
            "actions_order_and_position_verified": len(expected),
            "pressed_input_released": True,
            "final_position_delivered": True,
            "end_to_end_tls": "TLSv1.3",
            "transport": "hybrid" if hybrid else "reliable",
            "synthetic_datagram_loss": datagrams,
        }
        if hybrid:
            until(lambda: sender.channel.lane.sender.latest is None)
            result["motion_mode"] = sender.channel.lane.sender.mode
            result["idle_motion_deadline"] = sender.channel.lane.deadline
        if fault_report is not None:
            result["fault_observations"] = fault_report()
        if profile == "stall_after_recovery_80ms":
            settled = [received for received, _ in samples if started + 0.5 <= received <= started + 0.9]
            intervals = [(b - a) * 1000 for a, b in itertools.pairwise(settled)]
            if not intervals:
                raise RuntimeError("No samples in the post-recovery measurement window")
            result["post_recovery_window_seconds"] = [0.5, 0.9]
            result["post_recovery_delivery_gap_ms"] = {
                "p99": percentile(intervals, 0.99), "max": round(max(intervals), 3),
            }
        if not sender.session.leave():
            raise RuntimeError("Synthetic return to local input failed")
        until(lambda: receiver.session.phase == "local")
        return result


def main():
    cases = []
    for profile in ("no_delay", "stall_80ms", "variable_2_to_18ms"):
        for rate in (125, 1000):
            for source in (0, 1):
                for include_actions in (False, True):
                    with tempfile.TemporaryDirectory(prefix="cleverswitch-relay-") as path:
                        cases.append(run_case(Path(path), rate, source, profile, include_actions))
    print(
        json.dumps(
            {
                "scope": "synthetic_input_end_to_end_tls_with_ordered_ciphertext_relay",
                "seconds_per_case": 1,
                "excludes": ["IP_packet_loss", "TCP_retransmission", "Wi-Fi", "native_input", "display", "application"],
                "cases": cases,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
