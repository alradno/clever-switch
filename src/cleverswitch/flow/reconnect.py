"""Finite active search followed by passive listening; no periodic offline retries."""

import random
import time


class Reconnect:
    DELAYS = (1, 2, 5, 10, 30, 60)
    MAX_ATTEMPTS = 7
    WINDOW = 120
    HEARTBEAT = 30

    def __init__(self, clock=time.monotonic, jitter=None):
        self.clock = clock
        self.jitter = jitter or (lambda delay: delay * random.uniform(0.85, 1.15))
        self.online = False
        self.attempts = 0
        self.next = None
        self.deadline = 0
        self.last_hint = -float("inf")

    def restart(self):
        self.online = False
        self.attempts = 0
        self.next = self.clock()
        self.deadline = self.next + self.WINDOW

    def suspend(self):
        self.online = False
        self.next = None

    def authenticated(self):
        self.online = True
        self.attempts = 0
        self.next = self.clock() + self.HEARTBEAT

    def failed(self):
        now = self.clock()
        if self.online:
            self.restart()
        self.attempts += 1
        if self.attempts >= self.MAX_ATTEMPTS or now >= self.deadline:
            self.next = None
        else:
            delay = self.jitter(self.DELAYS[self.attempts - 1])
            self.next = min(now + delay, self.deadline)

    def hint(self):
        now = self.clock()
        if now - self.last_hint < 30:
            return False
        self.last_hint = now
        if self.next is None:
            # An announcement is not proof of reachability. Grant one attempt,
            # rather than resetting a full retry window indefinitely.
            self.attempts = self.MAX_ATTEMPTS - 1
            self.deadline = now + 3
        self.next = now
        return True

    def due(self):
        return self.next is not None and self.clock() >= self.next

    def wait(self):
        return None if self.next is None else max(0, self.next - self.clock())
