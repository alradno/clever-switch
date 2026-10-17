"""Tray UI; preview mode never imports the HID transport or application engine."""

import argparse
import logging
import os
import pathlib
import sys
import threading
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

from ..flow.version import EDITION
from . import firewall, startup
from .actions import Actions
from .icon import create_icon

log = logging.getLogger(__name__)


def configure_flow_logging():
    directory = pathlib.Path.home() / ".config/cleverswitch-tray"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "flow.log", maxBytes=262144, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("cleverswitch.flow")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


class Controller:
    def __init__(self, preview=False):
        self.preview = preview
        self.context = None
        self.worker = None
        self.paused = False
        self.error = None
        self.flow = None
        self.refresh_event = threading.Event()
        self.stopping = threading.Event()
        self.flow_lock = threading.RLock()
        self.installation = {"state": "unchecked"}
        self.installation_lock = threading.Lock()

    def check_installation(self, repair=False):
        if self.preview or sys.platform != "win32":
            return
        from ..flow.identity import Pairing

        with self.installation_lock:
            if self.stopping.is_set():
                return
            try:
                self.installation = firewall.inspect(Pairing().load(), repair=repair)
            except (OSError, ValueError, KeyError):
                self.installation = {"state": "unavailable"}
        self.refresh_event.set()

    def start(self):
        if self.preview:
            return
        # This runs on the main thread: setup_context registers POSIX signals.
        from ..discovery.discovery import discover
        from ..setup.app_setup import setup_context

        self.context = setup_context(
            argparse.Namespace(config=None, verbose=False, verbose_extra=False), native_loop=True
        )
        # Physical Easy-Switch must survive application failure. Observe analytics
        # where supported; never divert keys in the tray/Flow application.
        self.context.policy.observe_only = True
        self.context.policy.set_background_suspended(True)

        def shutdown_wakeup():
            # SIGTERM sets shutdown, while passive discovery/menu wait on other
            # events. Bridge that event without introducing periodic polling.
            self.context.shutdown.wait()
            self.context.policy.wake_background()
            self.refresh_event.set()

        threading.Thread(target=shutdown_wakeup, name="cleverswitch-shutdown", daemon=True).start()

        def run_engine():
            try:
                discover(self.context)
                if not self.context.shutdown.is_set():
                    self.error = "La conexión se ha detenido"
            except Exception:
                self.error = "Error de conexión; reinicia la aplicación"
                log.exception("Device engine failed")

        self.worker = threading.Thread(target=run_engine, name="cleverswitch-discovery", daemon=True)
        self.worker.start()
        self.start_flow()

    def start_flow(self):
        from ..flow.runtime import Runtime

        with self.flow_lock:
            if self.stopping.is_set():
                return
            if self.flow:
                self.flow.close()
            self.flow = Runtime(self.context, on_change=self.refresh_event.set)
            try:
                self.flow.start()
            except (OSError, ValueError):
                self.flow.error = "No se pudo cargar el enlace local"
                log.exception("Local pairing initialization failed")

    def status(self):
        if self.preview:
            return "Vista previa — sin conexión a dispositivos"
        if self.error:
            return self.error
        if self.paused:
            return "Cambio automático pausado"
        if self.context.policy.background_suspended.is_set():
            return "En espera · consultas a dispositivos suspendidas"
        entries = [
            d for d in self.context.device_registry.all_entries() if self.context.policy.allows(d) and d.connected
        ]
        if any(sum(d.role == role for d in entries) > 1 for role in ("keyboard", "mouse")):
            return "Más de una pareja detectada; cambio bloqueado"
        if {d.role for d in entries} == {"keyboard", "mouse"}:
            return "Cambio automático activo"
        return "Esperando al ratón y teclado en este equipo"

    def device_status(self, role):
        name = "MX Keys" if role == "keyboard" else "MX Master 3"
        if self.preview:
            return name + " — vista previa"
        if self.context is None:
            return name + " — sin conexión"
        if self.context.policy.background_suspended.is_set():
            return name + " — consultas suspendidas"
        devices = [
            d
            for d in self.context.device_registry.all_entries()
            if d.role == role and self.context.policy.allows(d) and d.connected
        ]
        if len(devices) > 1:
            return name + " — pareja ambigua"
        if not devices:
            return name + " — no conectado aquí"
        return name + (" — Bluetooth" if devices[0].slot == 0xFF else " — receptor USB")

    def toggle_pause(self):
        self.paused = not self.paused
        if self.preview:
            return
        from ..event.set_report_flag_event import SetReportFlagEvent

        if self.paused:
            self.context.policy.paused.set()
        else:
            self.context.policy.paused.clear()
        for device in self.context.device_registry.all_entries():
            if device.role == "keyboard" and self.context.policy.allows(device) and device.connected:
                self.context.topics.flags.publish(
                    SetReportFlagEvent(slot=device.slot, pid=device.pid, wpid=device.wpid, enable=not self.paused)
                )
        self.context.topics.flags.drain()
        self.context.topics.write.drain()
        if self.flow:
            self.flow.wake("pause")
        self.refresh_event.set()

    def stop(self):
        self.stopping.set()
        if self.context is not None:
            self.context.policy.closing.set()
            with self.flow_lock:
                if self.flow:
                    self.flow.close()
            self.context.shutdown.set()
            self.context.policy.wake_background()
            self.refresh_event.set()
            if self.worker is not None:
                self.worker.join(timeout=5)


