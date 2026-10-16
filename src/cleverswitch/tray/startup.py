"""Explicit per-user login startup. No service, shell, elevation or PATH edits."""

import os
import pathlib
import plistlib
import subprocess
import sys

NAME = "CleverSwitchTray"
LABEL = "local.cleverswitch.tray"


def executable_command():
    if not getattr(sys, "frozen", False):
        raise RuntimeError("El inicio automático se configura desde el paquete compilado.")
    return [str(pathlib.Path(sys.executable).resolve())]


def agent_path():
    return pathlib.Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def is_enabled():
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                value, _ = winreg.QueryValueEx(key, NAME)
            return value == subprocess.list2cmdline(executable_command())
        except (OSError, RuntimeError):
            return False
    if sys.platform == "darwin":
        try:
            data = plistlib.loads(agent_path().read_bytes())
            return data.get("ProgramArguments") == executable_command() and data.get("RunAtLoad") is True
        except (OSError, ValueError, RuntimeError):
            return False
    return False


def set_enabled(enabled):
    command = executable_command()
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            else:
                try:
                    current, _ = winreg.QueryValueEx(key, NAME)
                    if current == subprocess.list2cmdline(command):
                        winreg.DeleteValue(key, NAME)
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        path = agent_path()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "Label": LABEL,
                "ProgramArguments": command,
                "RunAtLoad": True,
                "LimitLoadToSessionType": "Aqua",
                "ProcessType": "Interactive",
            }
            # No KeepAlive: choosing Salir must not relaunch the application.
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(plistlib.dumps(data))
            os.replace(tmp, path)
        elif is_enabled():
            path.unlink(missing_ok=True)
    else:
        raise RuntimeError("Inicio automático disponible sólo en Windows y macOS.")
