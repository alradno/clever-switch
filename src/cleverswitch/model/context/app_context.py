import dataclasses
import threading

from ...policy import PairPolicy
from ...registry.logi_device_registry import LogiDeviceRegistry
from ...topic.topics import Topics
from ..config.config import Config


@dataclasses.dataclass(frozen=True)
class AppContext:
    device_registry: LogiDeviceRegistry
    topics: Topics
    config: Config
    shutdown: threading.Event
    policy: PairPolicy = dataclasses.field(default_factory=PairPolicy)
