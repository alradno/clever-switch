import asyncio
import ctypes
import socket
import struct
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from cleverswitch.flow.environment import (
    MacMonitor,
    Network,
    WindowsMonitor,
    home_state,
    mac_router,
    native_monitor,
    normalized_mac,
    snapshot,
    windows_router,
)
from cleverswitch.flow.reconnect import Reconnect
from cleverswitch.flow.runtime import Runtime
from cleverswitch.policy import PairPolicy


def rig(tmp_path, mocker, role="windows"):
    clock = [0.0]
    mocker.patch("cleverswitch.flow.runtime.time.monotonic", side_effect=lambda: clock[0])
    cfg = {"role": role, "address": "192.168.1.2", "network": "192.168.1.0/24", "peer": {"address": "192.168.1.3"}}
    pairing = Mock(directory=tmp_path)
    pairing.load.return_value = cfg
    ctx = SimpleNamespace(policy=PairPolicy(), device_registry=Mock(), topics=Mock())
    runtime = Runtime(ctx, pairing)
    runtime.budget = Reconnect(clock=lambda: clock[0], jitter=lambda value: value)
    current = [Network("192.168.1.2", "192.168.1.0/24", "192.168.1.1", "aa:bb:cc:dd:ee:ff")]
    mocker.patch("cleverswitch.flow.runtime.snapshot", side_effect=lambda config: current[0])
    link, discovery, hardware, cursor = Mock(), Mock(), Mock(), Mock()
    link.call.side_effect = OSError("absent")
    hardware.device.return_value = None
    hardware.probe.return_value = False
    hardware.allowed.return_value = True
    cursor.session_ready.return_value = True
    coordinator = Mock(status="Enlace local listo; cambio por bordes desactivado")
    coordinator.busy.locked.return_value = False

    def connect():
        runtime.link, runtime.discovery, runtime.hardware, runtime.cursor = link, discovery, hardware, cursor
        runtime.destination = Mock(state=None)
        runtime.coordinator = coordinator

    mocker.patch.object(runtime, "_connect", side_effect=connect)
    return runtime, cfg, clock, current, link, discovery, hardware, cursor


def exhaust(runtime, clock):
    runtime.wake("startup")
    runtime._step()
    while runtime.budget.next is not None:
        clock[0] = runtime.budget.next
        runtime._step()


def test_due_heartbeat_during_live_transfer_waits_without_busy_spin(tmp_path, mocker):
    runtime, cfg, clock, current, link, discovery, hardware, cursor = rig(tmp_path, mocker)
    runtime._connect()
    runtime.peer_ready = True
    runtime.budget.authenticated()
    runtime.coordinator.busy.locked.return_value = True
    clock[0] = 31
    assert runtime._step() == 0.2
    assert not runtime.context.policy.background_suspended.is_set()
    link.call.assert_not_called()
    cursor.sample.assert_not_called()


def test_retry_budget_is_finite_and_passive_after_a_day():
    clock = [0]
    budget = Reconnect(lambda: clock[0], lambda value: value)
    budget.restart()
    attempts = []
    while budget.due():
        attempts.append(clock[0])
        budget.failed()
        if budget.next is not None:
            clock[0] = budget.next
    assert attempts == [0, 1, 3, 8, 18, 48, 108]
    clock[0] = 86400
    assert budget.wait() is None
    assert not budget.due()
    assert budget.hint()
    assert budget.due()
    budget.failed()
    assert budget.next is None
    assert not budget.hint()
    budget.authenticated()
    assert budget.online and budget.wait() == 30
    clock[0] += 30
    budget.failed()
    assert not budget.online and budget.attempts == 1
    budget.suspend()
    assert budget.wait() is None


@pytest.mark.parametrize("role", ["windows", "mac"])
def test_absent_peer_stops_all_active_work_in_both_directions(tmp_path, mocker, role):
    runtime, cfg, clock, current, link, discovery, hardware, cursor = rig(tmp_path, mocker, role)
    exhaust(runtime, clock)
    assert link.call.call_count == 7
    assert discovery.announce.call_count == 7
    assert runtime.mode == "standby"
    assert runtime.context.policy.background_suspended.is_set()
    assert runtime._step() is None  # No periodic monitor wakeup in standby.
    from cleverswitch.tray.app import Controller

    controller = Controller()
    controller.context = runtime.context
    assert "En espera" in controller.status()
    assert "consultas suspendidas" in controller.device_status("mouse")
    clock[0] = 86400
    runtime.wake("network")  # Unrelated notification with unchanged home interface.
    assert runtime._step() is None
    assert link.call.call_count == 7
    assert discovery.announce.call_count == 7
    hardware.probe.assert_not_called()
    cursor.sample.assert_not_called()
    link.call.side_effect = None
    link.call.return_value = {"ready": False}  # Reachable; devices may still be waking.
    runtime.wake("announcement")
    runtime._step()
    assert runtime.mode == "online"
    assert not runtime.context.policy.background_suspended.is_set()
    assert link.call.call_count == 8
    assert cfg["home_router"]["mac"] == current[0].router_mac


