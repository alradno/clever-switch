import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.event.host_change_event import HostChangeEvent
from cleverswitch.flow.environment import Network
from cleverswitch.flow.identity import Pairing, local_addresses
from cleverswitch.flow.network import refresh_address
from cleverswitch.flow.runtime import Runtime
from cleverswitch.policy import PairPolicy
from cleverswitch.subscriber.host_change_subscriber import HostChangeSubscriber


@pytest.fixture(autouse=True)
def no_native_registration(mocker):
    mocker.patch("cleverswitch.flow.runtime.native_monitor", return_value=Mock(sleeping=False))


def context():
    keyboard = SimpleNamespace(pid=1, slot=255, role="keyboard", name="MX Keys", available_features={0x1814: 4})
    registry = Mock()
    registry.all_entries.return_value = [keyboard]
    return SimpleNamespace(policy=PairPolicy(), device_registry=registry, topics=Mock())


def test_runtime_unconfigured_and_pending_does_not_start_network(tmp_path):
    pairing = Pairing(tmp_path / "keys")
    runtime = Runtime(context(), pairing)
    runtime.start()
    assert runtime.worker is None
    assert "Vincula" in runtime.status()
    with pytest.raises(ValueError):
        runtime.toggle_edges()
    pairing.create_invitation(tmp_path / "invite.json", "192.168.1.2", "192.168.1.0/24")
    runtime.start()
    assert runtime.worker is None
    assert "respuesta" in runtime.status()
    runtime.close()


def test_keyboard_fallback_only_moves_keyboard_and_policy_routes_once():
    ctx = context()
    runtime = Runtime(ctx)
    event = HostChangeEvent(255, 1, 1)
    ctx.policy.host_change_handler = runtime._keyboard_event
    subscriber = HostChangeSubscriber(ctx.device_registry, ctx.topics, ctx.policy)
    subscriber.notify(event)
    ctx.topics.write.publish.assert_not_called()  # No radio traffic; physical keys remain native.
    ctx.policy.paused.set()
    subscriber.notify(event)
    ctx.topics.write.publish.assert_not_called()
    runtime.coordinator = Mock()
    runtime.peer_ready = True
    runtime._keyboard_event(event)
    runtime.coordinator.keyboard_event.assert_called_once_with(event)


class EndAfter:
    def __init__(self, loops):
        self.loops = loops
        self.ended = False

    def is_set(self):
        return self.ended

    def wait(self, duration):
        self.loops -= 1
        if self.loops <= 0:
            self.ended = True
        return self.ended

    def set(self):
        self.ended = True


@pytest.mark.parametrize("case", ["ready", "offline", "bind-fails", "discovery-fails", "cursor-fails", "dhcp"])
def test_runtime_connection_poll_and_cleanup_are_bounded(case, tmp_path, mocker):
    config = {
        "role": "windows",
        "address": "192.168.1.2",
        "network": "192.168.1.0/24",
        "peer": {"address": "192.168.1.3"},
    }
    pairing = Mock(directory=tmp_path)
    pairing.load.return_value = config
    runtime = Runtime(context(), pairing)
    runtime.enabled = True
    runtime.stop_event = EndAfter(3)
    current = Network("192.168.1.2", "192.168.1.0/24", "192.168.1.1", "aa:bb:cc:dd:ee:ff")
    refreshed = mocker.patch("cleverswitch.flow.runtime.snapshot", return_value=current)
    cursor = Mock()
    cursor.session_ready.return_value = True
    cursor.sample.return_value = SimpleNamespace()
    mocker.patch("cleverswitch.flow.runtime.native_cursor", return_value=cursor)
    hardware = Mock()
    hardware.known.return_value = True
    hardware.allowed.return_value = True
    mocker.patch("cleverswitch.flow.runtime.Hardware", return_value=hardware)
    edge = Mock()
    edge.poll.return_value = 0.5
    mocker.patch("cleverswitch.flow.runtime.Edge", return_value=edge)
    coordinator = Mock()
    coordinator.busy.locked.return_value = False
    mocker.patch("cleverswitch.flow.runtime.Coordinator", return_value=coordinator)
    link = Mock(address=config["address"], worker=Mock())
    link.call.return_value = {"ready": True}
    mocker.patch("cleverswitch.flow.runtime.Link", return_value=link)
    discovery = Mock()
    mocker.patch("cleverswitch.flow.runtime.Discovery", return_value=discovery)
    if case == "offline":
        link.call.side_effect = OSError()
    elif case == "bind-fails":
        link.start.side_effect = OSError()
        link.worker = None
    elif case == "discovery-fails":
        discovery.start.side_effect = OSError()
    elif case == "cursor-fails":
        cursor.sample.side_effect = OSError()
    elif case == "dhcp":
        refreshed.side_effect = [current, Network("192.168.1.4", current.network, current.gateway, current.router_mac)]
    runtime.wake("startup")
    runtime.wake_event.wait = lambda duration: runtime.stop_event.wait(duration)
    runtime._run()
    hardware.close.assert_called()
    link.close.assert_called()
    assert runtime.link is None
    assert runtime.context.policy.host_change_handler == runtime._keyboard_event
    if case in ("ready", "discovery-fails", "dhcp"):
        coordinator.request.assert_called_with("edge", 0.5)
    else:
        coordinator.request.assert_not_called()


