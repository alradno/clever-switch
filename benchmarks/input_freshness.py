"""Compare unsent motion freshness when a socket cannot write yet.

PYTHONPATH=src .venv/bin/python benchmarks/input_freshness.py

Real local sockets and the production stream scheduler, synthetic motion and a
controlled write-readiness stall. This does not measure Wi-Fi, native input or
screen latency. Already submitted TLS writes must retain their original bytes.
"""

import json
import select
import socket
import subprocess
import threading
import time
from unittest.mock import patch

from cleverswitch.flow.input_protocol import Decoder, Kind, Packet
from cleverswitch.flow.input_stream import Channel

BASELINE = "717c09daa9a48f1f908f4431c082c81137147437"


def run(channel_type, held_ms):
    a, b = socket.socketpair()
    waiting, release, sent = threading.Event(), threading.Event(), threading.Event()
    stamps, writes = {}, []
    real_select = select.select

    class Connection:
        def __getattr__(self, name):
            return getattr(a, name)

        def send(self, data):
            if not writes:
                # Synthetic payload only; retain ages/counts, not positions.
                first = Decoder().feed(data)[0]
                writes.append((time.perf_counter() - stamps[first.values[0]]) * 1000)
            else:
                writes.append(None)
            count = a.send(data)
            sent.set()
            return count

    connection = Connection()
    channel = channel_type(connection, lambda packet: None, lambda: None)

    def gate(readers, writers, errors, timeout):
        if connection in writers:
            waiting.set()
            if not release.wait(2):
                raise TimeoutError("The simulated stall did not finish")
        return real_select(readers, writers, errors, timeout)

    try:
        with patch("cleverswitch.flow.input_stream.select.select", side_effect=gate):
            stamps[1] = time.perf_counter()
            channel.send(Packet(Kind.MOVE, 1, (1, 100)))
            channel.start()
            if not waiting.wait(1):
                raise TimeoutError("The sender did not wait for write readiness")
            for x in range(2, 6):
                time.sleep(held_ms / 4000)
                stamps[x] = time.perf_counter()
                channel.send(Packet(Kind.MOVE, 1, (x, 100)))
            release.set()
            if not sent.wait(1):
                raise TimeoutError("The sender did not recover")
            b.settimeout(1)
            decoder, received = Decoder(), []
            while not received or received[-1].values[0] != 5:
                received.extend(decoder.feed(b.recv(8192)))
            result = {
                "simulated_write_stall_ms": held_ms,
                "offered_moves": 5,
                "wire_moves": len(received),
                "first_write_sample_age_ms": round(writes[0], 3),
                "first_write_is_latest_sample": received[0].values[0] == 5,
                "last_sample_arrived": received[-1].values[0] == 5,
            }
            channel.close()
            return result
    finally:
        release.set()
        channel.close()
        b.close()


def main():
    source = subprocess.check_output(["git", "show", f"{BASELINE}:src/cleverswitch/flow/input_stream.py"], text=True)
    namespace = {"__name__": "cleverswitch.flow.baseline_stream", "__package__": "cleverswitch.flow"}
    exec(compile(source, "baseline_stream", "exec"), namespace)
    cases = []
    for label, channel_type in (("baseline_0.5.3", namespace["Channel"]), ("candidate", Channel)):
        for delay in (8, 24, 80):
            result = run(channel_type, delay)
            result["revision"] = label
            cases.append(result)
    print(
        json.dumps(
            {
                "scope": "synthetic_motion_local_socket_write_readiness_stall",
                "baseline": BASELINE,
                "not_measured": ["TLS_library_backpressure", "Wi-Fi", "native_input", "display", "application"],
                "cases": cases,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