@pytest.mark.parametrize("role", ["windows", "mac"])
def test_different_router_with_same_ip_range_has_zero_attempts(tmp_path, mocker, role):
    runtime, cfg, clock, current, link, discovery, hardware, cursor = rig(tmp_path, mocker, role)
    cfg["home_router"] = {"gateway": "192.168.1.1", "mac": "11:22:33:44:55:66"}
    runtime.wake("startup")
    assert runtime._step() is None
    assert runtime.mode == "outside"
    runtime.wake("announcement")
    runtime.wake("manual")
    assert runtime._step() is None
    link.call.assert_not_called()
    discovery.announce.assert_not_called()
    cursor.sample.assert_not_called()
    hardware.probe.assert_not_called()
    current[0] = Network(current[0].address, current[0].network, current[0].gateway, cfg["home_router"]["mac"])
    runtime.wake("network")
    runtime._step()
    link.call.assert_called_once()
    assert runtime.mode == "searching"


def test_no_interface_only_performs_three_local_cache_rechecks(tmp_path, mocker):
    runtime, cfg, clock, current, link, discovery, hardware, cursor = rig(tmp_path, mocker)
    current[0] = Network()
    runtime.wake("startup")
    assert runtime._step() == 2
    for moment, expected_wait in ((2, 8), (10, 20), (30, None), (86400, None)):
        clock[0] = moment
        assert runtime._step() == expected_wait
    link.call.assert_not_called()
    discovery.announce.assert_not_called()
    assert runtime.context.policy.background_suspended.is_set()


def test_unknown_router_cache_waits_passively_then_resumes_after_dhcp_arp(tmp_path, mocker):
    runtime, cfg, clock, current, link, discovery, hardware, cursor = rig(tmp_path, mocker)
    cfg["home_router"] = {"gateway": current[0].gateway, "mac": current[0].router_mac}
    current[0] = Network(current[0].address, current[0].network, current[0].gateway)
    runtime.wake("startup")
    runtime._step()
    assert runtime.mode == "standby"
    link.call.assert_not_called()
    discovery.announce.assert_not_called()
    clock[0] = 2
    current[0] = Network(current[0].address, current[0].network, current[0].gateway, cfg["home_router"]["mac"])
    runtime._step()
    assert link.call.call_count == 1


def test_incoming_authenticated_peer_and_manual_reconnect_wake_quiet_wait(tmp_path, mocker):
    runtime, cfg, clock, current, link, discovery, hardware, cursor = rig(tmp_path, mocker)
    exhaust(runtime, clock)
    runtime.wake("authenticated")
    runtime._step()
    assert runtime.peer_ready and runtime.budget.online
    assert not runtime.context.policy.background_suspended.is_set()
    runtime.wake("failure")
    runtime._step()
    assert not runtime.peer_ready
    runtime.budget.suspend()
    runtime.reconnect_now()
    assert "manual" in runtime.events
    runtime._step()
    assert runtime.budget.attempts == 1


@pytest.mark.parametrize("reason", ["sleep", "pause"])
def test_sleep_pause_and_resume_stop_network_and_radio(tmp_path, mocker, reason):
    runtime, cfg, clock, current, link, discovery, hardware, cursor = rig(tmp_path, mocker)
    link.call.side_effect = None
    link.call.return_value = {"ready": True}
    runtime.wake("startup")
    runtime._step()
    assert runtime.mode == "online"
    runtime.monitor = Mock(sleeping=reason == "sleep")
    if reason == "pause":
        runtime.context.policy.paused.set()
    runtime.wake(reason)
    assert runtime._step() is None
    assert runtime.mode in ("sleep", "paused")
    assert runtime.context.policy.background_suspended.is_set()
    link.close.assert_called_once()
    runtime.context.policy.paused.clear()
    runtime.monitor.sleeping = False
    runtime.wake("resume")
    runtime._step()
    assert runtime.mode == "online"


