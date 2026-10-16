import logging
import struct

from ..event.info_task_progress_event import InfoTaskProgressEvent
from ..event.set_report_flag_event import SetReportFlagEvent
from ..event.write_event import WriteEvent
from ..hidpp.constants import (
    ANALYTICS_BYTE9,
    FEATURE_REPROG_CONTROLS_V4,
    KEY_FLAG_ANALYTICS,
    KEY_FLAG_DIVERTABLE,
    MAP_FLAG_DIVERTED,
    SW_ID_DIVERT,
)
from ..hidpp.protocol import build_msg
from ..policy import PairPolicy
from ..registry.logi_device_registry import LogiDeviceRegistry
from ..subscriber.subscriber import Subscriber
from ..topic.topics import Topics

log = logging.getLogger(__name__)


class SetReportFlagSubscriber(Subscriber):
    def __init__(self, device_registry: LogiDeviceRegistry, topics: Topics, policy: PairPolicy | None = None):
        self._device_registry = device_registry
        self._topics = topics
        self._policy = policy or PairPolicy()
        self._pending = {}
        topics.flags.subscribe(self)
        topics.info_progress.subscribe(self)

    def notify(self, event) -> None:
        if isinstance(event, InfoTaskProgressEvent):
            device = event.device
            if device.name is not None and device.role is not None:
                pending = self._pending.pop(device.wpid, None)
                if pending is not None:
                    self.notify(pending)
            return
        if not isinstance(event, SetReportFlagEvent):
            return

        device = self._device_registry.get_by_wpid(event.wpid)
        if device is None:
            log.warning(f"device wpid=0x{event.wpid:04X} not found")
            return

        if (device.name is None or device.role is None) and event.enable:
            self._pending[device.wpid] = event
            return
        if not self._policy.allows(device):
            return
        if event.enable and (self._policy.paused.is_set() or self._policy.closing.is_set()):
            return

        reprog_idx = device.available_features.get(FEATURE_REPROG_CONTROLS_V4)
        if reprog_idx is None:
            log.warning(f"wpid=0x{event.wpid:04X} has no REPROG_CONTROLS_V4")
            return

        if not device.supported_flags & {KEY_FLAG_ANALYTICS, KEY_FLAG_DIVERTABLE}:
            log.warning(f"wpid=0x{event.wpid:04X} has no reprog flags")
            return

        for cid in event.cids:
            if KEY_FLAG_ANALYTICS in device.supported_flags:
                bfield = (
                    (MAP_FLAG_DIVERTED << 1)
                    if self._policy.observe_only and KEY_FLAG_DIVERTABLE in device.supported_flags
                    else 0
                )
                params = struct.pack("!HBHB", cid, bfield, 0x0000, ANALYTICS_BYTE9 if event.enable else 0x01)
            else:
                # Temporary divert: valid + action
                if event.enable and not self._policy.observe_only:
                    bfield = MAP_FLAG_DIVERTED << 1 | MAP_FLAG_DIVERTED
                else:
                    bfield = MAP_FLAG_DIVERTED << 1
                # Never enable persistent diversion in the tray edition.
                params = struct.pack("!HBH", cid, bfield, 0)

            request_id = (reprog_idx << 8) | 0x30 | SW_ID_DIVERT
            msg = build_msg(event.slot, request_id, params)
            self._topics.write.publish(WriteEvent(slot=event.slot, pid=event.pid, hid_message=msg))
            action = (
                "Enabling analytics for"
                if KEY_FLAG_ANALYTICS in device.supported_flags
                else ("Setting divert" if event.enable else "Unsetting divert")
            )
            log.debug(f"{action} CID 0x{cid:04X} on wpid=0x{event.wpid:04X}")
