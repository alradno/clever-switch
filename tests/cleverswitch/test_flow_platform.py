import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cleverswitch.flow.cursor import MacCursor, WindowsCursor, native_cursor
from cleverswitch.tray import dialogs


def test_windows_cursor_reads_position_and_filters_drag_and_secure_desktop(mocker):
    api = Mock()
    mocker.patch("ctypes.WinDLL", return_value=api, create=True)
    api.OpenInputDesktop.return_value = 42
    api.GetUserObjectInformationW.side_effect = lambda handle, key, name, size, out: (
        setattr(name, "value", "Default") or True
    )

    def position(point):
        point._obj.x, point._obj.y = 100, 200
        return True

    api.GetCursorPos.side_effect = position
    api.GetAsyncKeyState.return_value = 0
    api.GetSystemMetrics.side_effect = lambda key: 1920 if key == 0 else 1080
    api.SetCursorPos.return_value = True
    cursor = WindowsCursor()
    sample = cursor.sample()
    assert (sample.x, sample.y, sample.width, sample.height, sample.safe) == (100, 200, 1920, 1080, True)
    cursor.enter("left", 0.5)
    api.SetCursorPos.assert_called_with(24, 540)
    cursor.enter("right", 0.5)
    api.SetCursorPos.assert_called_with(1895, 540)
    api.SetCursorPos.return_value = False
    with pytest.raises(OSError):
        cursor.enter("left", 0.5)
    api.GetAsyncKeyState.return_value = 0x8000
    assert not cursor.sample().safe
    with pytest.raises(BlockingIOError):
        cursor.enter("left", 0.5)
    api.GetAsyncKeyState.return_value = 0
    api.OpenInputDesktop.return_value = 0
    assert not cursor.session_ready()
    api.OpenInputDesktop.return_value = 42
    api.GetUserObjectInformationW.side_effect = lambda handle, key, name, size, out: (
        setattr(name, "value", "Winlogon") or True
    )
    assert not cursor.session_ready()
    api.GetCursorPos.side_effect = None
    api.GetCursorPos.return_value = False
    with pytest.raises(OSError):
        cursor.sample()
    mocker.patch.object(sys, "platform", "win32")
    assert isinstance(native_cursor(), WindowsCursor)
    mocker.patch.object(sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        native_cursor()


def test_mac_cursor_permissions_lock_drag_and_pointer_placement(mocker):
    q = Mock()
    q.kCGSessionOnConsoleKey = "on"
    q.kCGSessionLoginDoneKey = "login"
    q.kCGEventFlagMaskShift, q.kCGEventFlagMaskControl = 1, 2
    q.kCGEventFlagMaskAlternate, q.kCGEventFlagMaskCommand = 4, 8
    q.CGSessionCopyCurrentDictionary.return_value = {"on": True, "login": True}
    q.CGEventGetLocation.return_value = SimpleNamespace(x=0, y=100)
    q.CGDisplayBounds.return_value = SimpleNamespace(
        origin=SimpleNamespace(x=0, y=0), size=SimpleNamespace(width=1440, height=900)
    )
    q.CGEventSourceFlagsState.return_value = 0
    q.CGEventSourceButtonState.return_value = False
    q.CGWarpMouseCursorPosition.return_value = 0
    accessibility, workspace = Mock(), Mock()
    accessibility.AXIsProcessTrusted.return_value = True
    workspace.sharedWorkspace.return_value.frontmostApplication.return_value.bundleIdentifier.return_value = (
        "com.apple.finder"
    )
    mocker.patch.dict(
        sys.modules,
        {"Quartz": q, "ApplicationServices": accessibility, "AppKit": SimpleNamespace(NSWorkspace=workspace)},
    )
    cursor = MacCursor()
    assert cursor.sample().safe
    cursor.enter("left", 0.5)
    q.CGWarpMouseCursorPosition.assert_called_with((24, 450))
    cursor.enter("right", 0.5)
    q.CGWarpMouseCursorPosition.assert_called_with((1415, 450))
    q.CGWarpMouseCursorPosition.return_value = 1
    with pytest.raises(OSError):
        cursor.enter("left", 0.5)
    q.CGEventSourceFlagsState.return_value = 1
    assert not cursor.sample().safe
    with pytest.raises(BlockingIOError):
        cursor.enter("left", 0.5)
    q.CGEventSourceFlagsState.return_value = 0
    accessibility.AXIsProcessTrusted.return_value = False
    assert not cursor.session_ready()
    accessibility.AXIsProcessTrusted.return_value = True
    q.CGSessionCopyCurrentDictionary.return_value["CGSSessionScreenIsLocked"] = True
    assert not cursor.session_ready()
    q.CGEventCreate.return_value = None
    with pytest.raises(OSError):
        cursor.sample()
    mocker.patch.object(sys, "platform", "darwin")
    assert isinstance(native_cursor(), MacCursor)


def test_windows_pairing_dialog_uses_native_file_picker_without_side_effects(mocker):
    api = Mock()
    mocker.patch.object(sys, "platform", "win32")
    mocker.patch("ctypes.WinDLL", return_value=api, create=True)
    choose = mocker.patch(
        "cleverswitch.tray.windows_dialog.choose_file", return_value=__import__("pathlib").Path("enlace.json")
    )
    assert dialogs.choose_file(True, name="enlace.json").name == "enlace.json"
    choose.assert_called_once_with(True, "Enlace Windows y Mac", "enlace.json")
    choose.return_value = None
    assert dialogs.choose_file() is None
    dialogs.message("title", "body")
    api.MessageBoxW.assert_called_once()


def test_mac_pairing_dialogs_and_alert(mocker):
    panel = Mock()
    panel.runModal.return_value = 1
    panel.URL.return_value.path.return_value = "/tmp/enlace.json"
    open_panel, save_panel, alert = Mock(), Mock(), Mock()
    open_panel.openPanel.return_value = panel
    save_panel.savePanel.return_value = panel
    mocker.patch.object(sys, "platform", "darwin")
    mocker.patch.dict(
        sys.modules,
        {
            "AppKit": SimpleNamespace(
                NSModalResponseOK=1,
                NSOpenPanel=open_panel,
                NSSavePanel=save_panel,
                NSAlert=alert,
            )
        },
    )
    assert dialogs.choose_file().name == "enlace.json"
    assert dialogs.choose_file(True).name == "enlace.json"
    panel.runModal.return_value = 0
    assert dialogs.choose_file() is None
    dialogs.message("title", "body")
    alert.alloc.return_value.init.return_value.runModal.assert_called_once()
    mocker.patch.object(sys, "platform", "linux")
    assert dialogs.choose_file() is None