@pytest.mark.parametrize("case", ["not-known", "locked", "offline", "active", "inactive", "error", "busy", "result"])
def test_runtime_status_distinguishes_readiness(case):
    runtime = Runtime(context())
    runtime.mode = "online"
    runtime.coordinator = Mock(status="Enlace local listo; cambio por bordes desactivado")
    runtime.coordinator.busy.locked.return_value = False
    runtime.hardware = Mock()
    runtime.hardware.known.return_value = True
    runtime.cursor = Mock()
    runtime.cursor.session_ready.return_value = True
    runtime.peer_ready = True
    expected = "desactivados"
    if case == "not-known":
        runtime.hardware.known.return_value = False
        expected = "Conecta"
    elif case == "locked":
        runtime.cursor.session_ready.return_value = False
        expected = "bloqueado"
    elif case == "offline":
        runtime.peer_ready = False
        runtime.mode = "standby"
        expected = "ausente"
    elif case == "active":
        runtime.enabled = True
        expected = "activos"
    elif case == "error":
        runtime.error = "Error de prueba"
        expected = "Error"
    elif case == "busy":
        runtime.coordinator.busy.locked.return_value = True
        expected = "Enlace local"
    elif case == "result":
        runtime.coordinator.status = "Cambio cancelado"
        expected = "cancelado"
    assert expected in runtime.status()


def test_runtime_enable_persists_and_start_uses_worker(tmp_path, mocker):
    pairing = Mock(directory=tmp_path)
    pairing.load.return_value = {"peer": {}, "edges": True}
    runtime = Runtime(context(), pairing)
    with pytest.raises(ValueError):
        runtime.toggle_edges()
    pairing.load.return_value = {"peer": {"address": "192.168.1.3"}, "edges": True}
    mocker.patch.object(runtime, "_run")
    runtime.start()
    runtime.worker.join(1)
    assert runtime.enabled
    runtime.edge = Mock()
    runtime.toggle_edges()
    assert not runtime.enabled
    pairing.save.assert_called()
    runtime.edge.cooldown.assert_called_once()
    runtime.coordinator = Mock()
    runtime.close()
    runtime.coordinator.stop_event.set.assert_called_once()
    assert runtime.context.policy.host_change_handler is None


def test_private_network_discovery_ignores_down_and_public_interfaces(mocker):
    mocker.patch(
        "psutil.net_if_stats", return_value={"wifi": SimpleNamespace(isup=True), "off": SimpleNamespace(isup=False)}
    )
    mocker.patch(
        "psutil.net_if_addrs",
        return_value={
            "wifi": [
                SimpleNamespace(family=socket.AF_INET, address="192.168.1.34", netmask="255.255.255.0"),
                SimpleNamespace(family=socket.AF_INET, address="8.8.8.8", netmask="255.255.255.0"),
            ],
            "off": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.2", netmask="255.255.255.0")],
        },
    )
    assert local_addresses() == [("wifi", "192.168.1.34", "192.168.1.0/24")]


def test_dhcp_rebind_same_subnet_and_ambiguous_rejected(mocker):
    config = {"address": "192.168.1.2", "network": "192.168.1.0/24"}
    pairing = Mock()
    pairing.load.return_value = config
    addresses = mocker.patch(
        "cleverswitch.flow.network.local_addresses", return_value=[("wifi", "192.168.1.2", "192.168.1.0/24")]
    )
    assert refresh_address(pairing) == config
    pairing.save.assert_not_called()
    addresses.return_value = [("wifi", "192.168.1.3", "192.168.1.0/24")]
    assert refresh_address(pairing)["address"] == "192.168.1.3"
    pairing.save.assert_called_once()
    addresses.return_value = [("wifi", "192.168.1.4", "192.168.1.0/24"), ("eth", "192.168.1.5", "192.168.1.0/24")]
    with pytest.raises(ValueError):
        refresh_address(pairing)


