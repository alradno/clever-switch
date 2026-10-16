"""Build locally on Windows x64 or macOS arm64. Does not install or run the app."""

import pathlib
import platform
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
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
    import hashlib

    dll = ROOT / "hidapi.dll"
    if (
        hashlib.sha256(dll.read_bytes()).hexdigest()
        != "d4c05ba2138cb5259a7f796464b5eaa63b9f8f67bb5cb003f96989244ee01583"
    ):
        raise SystemExit("hidapi.dll no coincide con la versión oficial x64 revisada.")
    args += ["--onefile", "--add-binary", str(dll) + ";."]
else:
    if platform.machine() != "arm64":
        raise SystemExit("Usa Python ARM64 nativo en el Mac M4 Pro, sin Rosetta.")
    dll = pathlib.Path("/opt/homebrew/lib/libhidapi.dylib")
    if not dll.is_file():
        raise SystemExit("Instala hidapi con Homebrew antes de compilar.")
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
args.append(str(ROOT / "tray_main.py"))
subprocess.run(args, cwd=ROOT, check=True)
if platform_name == "Darwin":
    import plistlib

    plist = ROOT / "dist/CleverSwitchTray.app/Contents/Info.plist"
    info = plistlib.loads(plist.read_bytes())
    info.update(
        LSUIElement=True,
        CFBundleDisplayName="CleverSwitch",
        NSBluetoothAlwaysUsageDescription="Sincroniza únicamente el cambio de dispositivo del teclado y ratón.",
        NSLocalNetworkUsageDescription="Confirma el cambio de ratón y teclado con tu Windows en el mismo router.",
    )
    plist.write_bytes(plistlib.dumps(info))
    # Re-sign after plist changes; this is ad-hoc, not developer notarization.
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(ROOT / "dist/CleverSwitchTray.app")], check=True
    )
