"""CPU cost of bounded aggregates only; no capture, sockets or native injection."""

import json
import statistics
import time

from cleverswitch.flow.input_timing import Duration, RoutingTiming


def run(count=100000, repeats=5):
    cases = []
    for mode in ("callback_aggregates", "stage_aggregate", "contended_stage_aggregate"):
        samples = []
        for _ in range(repeats):
            routing, duration = RoutingTiming(), Duration()
            if mode == "contended_stage_aggregate":
                duration.lock.acquire()
            try:
                started = time.perf_counter()
                for _ in range(count):
                    now = time.perf_counter()
                    if mode == "callback_aggregates":
                        routing.motion(now)
                        routing.motion_callback.observe(time.perf_counter() - now)
                    else:
                        duration.observe(.008)
                samples.append((time.perf_counter() - started) * 1_000_000 / count)
            finally:
                if mode == "contended_stage_aggregate":
                    duration.lock.release()
            metric = routing.motion_callback if mode == "callback_aggregates" else duration
            expected = 0 if mode == "contended_stage_aggregate" else count
            assert metric.snapshot()["samples"] == expected
        cases.append({"mode": mode, "iterations": count, "repeats": repeats,
                      "median_microseconds_per_iteration": round(statistics.median(samples), 3),
                      "maximum_run_average_microseconds": round(max(samples), 3)})
    return {"scope": "aggregate_CPU_microbenchmark_including_loop_and_clock_cost",
            "clocks": {name: vars(time.get_clock_info(name)) for name in ("perf_counter", "monotonic")},
            "excludes": ["native_API_cost", "network", "OS_scheduling", "screen", "physical_latency"],
            "cases": cases}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
