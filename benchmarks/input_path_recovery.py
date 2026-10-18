"""Check adverse UDP and receiver pauses with synthetic input and real loopback.

PYTHONPATH=src .venv/bin/python benchmarks/input_path_recovery.py

Alters only this benchmark's encrypted datagrams and in-memory receiver. No OS
capture/injection, firewall, router, network configuration or private pairing.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.input_network import run_case  # noqa: E402


class Fault:
    def __init__(self, original, profile):
        self.original, self.profile = original, profile
        self.first = self.pending = None
        self.attempted = self.dropped = self.duplicates = self.corrupted = self.reordered = 0

    def __call__(self, raw, peer):
        now = time.perf_counter()
        if self.first is None:
            self.first = now
        self.attempted += 1
        if self.profile in ("loss_60ms", "loss_150ms", "receipt_loss_150ms"):
            duration = 0.06 if self.profile == "loss_60ms" else 0.15
            if now - self.first < duration:
                self.dropped += 1
                return
        if self.profile == "reverse_pairs":
            if self.pending is None:
                self.pending = raw, peer
                return
            self.original(raw, peer)
            self.original(*self.pending)
            self.pending = None
            self.reordered += 1
        elif self.profile == "duplicate":
            self.original(raw, peer)
            self.original(raw, peer)
            self.duplicates += 1
        elif self.profile == "altered_before_valid":
            changed = bytearray(raw)
            changed[-1] ^= 1
            self.original(bytes(changed), peer)
            self.original(raw, peer)
            self.corrupted += 1
        else:
            self.original(raw, peer)


def configure_fault(source, profile):
    def configure(peers):
        sender, receiver = peers[source], peers[1 - source]
        target = receiver if profile == "receipt_loss_150ms" else sender
        fault = Fault(target.channel.transport.send_motion, profile)
        target.channel.transport.send_motion = fault
        pause = {"count": 0}
        if profile.startswith("receiver_pause_"):
            original_tick = receiver.channel.on_tick
            paused_after = time.perf_counter() + 0.2
            duration = 0.06 if profile == "receiver_pause_60ms" else 0.15

            def delayed_tick():
                if not pause["count"] and time.perf_counter() >= paused_after:
                    pause["count"] += 1
                    time.sleep(duration)
                original_tick()

            receiver.channel.on_tick = delayed_tick

        def report():
            assert all(peer.channel.failure is None for peer in peers)
            result = {name: getattr(fault, name) for name in
                      ("attempted", "dropped", "duplicates", "corrupted", "reordered")}
            result.update(
                receiver_pauses=pause["count"],
                receiver_inbox_dropped=receiver.channel.inbox.dropped,
                receiver_udp_coalesced=receiver.channel.lane.received_coalesced,
                path_recheck_used=getattr(sender.channel.lane, "recovery_used", False),
            )
            return result

        return report

    return configure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-path-after-pause", choices=("datagram", "reliable"), default="datagram")
    args = parser.parse_args()
    cases = []
    profiles = (
        "duplicate", "reverse_pairs", "altered_before_valid", "loss_60ms",
        "loss_150ms", "receipt_loss_150ms", "receiver_pause_60ms", "receiver_pause_150ms",
        "receiver_pause_150ms_then_tls_stall",
    )
    for profile in profiles:
        for rate in (125, 1000):
            for source in (0, 1):
                with tempfile.TemporaryDirectory(prefix="cleverswitch-path-") as temporary:
                    network = "stall_after_recovery_80ms" if profile.endswith("then_tls_stall") else "no_delay"
                    row = run_case(
                        Path(temporary), rate, source, network, True,
                        hybrid=True, configure=configure_fault(source, profile),
                    )
                row["fault"] = profile
                expected = "reliable" if profile in ("loss_150ms", "receipt_loss_150ms") else "datagram"
                recovering = profile.startswith("receiver_pause_150ms")
                if recovering:
                    expected = args.expected_path_after_pause
                if row["motion_mode"] != expected or row["idle_motion_deadline"] is not None:
                    raise RuntimeError("Unexpected path recovery or remaining deadline")
                recheck = row["fault_observations"]["path_recheck_used"]
                if recheck != (recovering and args.expected_path_after_pause == "datagram"):
                    raise RuntimeError("Reachability check was missing or unsolicited")
                if row["fault_observations"]["receiver_pauses"] != int(profile.startswith("receiver_pause_")):
                    raise RuntimeError("Receiver pause was not exercised")
                cases.append(row)
    print(json.dumps({
        "scope": "production_Service_real_loopback_TLS_UDP_with_synthetic_input",
        "seconds_per_case": 1,
        "expected_path_after_pause": args.expected_path_after_pause,
        "excludes": ["physical_input", "Wi-Fi", "display", "native_input_API"],
        "cases": cases,
    }, indent=2))


if __name__ == "__main__":
    main()
