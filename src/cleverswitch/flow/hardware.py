"""Fresh HID++ root pings prove presence; cached discovery never proves arrival."""

import secrets
import struct
import threading
import time

from ..event.hidpp_response_event import HidppResponseEvent
from ..event.write_event import WriteEvent
from ..hidpp.constants import CHANGE_HOST_FN_SET, FEATURE_CHANGE_HOST, SW_ID_HOST_CHANGE


def message(slot, request, payload):
    return struct.pack("!BB18s", 0x11, slot, struct.pack("!H", request) + payload)


class Hardware:
    def __init__(self, context):
        self.context = context
        self.remembered = []
        if hasattr(context, "config"):
            from ..cache.device_cache import DeviceCache

            cache = DeviceCache(context.config.cache_path)
            cache.load()
            self.remembered = [
                d
                for d in cache.all_entries()
                if context.policy.allows(d) and FEATURE_CHANGE_HOST in d.available_features
            ]
        self.lock = threading.Lock()
        self.condition = threading.Condition()
        self.pending = None
        self.answer = False
        self.nonce = secrets.randbelow(256)
        self.subscription = context.topics.hid_event.subscribe(self)

    def device(self, role, connected=False):
        entries = [
            d
            for d in self.context.device_registry.all_entries()
            if d.role == role and self.context.policy.allows(d) and (d.connected or not connected)
        ]
        return entries[0] if len(entries) == 1 and FEATURE_CHANGE_HOST in entries[0].available_features else None

    def known(self):
        for role in ("mouse", "keyboard"):
            live = [
                d
                for d in self.context.device_registry.all_entries()
                if d.role == role and self.context.policy.allows(d)
            ]
            if len(live) > 1:
                return False
            if self.device(role) is None and sum(d.role == role for d in self.remembered) != 1:
                return False
        # Cached recognition allows preparation after restart, never an arrival proof.
        return True

    def allowed(self):
        return not (
            self.context.policy.paused.is_set()
            or self.context.policy.closing.is_set()
            or self.context.policy.background_suspended.is_set()
        )

    def expect_arrival(self, seconds):
        self.context.policy.expect_arrival(seconds)

    def notify(self, event):
        if not isinstance(event, HidppResponseEvent):
            return
        if (event.feature_index, event.function, event.sw_id) != (0, 1, 8) or len(event.payload) < 3:
            return
        with self.condition:
            if self.pending is None:
                return
            pid, slot, nonce = self.pending
            slots = (slot, 0) if slot == 0xFF else (slot,)
            if event.pid == pid and event.slot in slots and event.payload[2] == nonce and event.payload[0] >= 2:
                self.answer = True
                self.condition.notify_all()

    def probe(self, role, timeout=0.45):
        # Serialize probes, including independent server/transaction threads.
        with self.lock:
            device = self.device(role, connected=True)
            if device is None:
                return False
            with self.condition:
                self.nonce = (self.nonce + 1) % 256
                self.pending = (device.pid, device.slot, self.nonce)
                self.answer = False
                self.context.topics.write.publish(
                    WriteEvent(
                        slot=device.slot,
                        pid=device.pid,
                        hid_message=message(device.slot, 0x0018, bytes((0, 0, self.nonce))),
                    )
                )
                deadline = time.monotonic() + timeout
                try:
                    while not self.answer:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return False
                        self.condition.wait(remaining)
                    return True
                finally:
                    self.pending = None

    def switch(self, role, target):
        if target not in (0, 1, 2):
            raise ValueError("Canal inválido")
        device = self.device(role, connected=True)
        if device is None:
            raise RuntimeError("Dispositivo no disponible")
        request = (device.available_features[FEATURE_CHANGE_HOST] << 8) | CHANGE_HOST_FN_SET | SW_ID_HOST_CHANGE
        self.context.topics.write.publish(
            WriteEvent(
                slot=device.slot,
                pid=device.pid,
                hid_message=message(device.slot, request, bytes((target,))),
            )
        )

    def close(self):
        self.context.topics.hid_event.unsubscribe(self.subscription)
