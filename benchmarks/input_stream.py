"""Reproducible TLS loopback benchmark. No native capture, injection or user input.

PYTHONPATH=src .venv/bin/python benchmarks/input_stream.py --seconds 3

Results measure synthetic capture -> in-memory delivery on this one computer.
They exclude Bluetooth, Wi-Fi, native injection, display refresh and app latency.
"""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from cleverswitch.flow.identity import Pairing
from cleverswitch.flow.input_geometry import Desktop, Display
from cleverswitch.flow.input_protocol import Kind
from cleverswitch.flow.input_service import Service
from cleverswitch.flow.network import Link


class MemoryInput:
    def __init__(self, delay=0):
        self.desktop = Desktop([Display(0, 0, 100000, 1080)])
        self.captured = False
        self.pending = {}
        self.latencies = []
        self.delay = delay

    def ready(self):
        return True

    def start(self, sink):
        self.sink = sink

    def close(self):
        self.captured = False

    def capture(self, value):
        self.captured = bool(value)

    def restore(self, point):
        pass

    def inject(self, packet):
        if self.delay:
            time.sleep(self.delay)
        if packet.kind == Kind.MOVE:
            stamp = self.pending.pop(round(packet.values[0]), None)
            if stamp is not None:
                self.latencies.append((time.perf_counter() - stamp) * 1000)


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("La simulación no alcanzó el estado esperado")
        time.sleep(0.001)


def percentile(values, fraction):
    values = sorted(values)
    return round(values[min(len(values) - 1, int((len(values) - 1) * fraction))], 3) if values else None


def connected(directory, delay):
    pair = [Pairing(directory / role, testing=True) for role in ("windows", "mac")]
    invitation, response = directory / "invitation.json", directory / "response.json"
    pair[0].create_invitation(invitation, "127.0.0.1", "127.0.0.0/8")
    pair[1].accept_invitation(invitation, response, "127.0.0.2", "127.0.0.0/8")
    pair[0].accept_response(response)
    for pairing in pair:
        config = pairing.load()
        config["address"] = config["peer"]["address"] = "127.0.0.1"
        pairing.save(config)
    links = [Link(pairing, lambda op, args: {}, port=0) for pairing in pair]
    services = [Service(link, MemoryInput(delay), enabled=True) for link in links]
    try:
        for link in links:
            link.start()
        links[0].port, links[1].port = links[1].port, links[0].port
        services[0].connect()
        until(lambda: services[1].channel is not None)
        return services, links
    except Exception:
        for service in services:
            service.close()
        for link in links:
            link.close()
        raise


def run_case(directory, rate, seconds, source, delay=0):
    services, links = connected(directory, delay)
    sender, receiver = services[source], services[1 - source]
    direction = 1 if sender.role == "windows" else -1
    boundary = sender.backend.desktop.right if direction == 1 else 0
    cpu, wall = time.process_time(), time.perf_counter()
    try:
        sender.motion(boundary - direction * 100, 400, direction, 0)
        start = time.perf_counter()
        if not sender.motion(boundary, 400, direction * 100, 0):
            raise RuntimeError("El borde simulado no inició el cruce")
        until(lambda: sender.session.phase == "sending")
        handoff = (time.perf_counter() - start) * 1000
        count = max(1, int(rate * seconds))
        first_position = sender.remote_position[0]
        started = time.perf_counter()
        late = 0
        for index in range(count):
            target = started + index / rate
            wait = target - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            elif wait < -1 / rate:
                late += 1
            position = round(first_position + direction * (index + 1))
            receiver.backend.pending[position] = time.perf_counter()
            if not sender.motion(500, 400, direction, 0):
                raise RuntimeError(
                    json.dumps(
                        {
                            "error": "simulated_focus_lost",
                            "rate": rate,
                            "source": sender.role,
                            "receiver_delay_ms": delay * 1000,
                            "offered": index,
                            "sender": sender.diagnostic(),
                            "receiver": receiver.diagnostic(),
                        }
                    )
                )
        until(lambda: not sender.channel.outbox.items)
        try:
            until(lambda: position not in receiver.backend.pending)
        except TimeoutError:
            raise RuntimeError(
                json.dumps(
                    {
                        "error": "simulated_delivery_timeout",
                        "rate": rate,
                        "source": sender.role,
                        "receiver_delay_ms": delay * 1000,
                        "sender": sender.diagnostic(),
                        "receiver": receiver.diagnostic(),
                    }
                )
            ) from None
        latencies = receiver.backend.latencies
        sender.session.leave()
        until(lambda: receiver.session.phase == "local")
        elapsed = time.perf_counter() - wall
        return {
            "direction": sender.role + "_to_" + receiver.role,
            "rate_hz": rate,
            "receiver_simulated_delay_ms": delay * 1000,
            "offered": count,
            "delivered": len(latencies),
            "coalesced": sender.channel.outbox.coalesced,
            "received_coalesced": receiver.channel.received_coalesced,
            "scheduler_late": late,
            "handoff_upper_bound_ms": round(handoff, 3),
            "delivery_ms": {
                "p50": percentile(latencies, 0.50),
                "p95": percentile(latencies, 0.95),
                "p99": percentile(latencies, 0.99),
                "max": round(max(latencies), 3),
                "mean": round(statistics.mean(latencies), 3),
            },
            "process_cpu_percent_one_core": round((time.process_time() - cpu) / elapsed * 100, 2),
            "duration_seconds": round(elapsed, 3),
        }
    finally:
        for service in services:
            service.close()
        for link in links:
            link.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=3)
    args = parser.parse_args()
    if not 0.2 <= args.seconds <= 30:
        parser.error("--seconds debe estar entre 0.2 y 30")
    results = []
    for rate, delay in ((125, 0), (1000, 0), (1000, 0.002)):
        for source in (0, 1):
            with tempfile.TemporaryDirectory(prefix="cleverswitch-input-benchmark-") as path:
                results.append(run_case(Path(path), rate, args.seconds, source, delay))
    print(
        json.dumps(
            {
                "scope": "synthetic_input_mutual_tls_loopback_no_native_input",
                "excludes": ["Bluetooth", "Wi-Fi", "native_injection", "display", "application"],
                "cases": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
