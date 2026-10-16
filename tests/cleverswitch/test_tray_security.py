import argparse
import sys
from unittest.mock import MagicMock

import pytest

from cleverswitch.config.config import _parse
from cleverswitch.errors.errors import ConfigError
from cleverswitch.event.host_change_event import HostChangeEvent
from cleverswitch.event.info_task_progress_event import InfoTaskProgressEvent
from cleverswitch.event.set_report_flag_event import SetReportFlagEvent
from cleverswitch.hidpp.constants import (
    FEATURE_CHANGE_HOST,
    FEATURE_REPROG_CONTROLS_V4,
    KEY_FLAG_ANALYTICS,
    KEY_FLAG_DIVERTABLE,
    KEY_FLAG_PERSISTENTLY_DIVERTABLE,
)
from cleverswitch.model.logi_device import LogiDevice
from cleverswitch.policy import PairPolicy
from cleverswitch.registry.logi_device_registry import LogiDeviceRegistry
from cleverswitch.subscriber.host_change_subscriber import HostChangeSubscriber
from cleverswitch.subscriber.set_report_flag_subscriber import SetReportFlagSubscriber
from cleverswitch.tray import startup
from cleverswitch.tray.app import Controller, single_instance
from cleverswitch.tray.icon import create_icon


def pair():
    registry = LogiDeviceRegistry()
    keyboard = LogiDevice(
        0xB35B, 0xB35B, 255, "keyboard", {FEATURE_CHANGE_HOST: 9, FEATURE_REPROG_CONTROLS_V4: 8}, name="MX Keys"
    )
    mouse = LogiDevice(0xB023, 0xB023, 255, "mouse", {FEATURE_CHANGE_HOST: 9}, name="MX Master 3")
    registry.register(keyboard.wpid, keyboard)
    registry.register(mouse.wpid, mouse)
    return registry, keyboard, mouse


@pytest.mark.parametrize("host", [0, 1, 2])
def test_keyboard_switch_only_sends_to_exact_pair(host):
    registry, keyboard, mouse = pair()
    unrelated = LogiDevice(999, 999, 1, "mouse", {FEATURE_CHANGE_HOST: 4}, name="MX Master 3S")
    registry.register(999, unrelated)
    topics = MagicMock()
    HostChangeSubscriber(registry, topics).notify(HostChangeEvent(keyboard.slot, keyboard.pid, host))
    writes = [call.args[0] for call in topics.write.publish.call_args_list]
    assert {event.pid for event in writes} == {keyboard.pid, mouse.pid}
    assert all(event.hid_message[4] == host for event in writes)


@pytest.mark.parametrize(
    "case", ["paused", "closing", "ambiguous", "unknown-origin", "mouse-origin", "invalid-host", "unknown-name"]
)
def test_fail_closed_for_unsafe_or_ambiguous_switch(case):
    registry, keyboard, mouse = pair()
    policy = PairPolicy()
    event = HostChangeEvent(keyboard.slot, keyboard.pid, 1)
    if case == "paused":
        policy.paused.set()
    if case == "closing":
        policy.closing.set()
    if case == "ambiguous":
        registry.register(111, LogiDevice(111, 111, 1, "mouse", {FEATURE_CHANGE_HOST: 9}, name="MX Master 3"))
    if case == "unknown-origin":
        event = HostChangeEvent(1, 998, 1)
    if case == "mouse-origin":
        event = HostChangeEvent(mouse.slot, mouse.pid, 1)
    if case == "invalid-host":
        event = HostChangeEvent(keyboard.slot, keyboard.pid, 5)
    if case == "unknown-name":
        keyboard.name = None
    assert policy.targets(registry, event) == []


def test_friendly_name_cannot_authorize_another_model():
    registry, keyboard, mouse = pair()
    mouse.name, mouse.friendly_name = "Other mouse", "MX Master 3"
    assert PairPolicy().targets(registry, HostChangeEvent(keyboard.slot, keyboard.pid, 1)) == []


def test_reporting_waits_for_hardware_model_then_enables_only_allowed_keyboard():
    registry, keyboard, mouse = pair()
    keyboard.name = None
    keyboard.supported_flags = {KEY_FLAG_ANALYTICS}
    topics = MagicMock()
    subscriber = SetReportFlagSubscriber(registry, topics)
    subscriber.notify(SetReportFlagEvent(keyboard.slot, keyboard.pid, keyboard.wpid))
    topics.write.publish.assert_not_called()
    keyboard.name = "MX Keys"
    subscriber.notify(
        InfoTaskProgressEvent(slot=keyboard.slot, pid=keyboard.pid, step_name="name", success=True, device=keyboard)
    )
    assert topics.write.publish.call_count == 3


def test_no_persistent_diversion_bit_is_written():
    registry, keyboard, mouse = pair()
    keyboard.supported_flags = {KEY_FLAG_DIVERTABLE, KEY_FLAG_PERSISTENTLY_DIVERTABLE}
    topics = MagicMock()
    SetReportFlagSubscriber(registry, topics).notify(SetReportFlagEvent(keyboard.slot, keyboard.pid, keyboard.wpid))
    assert len(topics.write.publish.call_args_list) == 3
    assert all(call.args[0].hid_message[6] == 3 for call in topics.write.publish.call_args_list)


