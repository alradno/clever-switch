import threading
from unittest.mock import Mock

import pytest

from cleverswitch.flow.input_protocol import VERSION
from cleverswitch.flow.reconnect import Reconnect
from cleverswitch.flow.runtime import Runtime
from cleverswitch.flow.version import PROTOCOL

from .test_flow_runtime import context
from .test_input_service import DesktopBackend


def configured(tmp_path):
    config = {"peer": {"address": "192.168.1.3"}, "network_input": False, "edges": True}
    pairing = Mock(directory=tmp_path)
    pairing.load.side_effect = lambda: dict(config)
    pairing.save.side_effect = lambda value: config.update(value)
    factory = Mock(return_value=DesktopBackend())
    runtime = Runtime(context(), pairing, input_factory=factory)
    return runtime, config, factory


def online(tmp_path):
    runtime, config, factory = configured(tmp_path)
    runtime.input_enabled = True
    runtime.peer_ready = True
    runtime.mode = "online"
    runtime.budget.authenticated()
    runtime.peer_info = {"protocol": PROTOCOL, "input": {"version": VERSION, "enabled": True}}
    runtime.input_service = Mock(role="windows", channel=None, session=None)
    runtime.input_service._available.return_value = False
    return runtime


def test_input_mode_is_opt_in_and_start_with_old_config_never_constructs_backend(tmp_path, mocker):
    runtime, config, factory = configured(tmp_path)
    mocker.patch("cleverswitch.flow.runtime.native_monitor", return_value=Mock(sleeping=False))
    mocker.patch.object(runtime, "_run")
    runtime.start()
    runtime.worker.join(1)
    assert runtime.enabled and not runtime.input_enabled
    factory.assert_not_called()
    runtime.close()


def test_input_option_is_serialized_on_runtime_and_preserves_pairing_and_edges(tmp_path, mocker):
    runtime, config, factory = configured(tmp_path)
    config["identity_marker"] = "unchanged"
    mocker.patch.object(runtime, "_evaluate")
    runtime.toggle_input()
    assert not config["network_input"]  # The UI callback only schedules work.
    runtime._step()
    assert config["network_input"] and runtime.input_enabled
    assert config["identity_marker"] == "unchanged" and config["edges"]
    assert config["peer"] == {"address": "192.168.1.3"}
    factory.assert_not_called()  # Offline option change does not install hooks.


def test_mode_change_rejected_during_physical_transfer(tmp_path):
    runtime, config, factory = configured(tmp_path)
    runtime.coordinator = Mock()
    runtime.coordinator.busy.locked.return_value = True
    with pytest.raises(ValueError):
        runtime.toggle_input()
    assert not config["network_input"]


def test_input_ready_advertises_capability_without_hid_or_radio_work(tmp_path):
    runtime = online(tmp_path)
    runtime.destination = Mock()
    runtime.coordinator = Mock()
    runtime.hardware = Mock()
    ready = runtime._handle("ready", {})
    assert ready["protocol"] == PROTOCOL and ready["input"]["version"] == VERSION
    assert runtime._handle("prepare", {}) == {"accepted": False, "reason": "input"}
    assert runtime._handle("follow", {}) == {"accepted": False, "reason": "input"}
    assert runtime._keyboard_event(object()) is True
    runtime._schedule_follow()
    runtime._follow_keyboard()
    runtime.destination.handle.assert_not_called()
    runtime.coordinator.request.assert_not_called()
    runtime.coordinator.keyboard_event.assert_not_called()
    runtime.hardware.probe.assert_not_called()


def test_online_input_mode_suspends_hid_and_never_polls_cursor(tmp_path):
    runtime = online(tmp_path)
    runtime.hardware, runtime.cursor, runtime.edge = Mock(), Mock(), Mock()
    runtime.coordinator = Mock()
    runtime.coordinator.busy.locked.return_value = False
    runtime.input_service._available.return_value = True
    runtime.input_service.channel = Mock()
    wait = runtime._step()
    assert 0 < wait <= 30
    assert runtime.context.policy.background_suspended.is_set()
    runtime.cursor.sample.assert_not_called()
    runtime.hardware.device.assert_not_called()
    runtime.coordinator.request.assert_not_called()


@pytest.mark.parametrize("peer", [{}, {"enabled": True, "version": 99}, {"enabled": False, "version": VERSION}])
def test_peer_opt_in_and_compatible_version_are_required(tmp_path, peer):
    runtime = online(tmp_path)
    runtime.peer_info["input"] = peer
    runtime._input_step()
    runtime.input_service.connect.assert_not_called()
    assert runtime.input_budget.wait() is None


def test_input_connection_failures_have_finite_budget_even_with_healthy_control_link(tmp_path):
    runtime = online(tmp_path)
    now = [0.0]
    runtime.input_budget = Reconnect(clock=lambda: now[0], jitter=lambda delay: delay)
    runtime.input_service.connect.side_effect = ConnectionError
    for _ in range(10):
        runtime._input_step()
        now[0] += 30
    assert runtime.input_service.connect.call_count <= 7
    assert runtime.input_budget.next is None
    before = runtime.input_service.connect.call_count
    for _ in range(100):
        runtime._input_step()
    assert runtime.input_service.connect.call_count == before


