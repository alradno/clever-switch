"""Compare native-adapter pacing with a slow OS query over real loopback TLS.

PYTHONPATH=src .venv/bin/python benchmarks/input_pacing.py
Requires this repository's history and development dependencies. Every OS surface
is a test double: no user input, hooks, permission requests or native injection.
The baseline uses the 0.5.0 adapters; the transport is identical in both cases.
"""

import ctypes as c
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks import input_stream as benchmark  # noqa: E402
from cleverswitch.flow.input_macos import MacInput  # noqa: E402
from cleverswitch.flow.input_windows import Message, WindowsInput  # noqa: E402
from tests.cleverswitch.test_input_native import Quartz, User32  # noqa: E402

BASELINE = "a73af306d92e42e0c0f0ec78641afafe61e1b4e8"
MemoryInput = benchmark.MemoryInput


def backend_factory(classes):
    platforms = iter(("windows", "mac"))

    class TimedNative(MemoryInput):
        def __init__(self, delay=0):
            super().__init__(delay)
            platform = next(platforms)

            def check():
                time.sleep(0.018)
                return True

            if platform == "windows":
                api = User32()

                def message(ptr, *args):
                    value = api.messages.get()
                    c.cast(ptr, c.POINTER(Message)).contents.message = value
                    return 0 if value == 0x12 else 1

                api.GetMessageW.side_effect = message
                self.native = classes[0](api, Mock(), check, self.desktop)
            else:
                api = Quartz()
                api.CFRunLoopRun = lambda: api.stop.wait()
                self.native = classes[1](api, check, lambda: False, self.desktop)

        def ready(self):
            return self.native.ready()

        def start(self, sink):
            self.native.start(sink)

        def close(self):
            self.native.close()
            super().close()

        def capture(self, enabled):
            self.native.capture(enabled)
            super().capture(enabled)

        def restore(self, point):
            self.native.restore(point)

        def inject(self, packet):
            self.native.inject(packet)
            super().inject(packet)

    return TimedNative


def main():
    old = []
    for platform, name in [("windows", "WindowsInput"), ("macos", "MacInput")]:
        path = f"src/cleverswitch/flow/input_{platform}.py"
        source = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], text=True)
        namespace = {"__package__": "cleverswitch.flow", "__name__": f"cleverswitch.flow.baseline_{platform}"}
        exec(compile(source, f"baseline_{platform}", "exec"), namespace)
        old.append(namespace[name])
    results = []
    try:
        for revision, classes in [("baseline_0.5.0", old), ("candidate", [WindowsInput, MacInput])]:
            for direction in (0, 1):
                benchmark.MemoryInput = backend_factory(classes)
                with tempfile.TemporaryDirectory(prefix="cleverswitch-pacing-") as directory:
                    row = benchmark.run_case(Path(directory), 125, 2, direction)
                    row.update(revision=revision, simulated_permission_query_ms=18)
                    results.append(row)
    finally:
        benchmark.MemoryInput = MemoryInput
    print(
        json.dumps(
            {
                "scope": "synthetic_native_adapters_with_slow_permission_api_over_loopback_TLS",
                "baseline_adapters": BASELINE,
                "not_measured": ["physical_Bluetooth", "WiFi", "screen_refresh", "application"],
                "cases": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
