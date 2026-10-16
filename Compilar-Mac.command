#!/bin/bash
# Compile locally; does not replace an app, change permissions or activate startup.
set -euo pipefail

task_source="$(cd "$(dirname "$0")" && pwd)"
cd "$task_source"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "Ejecuta este archivo en el Mac con Apple Silicon, sin Rosetta."
    exit 1
fi

task_python=""
for task_candidate in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 "$(command -v python3 || true)"; do
    if [[ -n "$task_candidate" && -x "$task_candidate" ]] && "$task_candidate" -c 'import sys,platform; sys.exit(not (sys.version_info >= (3,12) and platform.machine() == "arm64"))'; then
        task_python="$task_candidate"
        break
    fi
done
if [[ -z "$task_python" ]]; then
    echo "Hace falta Python 3.12 o superior, ARM64 nativo."
    exit 1
fi
if [[ ! -f /opt/homebrew/lib/libhidapi.dylib ]]; then
    echo "Hace falta HIDAPI. Instalala en el Mac con: brew install hidapi"
    exit 1
fi

"$task_python" -m venv .venv
.venv/bin/python -m pip install -r requirements-tray.txt
.venv/bin/python build_tray.py
echo ""
echo "Compilacion terminada: $task_source/dist/CleverSwitchTray.app"
echo "Cierra CleverSwitch en ambos equipos. Sustituye la app del Mac por esta version."
echo "Abre la version 0.4.1 en ambos. La vinculacion guardada se conserva."
echo "Revisa Accesibilidad si macOS vuelve a pedir permiso para la nueva compilacion."