def test_unstable_streams_do_not_reset_retry_budget_forever(tmp_path, mocker):
    runtime = online(tmp_path)
    now = [0.0]
    mocker.patch("cleverswitch.flow.runtime.time.monotonic", side_effect=lambda: now[0])
    runtime.input_budget = Reconnect(clock=lambda: now[0], jitter=lambda delay: delay)
    service = runtime.input_service

    def open_stream():
        service.channel = Mock(finished=threading.Event())
        return True

    service.connect.side_effect = open_stream
    for _ in range(12):
        runtime._input_step()
        if service.channel:
            service.channel.finished.set()
        now[0] += 0.1
        runtime._input_step()
        now[0] += 60
    assert service.connect.call_count <= 7


def test_offline_service_releases_and_does_no_background_input_work(tmp_path):
    runtime = online(tmp_path)
    runtime.peer_ready = False
    runtime.budget.suspend()
    service = runtime.input_service
    assert runtime._step() is None
    service.suspend.assert_called_once()
    service.connect.assert_not_called()
    assert runtime.context.policy.background_suspended.is_set()


def test_dhcp_cleanup_keeps_failed_releases_in_memory(tmp_path):
    runtime = online(tmp_path)
    service = runtime.input_service
    service.session = Mock(unreleased={("synthetic", 1)})
    service.session.retry_releases.return_value = False
    runtime._disconnect()
    assert runtime.input_recovery == [service.session]
    assert runtime.input_service is None
    service.close.assert_called_once()
    runtime.input_service = Mock(role="windows", channel=None)
    runtime.input_service._available.return_value = False
    runtime._input_step()
    runtime.input_service.connect.assert_not_called()


def test_manual_input_recovery_never_sends_bluetooth_commands(tmp_path):
    runtime = online(tmp_path)
    runtime.input_service.session = Mock()
    runtime.coordinator = Mock()
    assert runtime.recover()
    runtime.input_service.session.cancel.assert_called_once_with("manual")
    runtime.coordinator.recover.assert_not_called()


@pytest.mark.parametrize("where", ["local", "peer"])
def test_locked_session_does_not_retry_hooks_and_unlock_reuses_online_heartbeat(tmp_path, where):
    runtime = online(tmp_path)
    runtime.input_service.backend.ready.return_value = where != "local"
    runtime.peer_info["input"]["available"] = where != "peer"
    for _ in range(10):
        runtime._input_step()
    runtime.input_service.connect.assert_not_called()
    assert runtime.input_budget.next is None
    assert ("este equipo" if where == "local" else "otro equipo") in runtime.input_error
    runtime.input_service.backend.ready.return_value = True
    runtime.peer_info["input"]["available"] = True
    runtime._input_step()
    runtime.input_service.connect.assert_called_once()


@pytest.mark.parametrize("failure,expected", [("timeout", "a tiempo"), ("capture", "permisos"), ("peer", "preparado")])
def test_input_status_reports_failed_handoff_even_when_transport_is_healthy(tmp_path, failure, expected):
    runtime = online(tmp_path)
    runtime.hardware, runtime.cursor, runtime.coordinator = Mock(), Mock(), Mock()
    runtime.input_service._available.return_value = True
    runtime.input_service.session = Mock(phase="local", failure=failure)
    assert expected in runtime.status()
    runtime.input_service.session.failure = None
    assert "Entrada por red lista" in runtime.status()


def test_repeated_local_display_changes_get_a_fresh_bounded_retry_window(tmp_path, mocker):
    runtime = online(tmp_path)
    now = [0.0]
    mocker.patch("cleverswitch.flow.runtime.time.monotonic", side_effect=lambda: now[0])
    runtime.input_budget = Reconnect(clock=lambda: now[0], jitter=lambda delay: delay)
    service = runtime.input_service

    def open_stream():
        service.channel = Mock(finished=threading.Event(), failure=None)
        return True

    service.connect.side_effect = open_stream
    runtime._input_step()
    for attempt in range(10):
        assert service.connect.call_count == attempt + 1
        now[0] += 0.1
        service.channel.failure = "display"
        service.channel.finished.set()
        wait = runtime._input_step()
        assert wait == 1 and runtime.input_budget.attempts == 1
        # A duplicated observation of this retired channel grants no extra retry.
        runtime._input_step()
        assert runtime.input_budget.attempts == 1
        assert service.connect.call_count == attempt + 1
        now[0] += 1
        runtime._input_step()
    assert service.connect.call_count == 11
    # A subsequent real connection failure still exhausts its finite budget.
    service.channel.failure = "connection"
    service.channel.finished.set()
    service.connect.side_effect = ConnectionError
    for _ in range(10):
        now[0] += 30
        runtime._input_step()
    assert runtime.input_budget.next is None
    assert service.connect.call_count <= 17
