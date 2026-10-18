"""Compare the production Service on TLS versus TLS + encrypted UDP loopback.

PYTHONPATH=src .venv/bin/python benchmarks/input_hybrid.py

Uses synthetic input, temporary test identities and real local sockets. The
same relay holds TLS ciphertext in both modes. The optional loss profile drops
every tenth source UDP datagram AFTER authentication; it does not model TCP loss.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.input_network import run_case  # noqa: E402


def main():
    cases = []
    for profile in ("no_delay", "stall_80ms", "variable_2_to_18ms"):
        for rate in (125, 1000):
            for source in (0, 1):
                for actions in (False, True):
                    for hybrid in (False, True):
                        with tempfile.TemporaryDirectory(prefix="cleverswitch-hybrid-") as path:
                            cases.append(run_case(Path(path), rate, source, profile, actions, hybrid=hybrid))
    for source in (0, 1):
        for actions in (False, True):
            with tempfile.TemporaryDirectory(prefix="cleverswitch-hybrid-loss-") as path:
                cases.append(run_case(Path(path), 1000, source, "no_delay", actions, hybrid=True, drop_every=10))
    print(json.dumps({
        "scope": "production_Service_real_mutual_TLS_and_encrypted_UDP_loopback_synthetic_input",
        "seconds_per_case": 1,
        "excludes": ["TCP_packet_loss", "Wi-Fi", "native_input", "display", "application"],
        "cases": cases,
    }, indent=2))


if __name__ == "__main__":
    main()
