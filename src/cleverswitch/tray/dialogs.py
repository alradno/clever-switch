"""Native pairing dialogs. Windows Shell dialogs require an initialized STA."""

import pathlib
import sys


def choose_file(save=False, title="Enlace Windows y Mac", name="CleverSwitch-enlace.json"):
    if sys.platform == "darwin":
        from AppKit import NSModalResponseOK, NSOpenPanel, NSSavePanel

        panel = NSSavePanel.savePanel() if save else NSOpenPanel.openPanel()
        panel.setTitle_(title)
        panel.setAllowedFileTypes_(["json"])
        if save:
            panel.setNameFieldStringValue_(name)
        else:
            panel.setAllowsMultipleSelection_(False)
        return pathlib.Path(panel.URL().path()) if panel.runModal() == NSModalResponseOK else None
    if sys.platform == "win32":
        from .windows_dialog import choose_file as windows_choose_file

        return windows_choose_file(save, title, name)
    return None


def message(title, body):
    if sys.platform == "darwin":
        from AppKit import NSAlert

        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(body)
        alert.addButtonWithTitle_("Aceptar")
        alert.runModal()
    elif sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        function = ctypes.WinDLL("user32").MessageBoxW
        function.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
        function.restype = ctypes.c_int
        function(None, body, title, 0x40)
