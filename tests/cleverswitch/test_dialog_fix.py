import ctypes
import pathlib
import sys
import threading
from unittest.mock import Mock

import pytest

from cleverswitch.tray import app
from cleverswitch.tray import windows_dialog as wd
from cleverswitch.tray.actions import Actions


def test_windows_setup_returns_while_dialog_waits_and_ignores_duplicate_clicks(mocker):
    mocker.patch.object(sys, "platform", "win32")
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    caller = threading.get_ident()
    observed = []
    actions = Actions(lambda: done.set() if not actions.busy else None)

    def slow_dialog(icon, item):
        observed.append(threading.get_ident())
        entered.set()
        assert release.wait(3)

    callback = actions.wrap(slow_dialog)
    try:
        callback(None, None)
        assert entered.wait(1)  # Callback already returned, dialog still waiting.
        assert actions.busy
        callback(None, None)
        assert len(observed) == 1 and observed[0] != caller
        actions.closed.set()  # Quit is available even while the dialog is blocked.
    finally:
        release.set()
    assert done.wait(2)
    callback(None, None)
    assert len(observed) == 1


def test_mac_setup_stays_on_caller_thread_and_releases_gate_on_error(mocker):
    mocker.patch.object(sys, "platform", "darwin")
    notice = mocker.patch("cleverswitch.tray.dialogs.message")
    actions = Actions(Mock())
    seen = []
    actions.wrap(lambda icon, item: seen.append(threading.get_ident()))(None, None)
    assert seen == [threading.get_ident()] and not actions.busy

    def fail(icon, item):
        raise OSError("test")

    actions.wrap(fail)(None, None)
    assert not actions.busy
    notice.assert_called_once()


@pytest.mark.parametrize("initial", [0, 1, -2147417850])
def test_sta_init_balances_success_and_does_not_uninitialize_failed_apartment(initial, mocker):
    api = Mock()
    api.CoInitializeEx.return_value = initial
    mocker.patch("ctypes.WinDLL", return_value=api)
    if initial < 0:
        with pytest.raises(OSError):
            with wd.apartment():
                pytest.fail("Incompatible COM mode must not open a dialog")
        api.CoUninitialize.assert_not_called()
    else:
        with pytest.raises(ValueError), wd.apartment():
            raise ValueError("cleanup on any exception")
        api.CoUninitialize.assert_called_once()
    api.CoInitializeEx.assert_called_once_with(None, 6)


@pytest.mark.parametrize("save", [False, True])
def test_dialog_preserves_flags_and_enforces_filesystem_parent_and_overwrite(save):
    dialog = Mock()

    def call(slot, types=(), *args):
        if slot == 10:
            args[0]._obj.value = 0x02000000
        return 0

    dialog.call.side_effect = call
    wd.configure(dialog, save, "Vincular", "enlace.json")
    flags = next(c.args[2] for c in dialog.call.call_args_list if c.args[0] == 9)
    assert flags & 0x02000000 and flags & 0x40 and flags & 8 and flags & 0x800
    assert flags & (2 if save else 0x1000)
    assert (15 in [c.args[0] for c in dialog.call.call_args_list]) == save


@pytest.mark.parametrize("outcome", [0, ctypes.c_long(0x800704C7).value, -2147467259])
def test_save_cancel_and_error_always_release_dialog(outcome, mocker):
    api, dialog = Mock(), Mock()
    dialog.call.return_value = outcome
    context = mocker.patch.object(wd, "apartment")
    context.return_value.__enter__.return_value = api
    mocker.patch.object(wd, "create", return_value=dialog)
    mocker.patch.object(wd, "configure")
    path = mocker.patch.object(wd, "result_path", return_value=pathlib.Path("enlace.json"))
    if outcome < 0 and outcome & 0xFFFFFFFF != wd.CANCELLED:
        with pytest.raises(OSError):
            wd.choose_file(True, "Guardar", "enlace.json")
    else:
        assert wd.choose_file(True, "Guardar", "enlace.json") == (path.return_value if outcome == 0 else None)
    dialog.release.assert_called_once()
    if outcome != 0:
        path.assert_not_called()


def test_result_path_releases_shell_item_and_task_memory(mocker):
    api, dialog, item = Mock(), Mock(), Mock()
    memory = ctypes.create_unicode_buffer("C:\\Users\\Alberto\\enlace.json")

    def call(slot, types, *args):
        args[-1]._obj.value = ctypes.addressof(memory)
        return 0

    item.call.side_effect = call
    dialog.call.return_value = 0
    mocker.patch.object(wd, "Interface", return_value=item)
    assert wd.result_path(api, dialog).name == "enlace.json"
    item.release.assert_called_once()
    api.CoTaskMemFree.assert_called_once()


