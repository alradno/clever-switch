import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from cleverswitch.tray import app, firewall

ROOT = pathlib.Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is needed to exercise the Windows installer")
@pytest.mark.parametrize(
    "case",
    [
        "networks",
        "dhcp",
        "idempotent",
        "subnet-change",
        "old-ip-or-path",
        "netmask-format",
        "foreign-rule",
        "explicit-deny",
        "startup",
        "update",
        "tampered-package",
        "rollback",
    ],
)
def test_windows_installer_contract(case, tmp_path):
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-File",
            str(ROOT / "tests/installer/windows_contract.ps1"),
            "-Case",
            case,
            "-Workspace",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PASS {case}" in result.stdout


@pytest.fixture
def installed_windows(mocker, tmp_path):
    mocker.patch.object(sys, "platform", "win32")
    mocker.patch.object(sys, "frozen", True, create=True)
    mocker.patch.object(sys, "_MEIPASS", str(tmp_path / "bundle"), create=True)
    mocker.patch.dict("os.environ", {"LOCALAPPDATA": str(tmp_path), "SystemRoot": str(tmp_path / "Windows")})
    mocker.patch.object(sys, "executable", str(tmp_path / "Programs/CleverSwitchFlow/CleverSwitchTray.exe"))
    return {"network": "192.168.1.0/24", "address": "192.168.1.34"}


@pytest.mark.parametrize("address", ["192.168.1.34", "192.168.1.95"])
def test_windows_check_after_dhcp_uses_subnet_without_elevation(installed_windows, mocker, address):
    config = {**installed_windows, "address": address}
    process = mocker.patch.object(subprocess, "run", return_value=mocker.Mock(stdout='{"state":"ready"}'))
    assert firewall.inspect(config) == {"state": "ready"}
    command = process.call_args.args[0]
    assert command[6] == "Check"
    assert command[-1] == "192.168.1.0/24"
    assert address not in command
    assert process.call_count == 1


@pytest.mark.parametrize("state,calls", [("ready", 1), ("repair", 2), ("blocked", 1)])
def test_firewall_setup_only_repairs_own_missing_rules(installed_windows, mocker, state, calls):
    process = mocker.patch.object(
        subprocess,
        "run",
        side_effect=[
            mocker.Mock(stdout=json.dumps({"state": state})),
            mocker.Mock(stdout='{"state":"ready"}'),
        ],
    )
    assert firewall.inspect(installed_windows, repair=True)["state"] == ("blocked" if state == "blocked" else "ready")
    assert process.call_count == calls
    if calls == 2:
        assert process.call_args.args[0][6] == "Repair"


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CalledProcessError(1, "powershell"),
        subprocess.TimeoutExpired("powershell", 20),
        OSError("unavailable"),
    ],
)
def test_firewall_check_reports_failure_without_retry_or_secret_output(installed_windows, mocker, failure):
    process = mocker.patch.object(subprocess, "run", side_effect=failure)
    assert firewall.inspect(installed_windows) == {"state": "unavailable"}
    assert process.call_count == 1


@pytest.mark.parametrize("case", ["source", "portable", "unpaired", "mac", "invalid", "no-environment"])
def test_firewall_boundaries_do_not_launch_helper(installed_windows, mocker, case):
    process = mocker.patch.object(subprocess, "run", side_effect=AssertionError("No helper"))
    config = installed_windows
    expected = "portable"
    if case == "source":
        mocker.patch.object(sys, "frozen", False)
    elif case == "portable":
        mocker.patch.object(sys, "executable", str(ROOT / "dist/CleverSwitchTray.exe"))
    elif case == "unpaired":
        config, expected = None, "unpaired"
    elif case == "mac":
        mocker.patch.object(sys, "platform", "darwin")
        expected = "not_applicable"
    elif case == "invalid":
        config, expected = {**config, "network": "0.0.0.0/0"}, "unavailable"
    else:
        mocker.patch.dict("os.environ", {"LOCALAPPDATA": ""})
        expected = "unavailable"
    assert firewall.inspect(config, repair=True) == {"state": expected}
    process.assert_not_called()


def test_firewall_invalid_output_is_not_shown(installed_windows, mocker):
    mocker.patch.object(subprocess, "run", return_value=mocker.Mock(stdout='{"state":"secret text"}'))
    result = firewall.inspect(installed_windows)
    assert result == {"state": "unavailable"}
    assert "secret" not in firewall.message(result)


def test_controller_checks_installed_rules_without_repair_on_start(installed_windows, mocker):
    pairing = mocker.patch("cleverswitch.flow.identity.Pairing").return_value
    pairing.load.return_value = installed_windows
    check = mocker.patch.object(firewall, "inspect", return_value={"state": "ready"})
    controller = app.Controller()
    controller.check_installation()
    check.assert_called_once_with(installed_windows, repair=False)
    assert controller.refresh_event.is_set()
    assert controller.installation == {"state": "ready"}
    controller.preview = True
    controller.check_installation(repair=True)
    assert check.call_count == 1


def test_controller_invalid_config_does_not_regenerate_pairing(installed_windows, mocker):
    pairing = mocker.patch("cleverswitch.flow.identity.Pairing").return_value
    pairing.load.side_effect = ValueError("Invalid local configuration")
    controller = app.Controller()
    controller.check_installation(repair=True)
    assert controller.installation == {"state": "unavailable"}
    pairing.save.assert_not_called()
    pairing.identity.assert_not_called()
