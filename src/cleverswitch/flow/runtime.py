"""Finite reconnection, passive offline waiting and suspended background HID work."""

import logging
import threading
import time

from .coordinator import REASONS, TERMINAL, Coordinator, Destination
from .cursor import Edge, native_cursor
from .environment import home_state, native_monitor, snapshot
from .hardware import Hardware
from .identity import Pairing
from .network import Discovery, Link
from .reconnect import Reconnect
from .version import EDITION, PROTOCOL

log = logging.getLogger(__name__)
LINK_EVENTS = frozenset(("authenticated", "announcement", "departure", "failure"))


class Runtime:
    def __init__(self, context, pairing=None, on_change=None):
        self.context = context
        self.context.policy.observe_only = True
        self.pairing = pairing or Pairing()
        self.on_change = on_change or (lambda: None)
        self.link = self.discovery = self.hardware = self.coordinator = self.destination = None
        self.cursor = self.edge = self.worker = self.monitor = None
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.event_lock = threading.Lock()
        self.events = set()
        self.transition = threading.Lock()
        self.enabled = False
        self.peer_ready = False  # Authenticated reachability; prepare checks readiness afresh.
        self.peer_departed = False
        self.peer_checked = 0
        self.error = None
        self.monitor_warning = False
        self.last_status = "Vincula Windows 1 y Mac 2 para activar los bordes"
        self.next_follow = 0
        self.follow_candidate = None
        self.peer_info = {}
        self.budget = Reconnect()
        self.environment = None
        self.listen_allowed = self.seek_allowed = False
        self.mode = "unconfigured"
        self.last_environment = None
        self.local_checks = []

    def wake(self, reason, source=None):
        with self.event_lock:
            if source is not None and source is not self.link:
                return
            if reason in ("announcement", "departure"):
                self.events.difference_update(("announcement", "departure"))
            self.events.add(reason)
            self.wake_event.set()

    def _events(self):
        with self.event_lock:
            events, self.events = self.events, set()
            self.wake_event.clear()
            return events

    def _mode(self, value):
        if self.mode != value:
            if self.mode == "online" or value == "online":
                # A normal reconnect retains its listeners and device objects.
                # Neither a parked edge nor old presence evidence is a new intent.
                if self.edge:
                    self.edge.cooldown()
                self.follow_candidate = None
                self.next_follow = 0
            self.mode = value
            self.on_change()

    def start(self):
        self.context.policy.set_background_suspended(True)
        config = self.pairing.load()
        if not config or not config.get("peer"):
            if config:
                self.last_status = "Enlace creado; falta importar la respuesta del Mac"
            return
        self.enabled = config.get("edges", False) is True
        try:
            self.monitor = native_monitor(self.wake)
            self.monitor.start()
        except Exception:
            if self.monitor:
                self.monitor.close()
            self.monitor = None
            self.monitor_warning = True
            log.debug("Native network notifications unavailable; manual reconnect remains available")
        self.wake("startup")
        self.worker = threading.Thread(target=self._run, name="flow-monitor", daemon=True)
        self.worker.start()

    def _connect(self):
        with self.transition:
            config = self.pairing.load()
            self.cursor = native_cursor()
            self.hardware = Hardware(self.context)
            host = 0 if config["role"] == "windows" else 1
            self.edge = Edge("right" if host == 0 else "left")
            self.edge.cooldown()  # A parked cursor after startup/resume is not a new crossing.
            self.destination = Destination(self.hardware, self.cursor, host, self.pairing.directory / "return.json")
            self.destination.arrived = self.edge.cooldown
            self.link = Link(self.pairing, self._handle)
            link = self.link
            link.on_authenticated = lambda: self.wake("authenticated", source=link)
            link.on_announcement = lambda: self.wake("announcement", source=link)
            link.on_departure = lambda: self.wake("departure", source=link)
            link.on_failure = lambda: self._rpc_failure(link)
            self.coordinator = Coordinator(self.hardware, self.cursor, self.link, self.destination)
            self.context.policy.host_change_handler = self._keyboard_event
            self.link.start()
            self.discovery = Discovery(self.link)
            try:
                self.discovery.start()
            except OSError:
                self.discovery.close()
                self.discovery = None
                log.debug("LAN announcements unavailable; configured address and manual reconnect remain usable")
            self.error = None

    def _evaluate(self, force=False):
        config = self.pairing.load()
        network = snapshot(config)
        sleeping = bool(self.monitor and self.monitor.sleeping)
        paused = self.context.policy.paused.is_set() or self.context.policy.closing.is_set()
        key = (network, sleeping, paused)
        changed = key != self.last_environment
        self.last_environment = key
        self.environment = network
        self.listen_allowed, self.seek_allowed = home_state(config, network)
        if changed or force:
            # Only three local cache reads while DHCP/ARP settles. No packets or
            # periodic offline polling, including when no interface is up yet.
            warming = not sleeping and not paused and (not network.address or not network.router_mac)
            now = time.monotonic()
            self.local_checks = [now + delay for delay in (2, 10, 30)] if warming else []
        if sleeping or paused or not self.listen_allowed:
            self.listen_allowed = self.seek_allowed = False
            self._disconnect(notify=True)
            self.budget.suspend()
            self.context.policy.set_background_suspended(True)
            self._mode("sleep" if sleeping else "paused" if paused else "outside")
            return
        if config["address"] != network.address:
            self._disconnect(notify=True)
            config["address"] = network.address
            self.pairing.save(config)
        if changed or force:
            self.peer_departed = False
            if self.seek_allowed:
                self.budget.restart()
                self._mode("searching")
            else:
                self.budget.suspend()
                self._mode("standby")

    def _learn_router(self):
        network = self.environment
        if not network or not network.gateway or not network.router_mac:
            return
        config = self.pairing.load()
        if not config.get("home_router"):
            # Only learn after mutual TLS, not from an SSID, IP range or UDP hint.
            config["home_router"] = {"gateway": network.gateway, "mac": network.router_mac}
            self.pairing.save(config)

    def _safety_pending(self):
        return bool(
            self.coordinator
            and self.coordinator.busy.locked()
            or self.destination
            and self.destination.state
            and self.destination.state["phase"] not in TERMINAL
        )

    def _handle(self, op, args):
        if op != "follow":
            return self.destination.handle(op, args)
        if args:
            raise ValueError("Parámetros inesperados")
        if (
            not self.coordinator.can_start()
            or not self.hardware.allowed()
            or not self.cursor.session_ready()
            or not self.hardware.known()
        ):
            return {"accepted": False}
        if self.hardware.probe("mouse") is True and self.hardware.probe("keyboard") is False:
            return {"accepted": self.coordinator.request("keyboard")}
        return {"accepted": False}

    def _follow_keyboard(self):
        now = time.monotonic()
        if now < self.next_follow:
            return
        if not self.coordinator.can_start() or not self.hardware.allowed():
            self.follow_candidate = None
            return
        self.next_follow = now + 1
        if self.destination.state and self.destination.state["phase"] not in TERMINAL:
            self.follow_candidate = None
            return
        if not self.cursor.session_ready():
            self.follow_candidate = None
            return
        if self.hardware.probe("keyboard") is True and self.hardware.probe("mouse") is False:
            if self.follow_candidate is None:
                self.follow_candidate = now
                return
            if now - self.follow_candidate < 0.6:
                return
            self.follow_candidate = None
            self.next_follow = now + 10
            log.info("Keyboard arrived without mouse; requesting companion follow")
            try:
                self.link.call("follow")
            except (OSError, ValueError):
                self.wake("failure")
        else:
            self.follow_candidate = None

    def _rpc_failure(self, source=None):
        # The transaction owns its finite retries. Do not suspend its HID readers
        # because one of those individual RPC responses was lost.
        if not self._safety_pending():
            self.wake("failure", source=source)

    def _keyboard_event(self, event):
        coordinator = self.coordinator
        if coordinator and self.peer_ready:
            return coordinator.keyboard_event(event)
        # Buttons are never diverted; offline presses keep their native behavior.
        return True

    def _disconnect(self, notify=False):
        with self.transition:
            if notify and self.discovery and self.peer_ready:
                try:
                    self.discovery.announce("departing")
                except OSError:
                    log.debug("Departure notice could not be sent; peer timeout remains bounded")
            for component in (self.coordinator, self.destination, self.link, self.discovery, self.hardware):
                if component:
                    try:
                        component.close()
                    except Exception:
                        self.error = "Cierre incompleto; comprueba los dispositivos con sus botones físicos"
                        log.debug("Local component cleanup failed")
            self.coordinator = self.destination = self.link = self.discovery = self.hardware = None
            self.peer_ready = False
            self.peer_departed = False
            self.peer_info = {}
            self.follow_candidate = None
            self.next_follow = 0
            with self.event_lock:
                self.events.difference_update(LINK_EVENTS)

    def _attempt(self):
        self._evaluate()
        if not self.listen_allowed:
            return
        try:
            if self.link is None:
                self._connect()
            if self.seek_allowed and self.discovery and not self.budget.online:
                try:
                    self.discovery.announce()
                except OSError:
                    log.debug("LAN announcement failed; trying the configured TLS peer")
            self.peer_info = self.link.call("ready", timeout=2)
            self.budget.authenticated()
            self.peer_ready = True
            self.peer_checked = time.monotonic()
            self._learn_router()
            self._mode("online")
            self.on_change()
        except Exception:
            self.peer_ready = False
            self.budget.failed()
            self._mode("searching" if self.budget.next is not None else "standby")
            # Partial listener initialization must not retain failed sockets/threads.
            if self.link is None or self.link.worker is None:
                self._disconnect()

    def _step(self):
        events = self._events()
        previous_link = self.link
        if self.local_checks and time.monotonic() >= self.local_checks[0]:
            self.local_checks.pop(0)
            self._evaluate()
        if events & {"startup", "network", "resume", "sleep", "pause", "manual"}:
            self._evaluate(force=bool(events & {"startup", "resume", "manual"}))
        if self.link is not previous_link:
            events.difference_update(LINK_EVENTS)
        if self.listen_allowed and self.link is None and self.budget.next is None and not self.seek_allowed:
            # Unknown ARP identity may listen passively, without starting a retry loop.
            try:
                self._connect()
            except Exception:
                self._disconnect()
                self._mode("standby")
        if "authenticated" in events and self.listen_allowed:
            self.peer_departed = False
            self.budget.authenticated()
            self.peer_ready = True
            self.peer_checked = time.monotonic()
            self._learn_router()
            self._mode("online")
            self.on_change()
        if "announcement" in events and self.listen_allowed:
            self.peer_departed = False
            self.budget.hint()
        if "departure" in events and self.listen_allowed:
            self.peer_departed = True
        if self.peer_departed and not self._safety_pending():
            self.peer_ready = False
            self.peer_info = {}
            self.budget.suspend()
            self._mode("standby")
        if "failure" in events and self.budget.online and not self._safety_pending():
            self.peer_ready = False
            self.budget.failed()
            self._mode("searching")
        if self.budget.due() and not self._safety_pending():
            self._attempt()
        pending = self._safety_pending()
        self.context.policy.set_background_suspended(not (self.peer_ready or pending))
        if self.destination:
            self.destination.expire()
        if pending:
            # A deferred heartbeat may already be due. Do not turn its zero
            # delay into a busy loop while the finite transaction owns the link.
            return 0.2
        if not self.peer_ready:
            wait = self.budget.wait()
            if self.local_checks:
                local_wait = max(0, self.local_checks[0] - time.monotonic())
                wait = local_wait if wait is None else min(wait, local_wait)
            return wait  # None -> no timer, waits only for an event.
        mouse_here = self.hardware.device("mouse", connected=True) is not None
        if self.enabled and mouse_here and self.coordinator.can_start() and self.hardware.allowed():
            y = self.edge.poll(self.cursor.sample())
            if y is not None:
                self.coordinator.request("edge", y)
        self._follow_keyboard()
        interval = 0.05 if self.enabled and mouse_here else 1
        return min(interval, self.budget.wait() if self.budget.next is not None else interval)

    def _run(self):
        self.context.policy.host_change_handler = self._keyboard_event
        try:
            while not self.stop_event.is_set():
                try:
                    wait = self._step()
                except Exception:
                    log.exception("Local switch monitor failed")
                    self.error = "Cambio bloqueado; revisa la sesión y los permisos"
                    self.peer_ready = False
                    self.budget.suspend()
                    self.context.policy.set_background_suspended(not self._safety_pending())
                    self._mode("standby")
                    wait = 0.2 if self._safety_pending() else None
                self.wake_event.wait(wait)
        finally:
            self._disconnect(notify=True)
            self.context.policy.set_background_suspended(True)

    def status(self):
        if self.error:
            return self.error
        if self.mode == "unconfigured":
            return self.last_status
        if self.mode == "outside":
            return "Fuera de la red de casa · sin intentos de conexión"
        if self.mode == "sleep":
            return "Equipo en reposo · enlace suspendido"
        if self.mode == "paused":
            return "Cambio automático pausado · enlace suspendido"
        if self.mode == "standby":
            if self.peer_departed:
                return "Compañero en reposo o cerrado · esperando su aviso de regreso"
            return (
                "Espera pasiva · usa Reconectar ahora si vuelve el compañero"
                if self.monitor_warning
                else "Compañero ausente · espera pasiva sin búsquedas"
            )
        if self.mode == "searching":
            return f"Buscando compañero · intento {min(self.budget.attempts + 1, 7)}/7"
        # Menu callbacks run concurrently with listener replacement. Snapshot
        # references instead of blocking the native UI on the transition lock.
        hardware, cursor, coordinator = self.hardware, self.cursor, self.coordinator
        if hardware is None or cursor is None or coordinator is None:
            return "Reiniciando el enlace local…"
        if not hardware.known():
            return "Conecta ambos dispositivos aquí una vez para reconocerlos"
        if not cursor.session_ready():
            return "Cambio bloqueado: sesión o Accesibilidad no disponibles"
        if self.peer_info and self.peer_info.get("protocol") != PROTOCOL:
            return "Actualiza CleverSwitch en ambos equipos para usar el traslado seguro"
        if coordinator.busy.locked():
            return coordinator.status
        if coordinator.status != "Enlace local listo; cambio por bordes desactivado":
            return coordinator.status
        if self.peer_info.get("ready") is False:
            return "Destino no preparado: " + REASONS.get(
                self.peer_info.get("reason"), "revisa el menú del otro equipo"
            )
        return "Bordes activos · Windows 1 ←→ Mac 2" if self.enabled else "Enlace listo · bordes desactivados"

    def toggle_edges(self):
        config = self.pairing.load()
        if not config or not config.get("peer"):
            raise ValueError("Vincula los equipos primero")
        self.enabled = not self.enabled
        config["edges"] = self.enabled
        self.pairing.save(config)
        if self.edge:
            self.edge.cooldown()
        self.wake("options")

    def diagnostic(self):
        coordinator = self.coordinator
        devices = [
            {"role": d.role, "name": d.name, "recognized": self.context.policy.allows(d), "connected": d.connected}
            for d in self.context.device_registry.all_entries()
        ]
        return {
            "edition": EDITION,
            "protocol": PROTOCOL,
            "mode": self.mode,
            "edges": self.enabled,
            "peer_reachable": self.peer_ready,
            "peer": self.peer_info,
            "devices": devices,
            "status": self.status(),
            "last_failure": coordinator.last_error if coordinator else None,
            "phase": coordinator.phase if coordinator else None,
            "energy": {
                "native_events_available": not self.monitor_warning,
                "background_suspended": self.context.policy.background_suspended.is_set(),
                "waiting_for_peer_notice": self.mode == "standby",
                "active_retry_scheduled": not self.budget.online and self.budget.next is not None,
                "attempts": self.budget.attempts,
            },
        }

    def reconnect_now(self):
        self.error = None
        self.wake("manual")

    def recover(self):
        coordinator = self.coordinator
        if coordinator and coordinator.recover():
            self.wake("recovery")  # Resume presence checks even from passive standby.
            return True
        return False

    def close(self):
        self.stop_event.set()
        self.wake("stop")
        if self.coordinator:
            self.coordinator.stop_event.set()
        if self.worker:
            self.worker.join(timeout=15)
            if self.worker.is_alive():
                raise RuntimeError("Espera a que termine el cambio antes de reiniciar el enlace")
        if self.monitor:
            self.monitor.close()
            self.monitor = None
        self.context.policy.host_change_handler = None
