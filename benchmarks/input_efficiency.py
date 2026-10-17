"""Compare idle checks and queued bursts against 0.5.1, without native input.

PYTHONPATH=src .venv/bin/python benchmarks/input_efficiency.py
TLS is real loopback; writer delay, permission checks and all input are simulated.
No capture, injection, user pairing, Wi-Fi, display or battery is measured.
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.input_stream import connected, until  # noqa: E402
from cleverswitch.flow import input_service  # noqa: E402
from cleverswitch.flow.input_health import Readiness  # noqa: E402
from cleverswitch.flow.input_protocol import Kind, Packet  # noqa: E402
from cleverswitch.flow.input_stream import Channel  # noqa: E402

BASELINE = "f1f93841382c8e53064943e70e2338afb82ced2f"


def previous(module, name):
    source = subprocess.check_output(["git", "show", f"{BASELINE}:src/cleverswitch/flow/{module}.py"], text=True)
    namespace = {"__package__": "cleverswitch.flow", "__name__": "baseline_" + module}
    exec(compile(source, "baseline_" + module, "exec"), namespace)
    return namespace[name]


def idle_checks(health_type):
    now, checks = [0.0], []
    health = health_type(lambda: checks.append(now[0]) or True, clock=lambda: now[0])
    health.ready()

    class SimulatedTime:
        def wait(self, delay):
            now[0] += delay + 1e-9
            if now[0] >= 60:
                health.stop.set()

        def clear(self):
            pass

    health.wake = SimulatedTime()
    health._run()
    return {"simulated_seconds": 60, "permission_checks": len(checks), "maximum_trust_ms": health.max_age * 1000}


def burst(channel_type, source):
    class CountedConnection:
        def __init__(self, connection):
            self.connection, self.writes = connection, 0

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def send(self, data):
            time.sleep(0.001)  # Synthetic per-write cost, identical in each run.
            result = self.connection.send(data)
            self.writes += 1
            return result

    class Paused(channel_type):
        def __init__(self, connection, *args, **kwargs):
            super().__init__(CountedConnection(connection), *args, **kwargs)

        def start(self):
            pass  # Queue the same burst before either worker can drain it.

        def release(self):
            super().start()

    original = input_service.Channel
    input_service.Channel = Paused
    try:
        with tempfile.TemporaryDirectory(prefix="cleverswitch-efficiency-") as path:
            services, links = connected(Path(path), 0)
            sender, receiver = services[source], services[1 - source]
            received = []
            receiver.channel.on_packet = received.append
            packets = [Packet(Kind.KEY, 9, (4, down)) for _ in range(100) for down in (1, 0)]
            try:
                for packet in packets:
                    sender.channel.send(packet)
                start = time.perf_counter()
                for peer in services:
                    peer.channel.release()
                until(lambda: len(received) == len(packets))
                if received != packets:
                    raise AssertionError("La ráfaga no conservó todas las acciones y su orden")
                return {
                    "direction": sender.role + "_to_" + receiver.role,
                    "events": len(packets),
                    "ordered_delivery": True,
                    "write_calls": sender.channel.connection.writes,
                    "simulated_write_cost_ms": 1,
                    "drain_ms": round((time.perf_counter() - start) * 1000, 3),
                }
            finally:
                for service in services:
                    service.close()
                for link in links:
                    link.close()
    finally:
        input_service.Channel = original


def main():
    revisions = [
        ("baseline_0.5.1", previous("input_health", "Readiness"), previous("input_stream", "Channel")),
        ("candidate", Readiness, Channel),
    ]
    print(
        json.dumps(
            {
                "scope": "synthetic_idle_time_and_queued_bursts_over_real_loopback_TLS",
                "baseline": BASELINE,
                "not_measured": ["physical_input", "WiFi", "screen", "applications", "battery"],
                "cases": [
                    {
                        "revision": name,
                        "idle": idle_checks(health),
                        "bursts": [burst(channel, direction) for direction in (0, 1)],
                    }
                    for name, health, channel in revisions
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
