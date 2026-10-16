from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.cache.device_cache import DeviceCache
from cleverswitch.flow.hardware import Hardware
from cleverswitch.hidpp.constants import FEATURE_CHANGE_HOST
from cleverswitch.model.logi_device import LogiDevice
from cleverswitch.policy import PairPolicy
from cleverswitch.registry.logi_device_registry import LogiDeviceRegistry
from cleverswitch.tray.app import Controller


@pytest.mark.parametrize(
    "role,name",
    [
        ("keyboard", "MX Keys"),
        ("keyboard", "MX Keys Wireless Keyboard"),
        ("mouse", "MX Master 3"),
        ("mouse", "Wireless Mouse MX Master 3"),
    ],
)
def test_exact_device_names_for_original_models(role, name):
    assert PairPolicy().allows(SimpleNamespace(role=role, name=name))


@pytest.mark.parametrize(
    "role,name",
    [
        ("keyboard", "MX Keys S"),
        ("keyboard", "MX Keys S Wireless Keyboard"),
        ("keyboard", "MX Keys Mini"),
        ("keyboard", "MX Keys Wireless Keyboard Other"),
        ("mouse", "MX Master 3S"),
        ("mouse", "Wireless Mouse MX Master 3S"),
        ("mouse", "Wireless Mouse MX Master 3 Other"),
        ("mouse", "MX Keys Wireless Keyboard"),
        ("keyboard", "Wireless Mouse MX Master 3"),
        ("keyboard", None),
    ],
)
def test_other_models_roles_and_editable_aliases_cannot_authorize_switch(role, name):
    device = SimpleNamespace(role=role, name=name, friendly_name=PairPolicy.MODELS[role])
    assert not PairPolicy().allows(device)


def test_unifying_full_names_reach_tray_and_live_hardware_without_editing_cache(tmp_path):
    # Hardware metadata observed on the user's Unifying receiver; no identity/keys.
    keyboard = LogiDevice(0x408A, 0xC52B, 1, "keyboard", {FEATURE_CHANGE_HOST: 9}, name="MX Keys Wireless Keyboard")
    mouse = LogiDevice(0x4082, 0xC52B, 2, "mouse", {FEATURE_CHANGE_HOST: 10}, name="Wireless Mouse MX Master 3")
    registry = LogiDeviceRegistry()
    for device in (keyboard, mouse):
        registry.register(device.wpid, device)
    cache_path = tmp_path / "device_cache.json"
    cache = DeviceCache(cache_path)
    for device in (keyboard, mouse):
        cache.save(device)
    original_cache = cache_path.read_bytes()
    context = SimpleNamespace(
        config=SimpleNamespace(cache_path=cache_path),
        policy=PairPolicy(),
        device_registry=registry,
        topics=Mock(),
    )
    hardware = Hardware(context)
    try:
        assert hardware.known()
        assert hardware.device("keyboard", connected=True) is keyboard
        assert hardware.device("mouse", connected=True) is mouse
        controller = Controller()
        controller.context = context
        assert controller.status() == "Cambio automático activo"
        assert controller.device_status("keyboard") == "MX Keys — receptor USB"
        assert controller.device_status("mouse") == "MX Master 3 — receptor USB"
        # Recognition from a cache remains separate from evidence of connection.
        keyboard.connected = mouse.connected = False
        assert hardware.known()
        assert hardware.device("keyboard", connected=True) is None
        assert hardware.device("mouse", connected=True) is None
        assert "no conectado aquí" in controller.device_status("keyboard")
        assert hardware.probe("keyboard", timeout=0) is False
        context.topics.write.publish.assert_not_called()
        assert cache_path.read_bytes() == original_cache
    finally:
        hardware.close()
