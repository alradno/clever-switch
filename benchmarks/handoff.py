"""Real loopback TLS + simulated radios; never touches user pairing or devices.

Run: PYTHONPATH=src .venv/bin/python benchmarks/handoff.py --cycles 20
Numbers exclude physical Bluetooth, OS input delivery and edge sampling jitter.
"""

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from cleverswitch.flow.coordinator import Coordinator, Destination
from cleverswitch.flow.cursor import Edge, Sample
from cleverswitch.flow.identity import Pairing
from cleverswitch.flow.network import Link


class Radio:
    def __init__(self, host, owners, arrival, reconnect):
        self.host, self.owners, self.arrival, self.reconnect = host, owners, arrival, reconnect

    def allowed(self):
        return True

    def expect_arrival(self, seconds):
        pass

    def known(self):
        return True

    def probe(self, role, timeout=0.45):
        time.sleep(0.003)  # Identical, explicit HID++ response cost in both revisions.
        return self.owners[role] == self.host and time.monotonic() >= self.arrival[role]

    def switch(self, role, target):
        self.owners[role] = target
        self.arrival[role] = time.monotonic() + self.reconnect


class Cursor:
    def __init__(self):
        self.placed = 0

    def session_ready(self):
        return True

    def sample(self):
        return Sample(500, 400, 0, 0, 1000, 800)

    def enter(self, side, y):
        self.placed = time.monotonic()


def distribution(values):
    ordered = sorted(values)
    return {
        "median_ms": round(statistics.median(values), 2),
        "p95_ms": round(ordered[int((len(values) - 1) * 0.95)], 2),
    }


def measure(cycles, reconnect):
    with tempfile.TemporaryDirectory(prefix="handoff-benchmark-") as directory:
        root = Path(directory)
        a, b = Pairing(root / "a", True), Pairing(root / "b", True)
        a.create_invitation(root / "invite.json", "127.0.0.1", "127.0.0.0/8")
        b.accept_invitation(root / "invite.json", root / "reply.json", "127.0.0.1", "127.0.0.0/8")
        a.accept_response(root / "reply.json")
        owners, arrival = dict(mouse=0, keyboard=0), dict(mouse=0, keyboard=0)
        cursors = [Cursor(), Cursor()]
        radios = [Radio(host, owners, arrival, reconnect) for host in (0, 1)]
        destinations = [Destination(radios[h], cursors[h], h, root / f"journal{h}.json") for h in (0, 1)]
        links = [Link(p, d.handle, 0) for p, d in zip((a, b), destinations, strict=True)]
        for link in links:
            link.start()
        links[0].port, links[1].port = links[1].port, links[0].port
        coordinators = [Coordinator(radios[h], cursors[h], links[h], destinations[h]) for h in (0, 1)]
        result = {h: {"cursor": [], "complete": []} for h in (0, 1)}
        try:
            for n in range(cycles * 2):
                host = n % 2
                coordinator = coordinators[host]
                coordinator.busy.acquire()
                start = time.monotonic()
                coordinator._run("edge", 0.5)
                end = time.monotonic()
                assert coordinator.phase == "committed", coordinator.last_error
                assert owners == dict(mouse=1 - host, keyboard=1 - host)
                result[host]["cursor"].append((cursors[1 - host].placed - start) * 1000)
                result[host]["complete"].append((end - start) * 1000)
        finally:
            for link in links:
                link.close()
        return {
            "windows_to_mac" if h == 0 else "mac_to_windows": {k: distribution(v) for k, v in times.items()}
            for h, times in result.items()
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=20)
    options = parser.parse_args()
    assert options.cycles > 0
    print(
        json.dumps(
            {
                "cycles_each_direction": options.cycles,
                "edge_dwell_ms": Edge("left").dwell * 1000,
                "simulated_reconnection_ms": 200,
                "immediate_radio": measure(options.cycles, 0),
                "delayed_radio": measure(options.cycles, 0.2),
            },
            indent=2,
        )
    )