def test_native_keyboard_arrival_requests_mouse_follow_without_interception(mocker):
    now = [0.0]
    mocker.patch("cleverswitch.flow.runtime.time.monotonic", side_effect=lambda: now[0])
    runtime = Runtime(context())
    runtime.coordinator = Mock()
    runtime.coordinator.busy.locked.return_value = False
    runtime.hardware = Mock()
    runtime.hardware.allowed.return_value = True
    runtime.hardware.known.return_value = True
    runtime.hardware.probe.side_effect = lambda role: role == "keyboard"
    runtime.destination = Mock(state=None)
    runtime.cursor = Mock()
    runtime.cursor.session_ready.return_value = True
    runtime.link = Mock()
    runtime._follow_keyboard()
    runtime.link.call.assert_not_called()
    now[0] = 0.05  # Edge polling between follow checks must not clear the candidate.
    runtime._follow_keyboard()
    now[0] = 1
    runtime._follow_keyboard()
    runtime.link.call.assert_called_once_with("follow")
    runtime._follow_keyboard()
    assert runtime.link.call.call_count == 1
    runtime.hardware.probe.side_effect = lambda role: role == "mouse"
    assert runtime._handle("follow", {})["accepted"]
    runtime.coordinator.request.assert_called_once_with("keyboard")
    runtime.coordinator.busy.locked.return_value = True
    runtime.coordinator.can_start.return_value = False
    assert runtime._handle("follow", {}) == {"accepted": False}
    with pytest.raises(ValueError):
        runtime._handle("follow", {"target": 3})
    runtime._handle("ready", {})
    runtime.destination.handle.assert_called_once_with("ready", {})


def test_transfer_rpc_loss_does_not_suspend_readers_during_a_live_lease():
    runtime = Runtime(context())
    runtime.coordinator = Mock()
    runtime.coordinator.busy.locked.return_value = True
    runtime._rpc_failure()
    assert "failure" not in runtime.events
    runtime.coordinator.busy.locked.return_value = False
    runtime._rpc_failure()
    assert "failure" in runtime.events


def test_keyboard_follow_requires_two_separate_absence_checks(mocker):
    now = [0.0]
    mocker.patch("cleverswitch.flow.runtime.time.monotonic", side_effect=lambda: now[0])
    runtime = Runtime(context())
    runtime.coordinator = Mock()
    runtime.coordinator.can_start.return_value = True
    runtime.hardware, runtime.cursor, runtime.link = Mock(), Mock(), Mock()
    runtime.hardware.allowed.return_value = True
    runtime.cursor.session_ready.return_value = True
    runtime.destination = Mock(state=None)
    present = [False]
    runtime.hardware.probe.side_effect = lambda role: role == "keyboard" or present[0]
    runtime._follow_keyboard()  # One missing ping is insufficient.
    now[0], present[0] = 1, True
    runtime._follow_keyboard()
    runtime.link.call.assert_not_called()
    assert runtime.follow_candidate is None
    now[0], present[0] = 2, False
    runtime._follow_keyboard()
    now[0] = 3
    runtime._follow_keyboard()
    runtime.link.call.assert_called_once_with("follow")


def test_observe_only_mode_clears_diversion_instead_of_intercepting_keys():
    from cleverswitch.event.set_report_flag_event import SetReportFlagEvent
    from cleverswitch.hidpp.constants import FEATURE_REPROG_CONTROLS_V4, KEY_FLAG_DIVERTABLE
    from cleverswitch.model.logi_device import LogiDevice
    from cleverswitch.registry.logi_device_registry import LogiDeviceRegistry
    from cleverswitch.subscriber.set_report_flag_subscriber import SetReportFlagSubscriber

    policy, registry = PairPolicy(), LogiDeviceRegistry()
    policy.observe_only = True
    keyboard = LogiDevice(
        1, 1, 255, "keyboard", {FEATURE_REPROG_CONTROLS_V4: 4}, name="MX Keys", supported_flags={KEY_FLAG_DIVERTABLE}
    )
    registry.register(1, keyboard)
    topics = Mock()
    SetReportFlagSubscriber(registry, topics, policy).notify(SetReportFlagEvent(255, 1, 1))
    assert topics.write.publish.call_count == 3
    assert all(call.args[0].hid_message[6] == 2 for call in topics.write.publish.call_args_list)


def test_known_devices_survive_restart_without_faking_presence(tmp_path):
    from cleverswitch.cache.device_cache import DeviceCache
    from cleverswitch.flow.hardware import Hardware
    from cleverswitch.model.logi_device import LogiDevice
    from cleverswitch.topic.topic import Topic
    from cleverswitch.topic.topics import Topics

    path = tmp_path / "devices.json"
    cache = DeviceCache(path)
    for role, name, pid in (("keyboard", "MX Keys", 1), ("mouse", "MX Master 3", 2)):
        cache.save(LogiDevice(pid, pid, 255, role, {0x1814: 4}, name=name))
    ctx = context()
    ctx.config = SimpleNamespace(cache_path=path)
    ctx.device_registry.all_entries.return_value = []
    ctx.topics = Topics(*(Topic() for _ in range(5)))
    hardware = Hardware(ctx)
    assert hardware.known()
    assert not hardware.probe("mouse")
    assert not hardware.probe("keyboard")
    hardware.close()


def test_cleanup_still_closes_network_when_recovery_journal_fails():
    runtime = Runtime(context())
    runtime.coordinator = Mock()
    runtime.destination = Mock()
    runtime.destination.close.side_effect = OSError("disk unavailable")
    link, hardware = Mock(), Mock()
    runtime.link, runtime.hardware = link, hardware
    runtime._disconnect()
    link.close.assert_called_once()
    hardware.close.assert_called_once()
    assert runtime.link is None
    assert "Cierre incompleto" in runtime.status()