def test_paused_rejects_enable_but_allows_reporting_cleanup():
    registry, keyboard, mouse = pair()
    keyboard.supported_flags = {KEY_FLAG_ANALYTICS}
    policy = PairPolicy()
    policy.paused.set()
    topics = MagicMock()
    subscriber = SetReportFlagSubscriber(registry, topics, policy)
    subscriber.notify(SetReportFlagEvent(keyboard.slot, keyboard.pid, keyboard.wpid))
    topics.write.publish.assert_not_called()
    subscriber.notify(SetReportFlagEvent(keyboard.slot, keyboard.pid, keyboard.wpid, enable=False))
    assert topics.write.publish.call_count == 3
    assert all(call.args[0].hid_message[9] == 1 for call in topics.write.publish.call_args_list)


@pytest.mark.parametrize(
    "hook", [{"command": "echo should-never-run", "type": "SWITCH"}, {"path": "/tmp/never-run", "type": "CONNECT"}]
)
def test_config_rejects_command_and_script_hooks(hook):
    with pytest.raises(ConfigError):
        _parse({"hooks": {"bad": hook}}, argparse.Namespace(verbose_extra=False))


def test_preview_has_no_engine_no_hid_import_or_startup_mutation(mocker):
    mocker.patch("cleverswitch.setup.app_setup.setup_context", side_effect=AssertionError("No engine"))
    controller = Controller(preview=True)
    controller.start()
    controller.toggle_pause()
    controller.stop()
    assert controller.context is None
    assert "Vista previa" in controller.status()
    assert "vista previa" in controller.device_status("mouse")


def test_controller_shutdown_and_pause_use_graceful_events():
    registry, keyboard, mouse = pair()
    controller = Controller()
    controller.context = MagicMock(device_registry=registry, policy=PairPolicy())
    controller.toggle_pause()
    assert controller.context.policy.paused.is_set()
    assert controller.context.topics.flags.publish.call_args.args[0].enable is False
    controller.toggle_pause()
    assert not controller.context.policy.paused.is_set()
    controller.stop()
    assert controller.context.policy.closing.is_set()
    controller.context.shutdown.set.assert_called_once()


def test_macos_login_agent_uses_fixed_argv_and_no_keepalive(tmp_path, mocker):
    mocker.patch.object(sys, "platform", "darwin")
    mocker.patch.object(sys, "frozen", True, create=True)
    mocker.patch.object(startup, "agent_path", return_value=tmp_path / "local.cleverswitch.tray.plist")
    startup.set_enabled(True)
    import plistlib

    data = plistlib.loads(startup.agent_path().read_bytes())
    assert "KeepAlive" not in data
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"] == startup.executable_command()
    assert startup.is_enabled()
    startup.set_enabled(False)
    assert not startup.agent_path().exists()


def test_source_mode_cannot_enable_login_startup(mocker):
    mocker.patch.object(sys, "frozen", False, create=True)
    with pytest.raises(RuntimeError):
        startup.set_enabled(True)


def test_windows_login_startup_is_only_own_user_value(mocker):
    values = {"OtherApp": "unrelated.exe"}
    key = MagicMock()
    key.__enter__.return_value = key
    registry = MagicMock(HKEY_CURRENT_USER="HKCU", REG_SZ=1)
    registry.CreateKey.return_value = key
    registry.OpenKey.return_value = key

    def query(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    registry.QueryValueEx.side_effect = query
    registry.SetValueEx.side_effect = lambda key, name, zero, type_, value: values.__setitem__(name, value)
    registry.DeleteValue.side_effect = lambda key, name: values.pop(name)
    mocker.patch.dict(sys.modules, {"winreg": registry})
    mocker.patch.object(sys, "platform", "win32")
    mocker.patch.object(sys, "frozen", True, create=True)
    startup.set_enabled(True)
    assert startup.is_enabled()
    assert set(values) == {"OtherApp", startup.NAME}
    startup.set_enabled(False)
    assert values == {"OtherApp": "unrelated.exe"}
    assert not startup.is_enabled()


def test_second_instance_is_refused_without_overwriting_lock(tmp_path, mocker):
    mocker.patch("cleverswitch.tray.app.pathlib.Path.home", return_value=tmp_path)
    with single_instance() as first:
        assert first
        with single_instance() as second:
            assert not second
    with single_instance() as recovered:
        assert recovered


@pytest.mark.parametrize("state", ["ready", "missing", "error", "paused", "ambiguous"])
def test_menu_distinguishes_real_connection_states(state):
    registry, keyboard, mouse = pair()
    controller = Controller()
    controller.context = MagicMock(device_registry=registry, policy=PairPolicy())
    expected = "activo"
    if state == "missing":
        mouse.connected = False
        expected = "Esperando"
    if state == "error":
        controller.error = "Error de conexión"
        expected = "Error"
    if state == "paused":
        controller.paused = True
        expected = "pausado"
    if state == "ambiguous":
        registry.register(111, LogiDevice(111, 111, 1, "mouse", {}, name="MX Master 3"))
        expected = "bloqueado"
    assert expected in controller.status()
    assert "MX Keys" in controller.device_status("keyboard")


def test_icon_is_visible_shape_with_different_paused_state():
    active, paused, mac = create_icon(), create_icon(True), create_icon(mac=True)
    assert active.size == (64, 64)
    assert active.getbbox() is not None
    assert active.tobytes() != paused.tobytes() != mac.tobytes()
