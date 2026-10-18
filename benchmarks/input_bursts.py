"""Measure recovery from an already queued UDP burst, using synthetic input.

PYTHONPATH=src .venv/bin/python benchmarks/input_bursts.py

Real pinned TLS/UDP on loopback; no native input, Wi-Fi or display measurement.
Hold the receiver, enqueue a bounded burst from the channel owner, then measure
release to the final injected position. An optional backend sleep models cost
per injection. No timer is added to the production path to gather the burst.
"""

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.input_network import connected  # noqa: E402
from benchmarks.input_stream import until  # noqa: E402
from cleverswitch.flow.input_protocol import Kind, Packet  # noqa: E402


def run_case(directory, source, count, cost_ms, expected):
    with connected(directory, hybrid=True) as (services, relay):
        sender, receiver = services[source], services[1 - source]
        direction = 1 if sender.role == "windows" else -1
        boundary = sender.local.right if direction == 1 else 0
        sender.motion(boundary - direction * 100, 400, direction, 0)
        if not sender.motion(boundary, 400, direction * 100, 0):
            raise RuntimeError("Synthetic focus transfer failed")
        until(lambda: sender.session.phase == "sending" and receiver.backend.position is not None)
        held, release, emitted = threading.Event(), threading.Event(), threading.Event()
        resume_sender, finished = threading.Event(), threading.Event()
        receiver_tick, sender_tick = receiver.channel.on_tick, sender.channel.on_tick
        inject = receiver.backend.inject
        deliveries = []
        final = (100 + count - 1, 400)
        receiver.backend.delay = cost_ms / 1000

        def observe(packet):
            inject(packet)
            if packet.kind == Kind.MOVE:
                deliveries.append((packet.values, time.perf_counter()))
                if packet.values == final:
                    finished.set()

        def hold_receiver():
            if not held.is_set():
                held.set()
                if not release.wait(2):
                    raise TimeoutError("Receiver was not released")
            receiver_tick()

        def burst_on_owner():
            if not emitted.is_set():
                for index in range(count):
                    sender.channel.lane.outgoing(
                        Packet(Kind.MOVE, sender.session.epoch, (100 + index, 400)), time.monotonic()
                    )
                emitted.set()
                # Avoid adding retries to the controlled backlog. Both modes
                # resume ACK handling after the final position is delivered.
                if not resume_sender.wait(2):
                    raise TimeoutError("Sender was not resumed")
            sender_tick()

        receiver.backend.inject = observe
        receiver.channel.on_tick = hold_receiver
        receiver.channel._wake()
        try:
            if not held.wait(1):
                raise TimeoutError("Receiver was not held")
            sender.channel.on_tick = burst_on_owner
            sender.channel._wake()
            if not emitted.wait(1):
                raise TimeoutError("Burst was not emitted")
            until(lambda: len(receiver.channel.inbox.queue) == count)
            started = time.perf_counter()
            release.set()
            if not finished.wait(1):
                raise TimeoutError("Final position was not applied")
        finally:
            release.set()
            resume_sender.set()
        until(lambda: sender.channel.lane.sender.latest is None)
        if len(deliveries) != (1 if expected == "latest" else count):
            raise RuntimeError(f"Unexpected injection count: {len(deliveries)}")
        if sender.channel.lane.deadline is not None or relay.failure or any(p.channel.failure for p in services):
            raise RuntimeError("Channel did not return to bounded idle")
        return {
            "direction": sender.role + "_to_" + receiver.role,
            "queued_moves": count,
            "requested_injection_cost_ms": cost_ms,
            "injections": len(deliveries),
            "release_to_final_position_ms": round((deliveries[-1][1] - started) * 1000, 3),
            "latest_position_acknowledged": True,
            "remaining_motion_deadline": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--expect-injections", choices=("latest", "all"), default="latest")
    args = parser.parse_args()
    cases = []
    for count in (1, 16, 64):
        for cost in (0, 0.5, 1):
            for source in (0, 1):
                for repeat in range(args.repeats):
                    with tempfile.TemporaryDirectory(prefix="cleverswitch-bursts-") as path:
                        row = run_case(Path(path), source, count, cost, args.expect_injections)
                    row["repeat"] = repeat + 1
                    cases.append(row)
    print(json.dumps({
        "scope": "production_Service_real_mutual_TLS_and_encrypted_UDP_loopback_synthetic_input",
        "measurement": "receiver_release_to_final_position_after_simulated_injection_cost",
        "excludes": ["native_input", "Wi-Fi", "display", "application", "capture_to_display_latency"],
        "expected_injections": args.expect_injections,
        "cases": cases,
    }, indent=2))


if __name__ == "__main__":
    main()
