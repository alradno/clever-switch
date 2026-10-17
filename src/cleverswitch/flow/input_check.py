"""Read-only native readiness, run inside the actual application identity.

Never installs capture, posts input, requests permissions or loads pairing data.
"""

import sys

from .input_service import native_input
from .version import EDITION


def inspect():
    report = {"edition": EDITION, "platform": sys.platform, "ready": False, "checks": {}}
    try:
        backend = native_input()
        if sys.platform == "darwin":
            report["checks"] = {
                "listen_permission": bool(backend.q.CGPreflightListenEventAccess()),
                "post_permission": bool(backend.q.CGPreflightPostEventAccess()),
                "session_and_accessibility": bool(backend._ready()),
                "secure_input_inactive": not backend.secure_input(),
            }
        else:
            report["checks"] = {"interactive_session": bool(backend.ready())}
        report["ready"] = all(report["checks"].values())
    except Exception as exc:
        report["error"] = type(exc).__name__
    return report


def message(report):
    if report["ready"]:
        return (
            "Los permisos y la sesión de este equipo están preparados para la entrada por red. "
            "Activa el modo en ambos equipos para comprobar la conexión y el cruce."
        )
    checks = report["checks"]
    if not checks:
        return "No se pudo comprobar la entrada. Revisa el diagnóstico de la aplicación."
    messages = {
        "listen_permission": "Revisa Monitorización de entrada para CleverSwitch en Privacidad y seguridad.",
        "post_permission": "Revisa el permiso de Accesibilidad de CleverSwitch en Privacidad y seguridad.",
        "session_and_accessibility": "Desbloquea la sesión del Mac y comprueba Accesibilidad para CleverSwitch.",
        "secure_input_inactive": "Entrada segura activa: termina de introducir la contraseña y vuelve a probar.",
        "interactive_session": "Desbloquea la sesión de Windows y cierra cualquier pantalla de confirmación de UAC.",
    }
    return "\n\n".join(messages[name] for name, available in checks.items() if not available)