def test_incomplete_switch_keeps_radio_awake_only_until_lease_expires(tmp_path, mocker):
    runtime, cfg, clock, current, link, discovery, hardware, cursor = rig(tmp_path, mocker)
    exhaust(runtime, clock)
    runtime.destination.state = {"phase": "mouse"}
    assert runtime._step() == 0.2
    assert not runtime.context.policy.background_suspended.is_set()
    runtime.destination.state = {"phase": "aborted"}
    assert runtime._step() is None
    assert runtime.context.policy.background_suspended.is_set()


def test_background_wait_is_interruptible_without_polling():
    policy = PairPolicy()
    policy.set_background_suspended(True)
    stop, entered, finished = threading.Event(), threading.Event(), threading.Event()

    def worker():
        entered.set()
        policy.wait_background(stop)
        finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(1)
    assert not finished.wait(0.02)
    stop.set()
    policy.wake_background()
    assert finished.wait(1)
    thread.join(1)


def test_hid_and_ble_readers_park_and_close_without_reconnecting(mocker):
    from cleverswitch.gateway.hid_gateway import HidGateway
    from cleverswitch.gateway.hid_gateway_ble import HidGatewayBLE

    device = SimpleNamespace(pid=1, connection_type="bluetooth", usage=2, usage_page=1, path="unused")
    policy = PairPolicy()
    policy.set_background_suspended(True)
    gateway = HidGateway(device, Mock())
    gateway.energy_policy = policy
    connected = mocker.patch.object(gateway, "_try_connect")
    parked = threading.Event()
    original = gateway._wait_background

    def wait():
        parked.set()
        original()

    mocker.patch.object(gateway, "_wait_background", side_effect=wait)
    gateway.start()
    assert parked.wait(1)
    connected.assert_not_called()
    gateway.close()
    assert not gateway.is_alive()
    ble = HidGatewayBLE(device, Mock())
    ble.energy_policy = policy
    ble._find_peripheral_by_wpid = AsyncMock()

    async def scenario():
        task = asyncio.create_task(ble._ble_main())
        await asyncio.sleep(0.02)
        ble._find_peripheral_by_wpid.assert_not_called()
        ble._stop_event.set()
        policy.wake_background()
        await asyncio.wait_for(task, 1)

    asyncio.run(scenario())


def test_home_state_and_normalization():
    cfg = {"network": "192.168.1.0/24", "home_router": {"gateway": "192.168.1.1", "mac": "01:02:03:04:05:06"}}
    assert home_state(cfg, Network()) == (False, False)
    assert home_state(cfg, Network("192.168.1.2", cfg["network"], "192.168.1.1")) == (True, False)
    assert home_state(cfg, Network("192.168.1.2", cfg["network"], "192.168.1.254")) == (False, False)
    assert normalized_mac("1:2:3:4:5:6") == "01:02:03:04:05:06"
    for value in ("invalid", "00:00:00:00:00:00", "xx:01:02:03:04:05", "001:2:3:4:5:6"):
        assert normalized_mac(value) is None


def test_journal_failure_cannot_cause_endless_recovery(mocker):
    from cleverswitch.flow.coordinator import Destination

    hardware, cursor = Mock(), Mock()
    hardware.known.return_value = True
    hardware.allowed.return_value = True
    hardware.probe.return_value = True
    destination = Destination(hardware, cursor, 1, None)
    destination.state = {"tx": "1" * 32, "phase": "mouse"}
    destination.deadline = 0
    mocker.patch.object(destination, "_record", side_effect=OSError("disk failed"))
    with pytest.raises(OSError):
        destination.expire()
    assert destination.state["phase"] == "aborted"
    assert hardware.switch.call_count == 2
    destination.expire()
    assert hardware.switch.call_count == 2  # No further queries/attempts.


def test_windows_router_reads_tables_without_arp_probe(mocker):
    address = struct.unpack("<I", socket.inet_aton("192.168.1.2"))[0]
    gateway = struct.unpack("<I", socket.inet_aton("192.168.1.1"))[0]
    buffers = {
        "GetIpAddrTable": struct.pack("<I5I2H", 1, address, 7, 0, 0, 0, 0, 0),
        "GetIpForwardTable": struct.pack("<I14I", 1, 0, 0, 0, gateway, 7, 4, 0, 0, 0, 10, 0, 0, 0, 0),
        "GetIpNetTable": struct.pack("<III8sII", 1, 7, 6, bytes.fromhex("0102030405060000"), gateway, 3),
    }
    api = Mock()
    for name, data in buffers.items():

        def call(buffer, size, sorted_, data=data):
            size._obj.value = len(data)
            if buffer is None:
                return 122
            ctypes.memmove(buffer, data, len(data))
            return 0

        getattr(api, name).side_effect = call
    mocker.patch("ctypes.WinDLL", return_value=api)
    assert windows_router("192.168.1.2") == ("192.168.1.1", "01:02:03:04:05:06")
    assert windows_router("192.168.1.20") == (None, None)
    api.GetIpNetTable.side_effect = lambda buffer, size, sorted_: 50
    assert windows_router("192.168.1.2") == ("192.168.1.1", None)


