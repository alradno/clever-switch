import hashlib
import json
import pathlib
import plistlib
import runpy
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def build_source(tmp_path):
    for name in ("build_tray.py", "Install-CleverSwitch.ps1", "Configure-CleverSwitchFirewall.ps1", "hidapi.dll"):
        shutil.copy2(ROOT / name, tmp_path / name)
    version = tmp_path / "src/cleverswitch/flow/version.py"
    version.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "src/cleverswitch/flow/version.py", version)
    (tmp_path / "dist").mkdir()
    return tmp_path


def test_windows_build_delivers_verified_installer_together_with_executable(build_source, mocker):
    mocker.patch("platform.system", return_value="Windows")
    exe = build_source / "dist/CleverSwitchTray.exe"
    exe.write_bytes(b"simulated native build")
    compile_app = mocker.patch.object(subprocess, "run")
    runpy.run_path(str(build_source / "build_tray.py"))
    command = compile_app.call_args.args[0]
    assert "--add-data" in command
    assert str(build_source / "Configure-CleverSwitchFirewall.ps1") + ";." in command
    manifest = json.loads((build_source / "dist/cleverswitch-package.json").read_text())
    assert manifest["protocol"] == 2
    assert set(manifest["files"]) == {
        "CleverSwitchTray.exe",
        "Install-CleverSwitch.ps1",
        "Configure-CleverSwitchFirewall.ps1",
    }
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((build_source / "dist" / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("available", [True, False])
def test_mac_requested_signing_identity_is_preserved_or_build_stops(build_source, mocker, available):
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("platform.machine", return_value="arm64")
    real_is_file = pathlib.Path.is_file
    mocker.patch.object(
        pathlib.Path,
        "is_file",
        lambda path: str(path) == "/opt/homebrew/lib/libhidapi.dylib" or real_is_file(path),
    )
    identity = "A" * 40
    mocker.patch.dict("os.environ", {"CLEVERSWITCH_CODESIGN_IDENTITY": identity})
    plist = build_source / "dist/CleverSwitchTray.app/Contents/Info.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({"CFBundleIdentifier": "local.cleverswitch.tray"}))
    process = mocker.patch.object(
        subprocess,
        "run",
        return_value=mocker.Mock(
            stdout=f'  1) {identity} "Existing signing identity"\n' if available else "0 valid identities found"
        ),
    )
    if not available:
        with pytest.raises(SystemExit, match="firma solicitada"):
            runpy.run_path(str(build_source / "build_tray.py"))
        assert process.call_count == 1  # No ad-hoc fallback, no build or permission changes.
        return
    runpy.run_path(str(build_source / "build_tray.py"))
    build, resign, verify = [call.args[0] for call in process.call_args_list[1:]]
    assert build[build.index("--codesign-identity") + 1] == identity
    assert resign[resign.index("--sign") + 1] == identity
    assert "--verify" in verify
    assert plistlib.loads(plist.read_bytes())["CFBundleIdentifier"] == "local.cleverswitch.tray"