@pytest.mark.parametrize("cancel", [True, False])
def test_quitting_while_picker_is_open_cannot_write_link_or_restart_flow(cancel, tmp_path, mocker):
    controller = Mock(context=None, paused=False, flow=Mock(enabled=False))
    mocker.patch.object(app, "Controller", return_value=controller)
    mocker.patch.object(sys, "platform", "win32")
    mocker.patch.object(app.startup, "is_enabled", return_value=False)
    mocker.patch("cleverswitch.flow.identity.local_addresses", return_value=[("wifi", "192.168.1.2", "192.168.1.0/24")])
    pairing = Mock()
    pairing.load.return_value = None
    mocker.patch("cleverswitch.flow.identity.Pairing", return_value=pairing)
    notice = mocker.patch("cleverswitch.tray.dialogs.message")
    entered, release = threading.Event(), threading.Event()
    actions = []
    original = Actions

    def make_actions(on_change):
        instance = original(on_change)
        actions.append(instance)
        return instance

    mocker.patch.object(app, "Actions", side_effect=make_actions)

    def picker(*args):
        entered.set()
        assert release.wait(3)
        return None if cancel else tmp_path / "enlace.json"

    mocker.patch("cleverswitch.tray.dialogs.choose_file", side_effect=picker)
    icon = Mock()
    menu = {}

    def create(*args):
        menu.update({item.text: item for item in args[3]})
        return icon

    mocker.patch("pystray.Icon", side_effect=create)

    def exercise(**kwargs):
        menu["Vincular Windows 1 y Mac 2…"](icon)
        assert entered.wait(1)
        assert not menu["Guardar enlace para Mac…"].enabled
        assert menu["Salir"].enabled
        menu["Salir"](icon)
        release.set()
        # Acquire the action gate: deterministic wait for callback completion.
        assert actions[0].lock.acquire(timeout=2)
        actions[0].lock.release()

    icon.run.side_effect = exercise
    try:
        app.run()
    finally:
        release.set()
    pairing.create_invitation.assert_not_called()
    controller.start_flow.assert_not_called()
    notice.assert_not_called()


def test_stopped_controller_cannot_restart_flow(mocker):
    controller = app.Controller()
    controller.stop()
    runtime = mocker.patch("cleverswitch.flow.runtime.Runtime")
    controller.start_flow()
    runtime.assert_not_called()


@pytest.mark.parametrize("state", ["new", "pending", "paired", "multiple-nics"])
def test_export_downloads_bypasses_picker_and_preserves_pending_identity(state, tmp_path, mocker):
    controller = Mock(context=None, paused=False, flow=Mock(enabled=False))
    mocker.patch.object(app, "Controller", return_value=controller)
    mocker.patch.object(sys, "platform", "win32")
    mocker.patch.object(app.startup, "is_enabled", return_value=False)
    mocker.patch("cleverswitch.tray.actions.threading.Thread.start", lambda self: self.run())
    networks = [("wifi", "192.168.1.2", "192.168.1.0/24")]
    if state == "multiple-nics":
        networks *= 2
    mocker.patch("cleverswitch.flow.identity.local_addresses", return_value=networks)
    pairing = Mock()
    pairing.load.return_value = {"role": "windows"} if state == "pending" else None
    if state == "paired":
        pairing.load.return_value = {"peer": {"address": "192.168.1.3"}}
    mocker.patch("cleverswitch.flow.identity.Pairing", return_value=pairing)
    picker = mocker.patch("cleverswitch.tray.dialogs.choose_file", side_effect=AssertionError("No picker"))
    mocker.patch.object(wd, "downloads_directory", return_value=tmp_path)
    notice = mocker.patch("cleverswitch.tray.dialogs.message")
    icon, menu = Mock(), {}

    def create(*args):
        menu.update({item.text: item for item in args[3]})
        return icon

    mocker.patch("pystray.Icon", side_effect=create)
    icon.run.side_effect = lambda **kwargs: menu["Guardar enlace en Descargas (sin selector)"](icon)
    app.run()
    picker.assert_not_called()
    if state == "new":
        path, address, network = pairing.create_invitation.call_args.args
        assert path.parent == tmp_path and path.name.startswith("CleverSwitch-enlace-")
        assert (address, network) == ("192.168.1.2", "192.168.1.0/24")
        pairing.export_invitation.assert_not_called()
    elif state == "pending":
        pairing.create_invitation.assert_not_called()
        pairing.export_invitation.assert_called_once()
    else:
        pairing.create_invitation.assert_not_called()
        pairing.export_invitation.assert_not_called()
        controller.start_flow.assert_not_called()
    notice.assert_called_once()


def test_downloads_resolves_known_folder_and_frees_memory(mocker):
    api, ole = Mock(), Mock()
    text = ctypes.create_unicode_buffer("C:\\Users\\Alberto\\Downloads")

    def resolve(folder, flags, token, out):
        out._obj.value = ctypes.addressof(text)
        return 0

    api.SHGetKnownFolderPath.side_effect = resolve
    mocker.patch("ctypes.WinDLL", side_effect=lambda name: api if name == "shell32" else ole)
    assert wd.downloads_directory() == pathlib.Path(text.value)
    ole.CoTaskMemFree.assert_called_once()