def test_native_windows_notification_registration_callbacks_and_cleanup(mocker):
    api, power = Mock(), Mock()
    mocker.patch("ctypes.WinDLL", side_effect=lambda name, **kw: power if name == "powrprof" else api)

    def register(*args):
        args[-1]._obj.value = 42
        return 0

    api.NotifyIpInterfaceChange.side_effect = register
    api.NotifyRouteChange2.side_effect = register
    power.PowerRegisterSuspendResumeNotification.side_effect = register
    events = []
    monitor = WindowsMonitor(events.append)
    monitor.start()
    monitor.network_callback(None, None, 1)
    monitor._power(None, 4, None)
    assert monitor.sleeping
    monitor._power(None, 18, None)
    assert not monitor.sleeping
    monitor.close()
    assert events == ["network", "sleep", "resume"]
    assert api.CancelMibChangeNotify2.call_count == 2
    power.PowerUnregisterSuspendResumeNotification.assert_called_once()
    api.NotifyIpInterfaceChange.side_effect = lambda *args: 5
    with pytest.raises(OSError):
        WindowsMonitor(events.append).start()


def test_native_mac_network_sleep_callbacks_and_cache_read(mocker):
    sc, cf, center = Mock(), Mock(), Mock()
    callbacks = []
    center.addObserverForName_object_queue_usingBlock_.side_effect = lambda name, obj, queue, callback: (
        callbacks.append(callback) or name
    )
    workspace = Mock()
    workspace.sharedWorkspace.return_value.notificationCenter.return_value = center
    mocker.patch.dict(
        sys.modules,
        {
            "SystemConfiguration": sc,
            "CoreFoundation": cf,
            "AppKit": SimpleNamespace(
                NSWorkspace=workspace, NSWorkspaceDidWakeNotification="wake", NSWorkspaceWillSleepNotification="sleep"
            ),
            "Foundation": SimpleNamespace(NSOperationQueue=Mock()),
        },
    )
    events = []
    monitor = MacMonitor(events.append)
    monitor.start()
    sc.SCDynamicStoreCreate.call_args.args[2](None, [], None)
    callbacks[0](None)
    assert monitor.sleeping
    callbacks[1](None)
    assert not monitor.sleeping
    monitor.close()
    assert events == ["network", "sleep", "resume"]
    assert center.removeObserver_.call_count == 2
    sc.SCDynamicStoreCopyKeyList.return_value = ["State:/Network/Service/test/IPv4"]
    sc.SCDynamicStoreCopyValue.return_value = {"Addresses": ["192.168.1.2"], "Router": "192.168.1.1"}
    run = mocker.patch(
        "cleverswitch.flow.environment.subprocess.run", return_value=SimpleNamespace(stdout="? at 1:2:3:4:5:6 on en0")
    )
    assert mac_router("192.168.1.2") == ("192.168.1.1", "01:02:03:04:05:06")
    assert run.call_args.args[0] == ["/usr/sbin/arp", "-n", "192.168.1.1"]
    assert mac_router("192.168.1.20") == (None, None)
    sc.SCDynamicStoreSetNotificationKeys.return_value = False
    with pytest.raises(OSError):
        MacMonitor(events.append).start()


def test_snapshot_matches_selected_interface_and_excludes_vpn_gateway(mocker):
    cfg = {"address": "192.168.1.2", "network": "192.168.1.0/24"}
    addresses = mocker.patch(
        "cleverswitch.flow.environment.local_addresses", return_value=[("wifi", cfg["address"], cfg["network"])]
    )
    router = mocker.patch(
        "cleverswitch.flow.environment.windows_router", return_value=("192.168.1.1", "01:02:03:04:05:06")
    )
    mocker.patch.object(sys, "platform", "win32")
    assert snapshot(cfg).router_mac == "01:02:03:04:05:06"
    router.return_value = ("10.1.1.1", "01:02:03:04:05:06")
    assert snapshot(cfg).gateway is None
    addresses.return_value = [("wifi", "192.168.1.4", cfg["network"])]
    assert snapshot(cfg).address == "192.168.1.4"
    addresses.return_value = []
    assert snapshot(cfg) == Network()
    mocker.patch.object(sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        native_monitor(lambda reason: None)
