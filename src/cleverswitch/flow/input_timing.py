"""Bounded duration aggregates; never store input, coordinates or event history.

Observation cannot wait on a native callback. Contention with a diagnostic
snapshot drops a measurement, never an input event. No timers or I/O live here.
"""

import math
import threading


class Duration:
    limits = (0.001, 0.004, 0.008, 0.016, 0.033, 0.050, 0.100, 0.250)

    def __init__(self):
        self.lock = threading.Lock()
        self.count = 0
        self.total = self.maximum = 0.0
        self.buckets = [0] * (len(self.limits) + 1)

    def observe(self, seconds):
        if not math.isfinite(seconds) or seconds < 0 or not self.lock.acquire(blocking=False):
            return
        try:
            self.count += 1
            self.total += seconds
            self.maximum = max(self.maximum, seconds)
            bucket = 0
            while bucket < len(self.limits) and seconds > self.limits[bucket]:
                bucket += 1
            self.buckets[bucket] += 1
        finally:
            self.lock.release()

    def snapshot(self):
        with self.lock:
            count, total, maximum, buckets = self.count, self.total, self.maximum, tuple(self.buckets)
        return {
            "samples": count,
            "average_ms": round(total * 1000 / count, 3) if count else None,
            "maximum_ms": round(maximum * 1000, 3) if count else None,
            "bucket_upper_ms": [int(limit * 1000) for limit in self.limits] + [None],
            "bucket_counts": list(buckets),
        }


class RoutingTiming:
    def __init__(self):
        self.motion_callback = Duration()
        self.action_callback = Duration()
        self.motion_interval = Duration()
        self.deferred_wait = Duration()
        self.native_delivery = Duration()
        self.previous_motion = None

    def motion(self, now):
        if self.previous_motion is not None:
            self.motion_interval.observe(now - self.previous_motion)
        self.previous_motion = now

    def snapshot(self):
        return {
            name: getattr(self, name).snapshot()
            for name in ("motion_callback", "action_callback", "motion_interval", "deferred_wait", "native_delivery")
        }
