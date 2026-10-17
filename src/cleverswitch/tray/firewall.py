"""On-demand Windows checks. Only explicit setup actions may request UAC."""

import json
import os
import pathlib
import subprocess
import sys

from ..flow.identity import private_network

MESSAGES = {
    "unchecked": "sin comprobar",
    "ready": "reglas de la red doméstica presentes",
    "repair": "pendiente de configurar para esta red",
    "blocked": "hay una regla de bloqueo; revísala con el administrador",
    "portable": "instala la app con Install-CleverSwitch.ps1",
    "unpaired": "se configurará al vincular los equipos",
    "unavailable": "no se pudo comprobar; usa Revisar firewall de Windows",
    "not_applicable": "gestionado por macOS",
}


def inspect(config, repair=False):
    if sys.platform != "win32":
        return {"state": "not_applicable"}
    if not getattr(sys, "frozen", False):
        return {"state": "portable"}
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return {"state": "unavailable"}
    expected = pathlib.Path(local) / "Programs/CleverSwitchFlow/CleverSwitchTray.exe"
    if pathlib.Path(sys.executable).resolve() != expected.resolve():
        return {"state": "portable"}
    if not config:
        return {"state": "unpaired"}
    try:
        network = private_network(config["network"], config["address"])
        helper = pathlib.Path(sys._MEIPASS) / "Configure-CleverSwitchFirewall.ps1"
        shell = pathlib.Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [
            str(shell),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(helper),
            "-Mode",
            "Check",
            "-Program",
            str(expected),
            "-Network",
            network,
        ]

        def invoke():
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120 if command[6] == "Repair" else 20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=True,
            )
            status = json.loads(result.stdout)
            if not isinstance(status, dict) or status.get("state") not in ("ready", "repair", "blocked"):
                raise ValueError("Invalid firewall result")
            # Do not retain script output, profile contents or arbitrary error text.
            return {"state": status["state"]}

        status = invoke()
        if repair and status["state"] == "repair":
            command[6] = "Repair"
            status = invoke()
        return status
    except (OSError, ValueError, KeyError, AttributeError, subprocess.SubprocessError):
        return {"state": "unavailable"}


def message(status):
    return MESSAGES.get(status.get("state"), MESSAGES["unavailable"])