@contextmanager
def single_instance():
    """OS lock is released on exit/crash; file remains only as a lock carrier."""
    directory = pathlib.Path.home() / ".config/cleverswitch-tray"
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "instance.lock").open("a+b")
    if os.fstat(handle.fileno()).st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        handle.close()


def run(preview=False, preview_seconds=0):
    import pystray

    controller = Controller(preview)
    actions = Actions(controller.refresh_event.set)
    refreshed = threading.Event()
    refresh_lock = threading.Lock()
    try:
        controller.start()
    except (Exception, SystemExit):
        controller.error = "No se pudo iniciar la conexión; revisa los permisos"
        log.exception("Device initialization failed")

    if not preview and sys.platform == "win32" and getattr(sys, "frozen", False):
        threading.Thread(target=controller.check_installation, name="cleverswitch-installation", daemon=True).start()

    def pause(icon, item):
        controller.toggle_pause()
        icon.icon = create_icon(controller.paused, sys.platform == "darwin")
        icon.update_menu()

    def autostart(icon, item):
        try:
            startup.set_enabled(not startup.is_enabled())
        except Exception:
            controller.error = "No se pudo cambiar el inicio automático"
            log.exception("Login startup update failed")
        icon.update_menu()

    def quit_app(icon, item):
        actions.closed.set()
        # Finish any in-progress redraw before pystray destroys its menu handle.
        # Wake the setup thread now, so it cannot redraw during icon.stop().
        with refresh_lock:
            refreshed.set()
            controller.refresh_event.set()
        controller.stop()
        icon.stop()

    def noop(icon, item):
        pass

    def flow_status(item):
        if preview:
            return "Bordes · vista previa sin red ni dispositivos"
        return controller.flow.status() if controller.flow else "Enlace sin iniciar"

    selected_network = [None]

    def networks():
        if preview:
            return []
        from ..flow.identity import local_addresses

        return local_addresses()

    def choose_network(value):
        def action(icon, item):
            selected_network[0] = value

        return action

    def network_menu():
        return [
            pystray.MenuItem(
                f"{name}: {address}",
                choose_network((address, network)),
                checked=lambda item, value=(address, network): selected_network[0] == value,
            )
            for name, address, network in networks()
        ]

    def pair(icon, item):
        from ..flow.identity import Pairing
        from . import dialogs

        try:
            current_network = selected_network[0]
            if current_network is None:
                choices = networks()
                if len(choices) != 1:
                    dialogs.message("Selecciona tu red", "Selecciona Wi-Fi o Ethernet en el menú Red local primero.")
                    return
                current_network = choices[0][1:]
            address, network = current_network
            pairing = Pairing()
            config = pairing.load()
            if sys.platform == "win32" and not config:
                path = dialogs.choose_file(True, "Guardar enlace para el Mac")
                if not path or actions.closed.is_set():
                    return
                if path:
                    pairing.create_invitation(path, address, network)
                    dialogs.message(
                        "Enlace creado",
                        "Lleva este archivo al Mac e impórtalo desde su icono. "
                        "Luego trae la respuesta a Windows. Los archivos de enlace son privados; "
                        "bórralos después de completar el enlace.",
                    )
            elif sys.platform == "win32" and config and not config.get("peer"):
                path = dialogs.choose_file(False, "Importar respuesta del Mac")
                if not path or actions.closed.is_set():
                    return
                if path:
                    pairing.accept_response(path)
                    dialogs.message(
                        "Equipos vinculados",
                        "Windows usa 1 y el Mac 2. Conecta el ratón y teclado "
                        "en cada equipo una vez para reconocerlos. Después activa Cambio por bordes.",
                    )
            elif sys.platform == "darwin" and not config:
                path = dialogs.choose_file(False, "Importar enlace de Windows")
                if not path or actions.closed.is_set():
                    return
                if path:
                    response = dialogs.choose_file(
                        True, "Guardar respuesta para Windows", "CleverSwitch-respuesta.json"
                    )
                    if not response or actions.closed.is_set():
                        return
                    if response:
                        pairing.accept_invitation(path, response, address, network)
                        dialogs.message(
                            "Respuesta creada",
                            "Importa esta respuesta en Windows. Después borra ambos "
                            "archivos de enlace. Conecta ratón y teclado al canal 2 una vez aquí y "
                            "concede Accesibilidad en Ajustes del sistema para usar los bordes.",
                        )
            else:
                dialogs.message(
                    "Enlace existente",
                    "Estos equipos ya están vinculados. Si cambias de red, "
                    "selecciona Red local y usa Actualizar la red del enlace.",
                )
            if not actions.closed.is_set():
                controller.start_flow()
                controller.check_installation(repair=True)
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Known local validation errors only; never display secret JSON content.
            log.debug("Pairing operation failed: %s", type(error).__name__)
            dialogs.message(
                "No se pudo vincular",
                "Comprueba la red seleccionada y el archivo de enlace. "
                "Usa el archivo original de la aplicación del otro equipo.",
            )
        controller.refresh_event.set()

    def update_network(icon, item):
        from ..flow.identity import Pairing
        from . import dialogs

        try:
            if selected_network[0] is None:
                raise ValueError("Selecciona una red")
            pairing = Pairing()
            config = pairing.load()
            if not config:
                raise ValueError("Vincula primero")
            config["address"], config["network"] = selected_network[0]
            config.pop("home_router", None)  # Explicitly selecting a new home network; retain peer identity.
            pairing.save(config)
            controller.start_flow()
            controller.check_installation(repair=True)
        except (OSError, ValueError):
            dialogs.message("Red sin actualizar", "Selecciona primero la red Wi-Fi o Ethernet del router compartido.")

    def export_link(icon, item):
        from ..flow.identity import Pairing
        from . import dialogs

        try:
            path = dialogs.choose_file(True, "Guardar otra copia del enlace para el Mac")
            if path and not actions.closed.is_set():
                Pairing().export_invitation(path)
        except (OSError, ValueError):
            dialogs.message(
                "Enlace sin guardar",
                "Crea primero un enlace en Windows. Sólo se puede exportar "
                "mientras esté pendiente la respuesta del Mac.",
            )

    def export_downloads(icon, item):
        """Alternative when an Explorer shell extension blocks the file picker."""
        import secrets

        from ..flow.identity import Pairing
        from . import dialogs
        from .windows_dialog import downloads_directory

        try:
            pairing = Pairing()
            config = pairing.load()
            if config and config.get("peer"):
                dialogs.message("Enlace existente", "Estos equipos ya están vinculados.")
                return
            path = downloads_directory() / f"CleverSwitch-enlace-{secrets.token_hex(4)}.json"
            if not config:
                current_network = selected_network[0]
                if current_network is None:
                    choices = networks()
                    if len(choices) != 1:
                        dialogs.message(
                            "Selecciona tu red", "Selecciona Wi-Fi o Ethernet en el menú Red local primero."
                        )
                        return
                    current_network = choices[0][1:]
                if actions.closed.is_set():
                    return
                pairing.create_invitation(path, *current_network)
            else:
                if actions.closed.is_set():
                    return
                pairing.export_invitation(path)
            if not actions.closed.is_set():
                controller.start_flow()
                controller.check_installation(repair=True)
                dialogs.message(
                    "Enlace guardado en Descargas",
                    f"Archivo: {path}\n\nLlévalo al Mac e importa allí el enlace. "
                    "No lo compartas con terceros; bórralo al completar la vinculación.",
                )
        except (OSError, ValueError, KeyError, TypeError):
            dialogs.message(
                "Enlace sin guardar", "Comprueba la red seleccionada y los permisos de la carpeta Descargas."
            )

    def edges(icon, item):
        if controller.flow:
            try:
                controller.flow.toggle_edges()
            except (OSError, ValueError):
                from . import dialogs

                dialogs.message("Vincula los equipos", "Completa el enlace de Windows y Mac primero.")
        controller.refresh_event.set()

    def recover(icon, item):
        if controller.flow:
            controller.flow.recover()

    def reconnect(icon, item):
        if controller.flow:
            controller.flow.reconnect_now()
        icon.update_menu()

    def diagnostic(icon, item):
        import json

        from . import dialogs

        if controller.flow:
            controller.check_installation()
            data = controller.flow.diagnostic()
            if sys.platform == "win32":
                data["installation"] = controller.installation
            dialogs.message("Diagnóstico de CleverSwitch", json.dumps(data, ensure_ascii=False, indent=2))

    def repair_firewall(icon, item):
        from . import dialogs

        controller.check_installation(repair=True)
        dialogs.message("Firewall de Windows", firewall.message(controller.installation))
        if controller.installation.get("state") == "ready" and controller.flow:
            controller.flow.reconnect_now()

    items = [
        pystray.MenuItem(f"CleverSwitch {EDITION} · MX Keys + MX Master 3", noop, enabled=False),
        pystray.MenuItem(lambda item: controller.status(), noop, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: controller.device_status("keyboard"), noop, enabled=False),
        pystray.MenuItem(lambda item: controller.device_status("mouse"), noop, enabled=False),
        pystray.MenuItem(flow_status, noop, enabled=False),
        pystray.MenuItem(
            lambda item: "Firewall · " + firewall.message(controller.installation),
            noop,
            enabled=False,
            visible=sys.platform == "win32" and not preview,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Vincular Windows 1 y Mac 2…", actions.wrap(pair), enabled=lambda item: not preview and not actions.busy
        ),
        pystray.MenuItem(
            "Guardar enlace para Mac…",
            actions.wrap(export_link),
            enabled=lambda item: not preview and sys.platform == "win32" and not actions.busy,
        ),
        pystray.MenuItem(
            "Guardar enlace en Descargas (sin selector)",
            actions.wrap(export_downloads),
            enabled=lambda item: not preview and sys.platform == "win32" and not actions.busy,
            visible=sys.platform == "win32",
        ),
        pystray.MenuItem(
            "Red local", pystray.Menu(network_menu), enabled=lambda item: not preview and not actions.busy
        ),
        pystray.MenuItem(
            "Actualizar la red del enlace",
            actions.wrap(update_network),
            enabled=lambda item: not preview and not actions.busy,
        ),
        pystray.MenuItem(
            "Cambio por bordes",
            actions.wrap(edges),
            enabled=lambda item: not preview and not actions.busy,
            checked=lambda item: bool(controller.flow and controller.flow.enabled),
        ),
        pystray.MenuItem("Traer ratón y teclado aquí", recover, enabled=not preview),
        pystray.MenuItem("Reconectar ahora", reconnect, enabled=not preview),
        pystray.MenuItem(
            "Ver diagnóstico…", actions.wrap(diagnostic), enabled=lambda item: not preview and not actions.busy
        ),
        pystray.MenuItem(
            "Revisar firewall de Windows…",
            actions.wrap(repair_firewall),
            enabled=lambda item: not preview and not actions.busy,
            visible=sys.platform == "win32",
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: "Reanudar cambio automático" if controller.paused else "Pausar cambio automático", pause
        ),
        pystray.MenuItem(
            "Iniciar al entrar en la sesión",
            autostart,
            checked=lambda item: startup.is_enabled(),
            enabled=not preview and bool(getattr(sys, "frozen", False)),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Salir", quit_app),
    ]
    icon = pystray.Icon(
        "CleverSwitchTray",
        create_icon(mac=sys.platform == "darwin"),
        "CleverSwitch — cambio conjunto de ratón y teclado",
        pystray.Menu(*items),
    )

    def setup(icon):
        icon.visible = True
        if preview and preview_seconds:
            threading.Timer(preview_seconds, lambda: quit_app(icon, None)).start()
        while not refreshed.is_set():
            quiet = not controller.flow or controller.flow.mode in (
                "unconfigured",
                "outside",
                "sleep",
                "paused",
                "standby",
            )
            controller.refresh_event.wait(None if quiet and not preview else 2)
            controller.refresh_event.clear()
            if refreshed.is_set():
                break

            def refresh():
                with refresh_lock:
                    if refreshed.is_set():
                        return
                    icon.title = "CleverSwitch — " + controller.status()
                    icon.update_menu()

            if sys.platform == "darwin":
                from Foundation import NSOperationQueue

                NSOperationQueue.mainQueue().addOperationWithBlock_(refresh)
            else:
                refresh()
            if controller.context and controller.context.shutdown.is_set():
                icon.stop()
                break

    try:
        # Main-thread runloop is required for the native macOS menu bar.
        icon.run(setup=setup)
    finally:
        actions.closed.set()
        refreshed.set()
        controller.refresh_event.set()
        controller.stop()


def main():
    parser = argparse.ArgumentParser(description="CleverSwitch Tray — sólo MX Keys + MX Master 3")
    parser.add_argument(
        "--preview", action="store_true", help="Mostrar el icono sin acceso a HID ni configuración de Inicio"
    )
    parser.add_argument("--preview-seconds", type=int, default=0)
    args = parser.parse_args()
    if args.preview:
        run(True, max(0, args.preview_seconds))
    else:
        with single_instance() as acquired:
            if acquired:
                try:
                    configure_flow_logging()
                except OSError:
                    log.warning("Cannot open the local switch diagnostic log")
                run()
