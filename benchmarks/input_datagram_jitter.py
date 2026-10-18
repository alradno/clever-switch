"""Exercise delayed, reordered UDP in both directions with synthetic input.

PYTHONPATH=src .venv/bin/python benchmarks/input_datagram_jitter.py

Only temporary test identities, loopback TLS/UDP and in-memory input are used.
The test relay delays encrypted datagrams, including acknowledgements, without
blocking their sender. This is a controlled queue model, not a Wi-Fi measurement.
"""

import heapq
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.input_network import run_case  # noqa: E402
from benchmarks.input_stream import percentile, until  # noqa: E402
from cleverswitch.flow.input_motion import HEADER  # noqa: E402
from cleverswitch.flow.input_protocol import Kind  # noqa: E402


class DatagramDelay:
    """One bounded scheduler for this case's two UDP directions, ciphertext only."""

    capacity = 512

    def __init__(self, profile, source):
        self.profile, self.source = profile, source
        self.condition = threading.Condition()
        self.queue = []
        self.stopped = self.busy = False
        self.failure = None
        self.started = None
        self.prepared = self.negotiated_ms = None
        self.ordinal = self.peak = self.regressions = 0
        self.counts = [0, 0]
        self.dropped = [0, 0]
        self.reordered = [0, 0]
        self.last_sequence = [0, 0]
        self.delays = [[], []]
        self.held = [0, 0]
        self.originals = []
        self.worker = threading.Thread(target=self._run, name="benchmark-udp-delay", daemon=True)

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self, *_):
        with self.condition:
            self.stopped = True
            self.queue.clear()
            self.condition.notify()
        self.worker.join(2)
        if self.worker.is_alive():
            raise RuntimeError("Datagram relay did not terminate")

    def install(self, transports):
        self.originals = [transport.send_motion for transport in transports]
        self.prepared = time.perf_counter()
        for index, transport in enumerate(transports):
            transport.send_motion = lambda raw, address, i=index: self.send(i, raw, address)

    def configure(self, peers):
        # connected() requires both finite path probes to succeed. Delays were
        # installed before TLS/bootstrap, so that readiness is also exercised.
        self.negotiated_ms = (time.perf_counter() - self.prepared) * 1000
        self.started = time.perf_counter()
        receiver = peers[1 - self.source]
        inject = receiver.backend.inject
        previous = None
        direction = 1 if self.source == 0 else -1

        def observe(packet):
            nonlocal previous
            if packet.kind == Kind.MOVE:
                if previous is not None and direction * (packet.values[0] - previous) < 0:
                    self.regressions += 1
                previous = packet.values[0]
            inject(packet)

        receiver.backend.inject = observe
        return lambda: self.report(peers)

    def send(self, direction, raw, address):
        queued = time.perf_counter()
        with self.condition:
            if self.stopped:
                raise OSError("Datagram relay is closed")
            if self.started is None:
                self.started = queued
            self.counts[direction] += 1
            count = self.counts[direction]
            if self.profile == "variable_loss10" and count % 10 == 0:
                self.dropped[direction] += 1
                return
            delay = 0.002 if self.profile == "burst_40ms" else (0.002, 0.006, 0.018, 0.004)[count % 4]
            due = queued + delay
            if self.profile == "burst_40ms" and self.started + 0.2 <= queued < self.started + 0.24:
                due = max(due, self.started + 0.24)
                self.held[direction] += 1
            if len(self.queue) >= self.capacity:
                raise BufferError("Datagram relay capacity exceeded")
            self.ordinal += 1
            heapq.heappush(self.queue, (due, self.ordinal, direction, queued, raw, address))
            self.peak = max(self.peak, len(self.queue))
            self.condition.notify()

    def _run(self):
        try:
            while True:
                with self.condition:
                    while not self.stopped:
                        if not self.queue:
                            self.condition.wait()
                        else:
                            wait = self.queue[0][0] - time.perf_counter()
                            if wait > 0:
                                self.condition.wait(wait)
                            else:
                                _, _, direction, queued, raw, address = heapq.heappop(self.queue)
                                self.busy = True
                                break
                    if self.stopped:
                        return
                self.originals[direction](raw, address)
                sequence = HEADER.unpack_from(raw)[-1]
                with self.condition:
                    self.delays[direction].append((time.perf_counter() - queued) * 1000)
                    self.reordered[direction] += int(sequence < self.last_sequence[direction])
                    self.last_sequence[direction] = max(sequence, self.last_sequence[direction])
                    self.busy = False
        except Exception as error:
            self.failure = type(error).__name__

    def settled(self):
        with self.condition:
            return self.failure is not None or (not self.queue and not self.busy)

    def report(self, peers):
        until(self.settled)
        if self.failure or self.regressions or any(peer.channel.failure for peer in peers):
            raise RuntimeError("Delayed datagrams broke pointer order or the connection")
        sender = peers[self.source]
        if sender.channel.lane.sender.mode != "datagram" or sender.channel.lane.deadline is not None:
            raise RuntimeError("A usable path was lost or retained motion work while idle")
        if self.profile != "burst_40ms" and not all(self.reordered):
            raise RuntimeError("The delay profile did not exercise reordering in both directions")
        if self.profile == "variable_loss10" and not all(self.dropped):
            raise RuntimeError("The loss profile did not exercise both directions")
        if self.profile == "burst_40ms" and not self.held[self.source]:
            raise RuntimeError("The source burst was not held")
        return {
            "profile": self.profile,
            "directions": ["windows_to_mac", "mac_to_windows"],
            "attempted": self.counts,
            "dropped": self.dropped,
            "delivered_out_of_order": self.reordered,
            "held_by_burst": self.held,
            "peak_queued_datagrams": self.peak,
            "pointer_regressions": self.regressions,
            "connection_and_path_ready_ms": round(self.negotiated_ms, 3),
            "relay_delay_ms": [
                {"p50": percentile(values, 0.5), "p99": percentile(values, 0.99), "max": round(max(values), 3)}
                for values in self.delays
            ],
            "queue_drained": True,
        }


def main():
    cases = []
    for fault in ("variable_2_to_18ms", "variable_loss10", "burst_40ms"):
        for rate in (125, 1000):
            for source in (0, 1):
                for actions in (False, True):
                    with tempfile.TemporaryDirectory(prefix="cleverswitch-jitter-") as temporary:
                        with DatagramDelay(fault, source) as relay:
                            row = run_case(
                                Path(temporary), rate, source, "variable_2_to_18ms", actions,
                                hybrid=True, configure=relay.configure, prepare_transport=relay.install,
                            )
                    cases.append(row)
    print(json.dumps({
        "scope": "production_Service_real_loopback_TLS_UDP_with_synthetic_input",
        "seconds_per_case": 1,
        "udp_delay_both_directions": True,
        "tls_delay": "ordered_2_to_18ms",
        "excludes": ["physical_input", "Wi-Fi", "display", "native_input_API"],
        "cases": cases,
    }, indent=2))


if __name__ == "__main__":
    main()
