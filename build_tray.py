"""Build locally on Windows x64 or macOS arm64. Does not install or run the app."""

import hashlib
import json
import os
import pathlib
import platform
import runpy
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
EDITION = runpy.run_path(str(ROOT / "src/cleverswitch/flow/version.py"))["EDITION"]
platform_name = platform.system()
if platform_name not in ("Windows", "Darwin"):
    raise SystemExit("Compila en Windows o macOS.")
args = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name",
    "CleverSwitchTray",
    "--paths",
    str(ROOT / "src"),
    "--hidden-import",
    "yaml",
    "--hidden-import",
    "pystray._" + ("win32" if platform_name == "Windows" else "darwin"),
    "--icon",
    str(ROOT / "assets" / ("tray-icon.ico" if platform_name == "Windows" else "tray-icon.icns")),
]
if platform_name == "Windows":
    dll = ROOT / "hidapi.dll"
    if (
        hashlib.sha256(dll.read_bytes()).hexdigest()
        != "d4c05ba2138cb5259a7f796464b5eaa63b9f8f67bb5cb003f96989244ee01583"
    ):
        raise SystemExit("hidapi.dll no coincide con la versión oficial x64 revisada.")
    args += [
        "--onefile",
        "--add-binary",
        str(dll) + ";.",
        "--add-data",
        str(ROOT / "Configure-CleverSwitchFirewall.ps1") + ";.",
    ]
else:
    if platform.machine() != "arm64":
        raise SystemExit("Usa Python ARM64 nativo en el Mac M4 Pro, sin Rosetta.")
    dll = pathlib.Path("/opt/homebrew/lib/libhidapi.dylib")
    if not dll.is_file():
        raise SystemExit("Instala hidapi con Homebrew antes de compilar.")
    signing_identity = os.environ.get("CLEVERSWITCH_CODESIGN_IDENTITY", "-")
    if signing_identity != "-":
        identities = subprocess.run(
            ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        # Select an existing valid identity. Never create a certificate or silently
        # replace a requested stable identity with an ad-hoc signature.
        if not signing_identity or not any(
            signing_identity in (line.split()[1], line.partition('"')[2].rpartition('"')[0])
            for line in identities.splitlines()
            if ") " in line and '"' in line
        ):
            raise SystemExit("La identidad de firma solicitada no está disponible en el llavero.")
    args += [
        "--onedir",
        "--target-architecture",
        "arm64",
        "--add-binary",
        str(dll) + ":.",
        "--osx-bundle-identifier",
        "local.cleverswitch.tray",
        "--hidden-import",
        "Quartz",
        "--hidden-import",
        "ApplicationServices",
        "--hidden-import",
        "SystemConfiguration",
    ]
    if signing_identity != "-":
        args += ["--codesign-identity", signing_identity]
args.append(str(ROOT / "tray_main.py"))
subprocess.run(args, cwd=ROOT, check=True)
if platform_name == "Darwin":
    import plistlib

    plist = ROOT / "dist/CleverSwitchTray.app/Contents/Info.plist"
    info = plistlib.loads(plist.read_bytes())
    info.update(
        LSUIElement=True,
        CFBundleDisplayName="CleverSwitch",
        CFBundleShortVersionString=EDITION,
        CFBundleVersion=EDITION,
        NSBluetoothAlwaysUsageDescription="Sincroniza únicamente el cambio de dispositivo del teclado y ratón.",
        NSLocalNetworkUsageDescription="Sincroniza o comparte el ratón y teclado entre tus equipos en la red local.",
    )
    plist.write_bytes(plistlib.dumps(info))
    # Re-sign with the same identity after plist changes. This does not notarize.
    sign_options = ["--options", "runtime", "--timestamp"] if signing_identity != "-" else []
    subprocess.run(
        [
            "/usr/bin/codesign",
            "--force",
            "--deep",
            "--sign",
            signing_identity,
            *sign_options,
            str(ROOT / "dist/CleverSwitchTray.app"),
        ],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(ROOT / "dist/CleverSwitchTray.app")], check=True
    )
else:
    distribution = ROOT / "dist"
    for name in ("Install-CleverSwitch.ps1", "Configure-CleverSwitchFirewall.ps1"):
        shutil.copy2(ROOT / name, distribution / name)
    manifest = {
        "edition": EDITION,
        "protocol": runpy.run_path(str(ROOT / "src/cleverswitch/flow/version.py"))["PROTOCOL"],
        "files": {
            name: hashlib.sha256((distribution / name).read_bytes()).hexdigest()
            for name in ("CleverSwitchTray.exe", "Install-CleverSwitch.ps1", "Configure-CleverSwitchFirewall.ps1")
        },
    }
    (distribution / "cleverswitch-package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
