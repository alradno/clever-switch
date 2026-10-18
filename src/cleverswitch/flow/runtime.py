"""Finite reconnection, passive offline waiting and suspended background HID work."""

import logging
import threading
import time

from .coordinator import REASONS, TERMINAL, Coordinator, Destination
from .cursor import Edge, native_cursor
from .environment import home_state, native_monitor, snapshot
from .hardware import Hardware
from .identity import Pairing
from .input_protocol import VERSION as INPUT_VERSION
from .input_service import Service as InputService
from .input_service import native_input
from .network import Discovery, Link
from .reconnect import Reconnect
from .version import EDITION, PROTOCOL

log = logging.getLogger(__name__)
LINK_EVENTS = frozenset(("authenticated", "announcement", "departure", "failure"))


class Runtime:
    def __init__(self, context, pairing=None, on_change=None, input_factory=None):
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
        self.input_enabled = False
        self.input_factory = input_factory or native_input
        self.input_service = None
        self.input_budget = Reconnect()
        self.input_seen_channel = None
        self.input_peer = None
        self.input_error = None
        self.input_recovery = []
        self.input_online_since = 0
        self.input_requested = None
        self.input_last_ready = None
        self.peer_ready = False  # Authenticated reachability; prepare checks readiness afresh.
        self.peer_departed = False
        self.peer_checked = 0
        self.error = None
        self.monitor_warning = False
        self.last_status = "Vincula Windows 1 y Mac 2 para activar los bordes"
        self.next_follow = 0
        self.follow_candidate = None
        self.follow_worker = None
        self.follow_generation = 0
        self.follow_lock = threading.Lock()
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
                with self.follow_lock:
                    self.follow_candidate = None
                    self.next_follow = 0
                    self.follow_generation += 1
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
        self.input_enabled = config.get("network_input", False) is True
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
            if self.input_enabled:
                self.input_service = InputService(
                    link,
                    self.input_factory(),
                    enabled=True,
                    on_change=lambda: self.wake("input", source=link),
                    can_start=lambda: not self.input_recovery and not self._safety_pending(),
                )
                self.input_budget.restart()
                self.input_seen_channel = self.input_peer = None
                self.input_last_ready = None
                self.input_error = None
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
        if self.input_enabled:
            if op == "ready":
                if args:
                    raise ValueError("Parámetros inesperados")
                return {
                    "edition": EDITION,
                    "protocol": PROTOCOL,
                    "ready": False,
                    "reason": "input",
                    "input": {
                        "version": INPUT_VERSION,
                        "enabled": True,
                        "available": bool(self.input_service and self.input_service.backend.ready()),
                        "capabilities": self.input_service.capabilities() if self.input_service else {},
                    },
                }
            if op in ("follow", "prepare"):
                return {"accepted": False, "reason": "input"}
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

    def _schedule_follow(self):
        if self.input_enabled:
            return
        if time.monotonic() < self.next_follow or (self.follow_worker and self.follow_worker.is_alive()):
            return
        if not self.coordinator.can_start() or not self.hardware.allowed():
            return
        self.follow_worker = threading.Thread(
            target=self._follow_keyboard, args=(self.follow_generation,), name="flow-follow", daemon=True
        )
        self.follow_worker.start()

    def _follow_keyboard(self, generation=None):
        if self.input_enabled:
            return
        # Snapshot this incarnation. A slow probe must not apply to a replacement
        # link or an offline/reconnected session, nor block the cursor monitor.
        hardware, coordinator, cursor, link, destination = (
            self.hardware,
            self.coordinator,
            self.cursor,
            self.link,
            self.destination,
        )

        def current():
            return generation is None or (
                generation == self.follow_generation
                and self.peer_ready
                and not self.stop_event.is_set()
                and link is self.link
                and hardware.allowed()
                and coordinator.can_start()
            )

        now = time.monotonic()
        if not current() or now < self.next_follow:
            return
        if not coordinator.can_start() or not hardware.allowed():
            self.follow_candidate = None
            return
        self.next_follow = now + 1
        if destination.state and destination.state["phase"] not in TERMINAL:
            self.follow_candidate = None
            return
        if not cursor.session_ready():
            self.follow_candidate = None
            return
        try:
            keyboard_here = hardware.probe("keyboard") is True
            if not current():
                return
            missing_mouse = keyboard_here and hardware.probe("mouse") is False
        except (OSError, RuntimeError):
            if current():
                self.follow_candidate = None
            return
        with self.follow_lock:
            if not current():
                return
            if missing_mouse:
                if self.follow_candidate is None:
                    self.follow_candidate = now
                    return
                if now - self.follow_candidate < 0.6:
                    return
                self.follow_candidate = None
                self.next_follow = now + 10
            else:
                self.follow_candidate = None
                return
        if current():
            log.info("Keyboard arrived without mouse; requesting companion follow")
            try:
                link.call("follow")
            except (OSError, ValueError):
                self.wake("failure", source=link)

    def _rpc_failure(self, source=None):
        # The transaction owns its finite retries. Do not suspend its HID readers
        # because one of those individual RPC responses was lost.
        if not self._safety_pending():
            self.wake("failure", source=source)

    def _keyboard_event(self, event):
        if self.input_enabled:
            # Easy-Switch remains firmware-owned. Local physical input on the
            # destination ends remote focus; no radio follow command is sent.
            return True
        coordinator = self.coordinator
        if coordinator and self.peer_ready:
            return coordinator.keyboard_event(event)
        # Buttons are never diverted; offline presses keep their native behavior.
        return True

    def _disconnect(self, notify=False):
        with self.transition:
            with self.follow_lock:
                self.follow_generation += 1
            if notify and self.discovery and self.peer_ready:
                try:
                    self.discovery.announce("departing")
                except OSError:
                    log.debug("Departure notice could not be sent; peer timeout remains bounded")
            for component in (
                self.input_service,
                self.coordinator,
                self.destination,
                self.link,
                self.discovery,
                self.hardware,
            ):
                if component:
                    try:
                        component.close()
                    except Exception:
                        self.error = "Cierre incompleto; comprueba los dispositivos con sus botones físicos"
                        log.debug("Local component cleanup failed")
            if (
                self.input_service
                and self.input_service.session
                and (
                    self.input_service.session.unreleased
                    or self.input_service.session.pending_locks is not None
                    or self.input_service.session.pending_capture
                )
            ):
                self.input_recovery.append(self.input_service.session)
            self.input_service = None
            self.input_budget.suspend()
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
            if self.input_service:
                self.input_service.suspend()
            self.budget.failed()
            self._mode("searching" if self.budget.next is not None else "standby")
            # Partial listener initialization must not retain failed sockets/threads.
            if self.link is None or self.link.worker is None:
                self._disconnect()

    def _step(self):
        events = self._events()
        previous_link = self.link
        if "input_options" in events and self.input_requested is not None:
            if self._safety_pending():
                self.error = "Espera a que termine el traslado antes de cambiar el modo de entrada"
            else:
                self._disconnect(notify=True)
                config = self.pairing.load()
                config["network_input"] = self.input_requested
                self.pairing.save(config)
                self.input_enabled = self.input_requested
                self.error = None
                events.add("manual")
            self.input_requested = None
        if self.local_checks and time.monotonic() >= self.local_checks[0]:
            self.local_checks.pop(0)
            self._evaluate()
        if events & {"startup", "network", "resume", "sleep", "pause", "manual"}:
            self._evaluate(force=bool(events & {"startup", "resume", "manual"}))
        if self.link is not previous_link:
            events.difference_update(LINK_EVENTS)
        if events & {"manual", "network", "resume"}:
            self.input_budget.restart()
        elif "announcement" in events:
            self.input_budget.hint()
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
        self.context.policy.set_background_suspended(not (pending or (self.peer_ready and not self.input_enabled)))
        if self.destination:
            self.destination.expire()
        if pending:
            # A deferred heartbeat may already be due. Do not turn its zero
            # delay into a busy loop while the finite transaction owns the link.
            return 0.2
        if not self.peer_ready:
            if self.input_service:
                self.input_service.suspend()
            wait = self.budget.wait()
            if self.local_checks:
                local_wait = max(0, self.local_checks[0] - time.monotonic())
                wait = local_wait if wait is None else min(wait, local_wait)
            return wait  # None -> no timer, waits only for an event.
        if self.input_enabled:
            return self._input_step()
        mouse_here = self.hardware.device("mouse", connected=True) is not None
        if self.enabled and mouse_here and self.coordinator.can_start() and self.hardware.allowed():
            y = self.edge.poll(self.cursor.sample())
            if y is not None:
                self.coordinator.request("edge", y)
        self._schedule_follow()
        interval = 0.05 if self.enabled and mouse_here else 1
        if self.enabled and mouse_here and self.edge.since is not None and not self.edge.latched:
            interval = 0.01  # Only while confirming the brief edge gesture.
        return min(interval, self.budget.wait() if self.budget.next is not None else interval)

    def _input_step(self):
        """No edge/HID polling in LAN mode. Retry streams only with a finite budget."""
        service = self.input_service
        wait = self.budget.wait()
        if service is None:
            return wait
        peer = self.peer_info.get("input", {})
        compatible = (
            isinstance(peer, dict)
            and peer.get("enabled") is True
            and type(peer.get("version")) is int
            and peer["version"] == INPUT_VERSION
            and self.peer_info.get("protocol") == PROTOCOL
        )
        if compatible != self.input_peer:
            self.input_peer = compatible
            self.input_budget.restart() if compatible else self.input_budget.suspend()
        if not compatible:
            # An incoming authenticated stream has its own capability handshake;
            # the ready reply may not yet have arrived on this endpoint.
            if not service._available():
                self.input_error = "Activa entrada por red en ambos equipos con la misma versión"
            return wait
        local_ready = service.backend.ready()
        if not local_ready or not peer.get("available", True):
            service.suspend()
            self.input_last_ready = False
            self.input_budget.suspend()
            where = "este equipo" if not local_ready else "el otro equipo"
            self.input_error = f"Entrada suspendida en {where}: revisa sesión y permisos"
            return wait
        if self.input_last_ready is False:
            # Reuse the existing online presence check to notice an unlocked
            # session. No extra offline timer or repeated hook registration.
            self.input_budget.restart()
        self.input_last_ready = True
        if service._available():
            self.input_error = None
            if self.input_seen_channel is not service.channel:
                self.input_online_since = time.monotonic()
            self.input_seen_channel = service.channel
            self.input_budget.suspend()
            return wait
        if service.channel is not None and not service.channel.finished.is_set():
            return min(wait, 0.05) if wait is not None else 0.05
        if service.channel is not None and service.channel is self.input_seen_channel:
            self.input_seen_channel = None
            # A local display reconfiguration is new availability, not a
            # failing network. Allow a fresh finite window, with the normal
            # first delay so a burst of OS display changes can settle.
            if service.channel.failure == "display" or time.monotonic() - self.input_online_since >= 30:
                self.input_budget.restart()
            self.input_budget.failed()
        # Failed releases survive a DHCP rebind. Do not discard their ownership
        # ledger or admit a new input stream while any old release is unresolved.
        self.input_recovery = [old for old in self.input_recovery if not old.retry_releases()]
        if self.input_recovery:
            self.input_error = "Entrada pendiente de liberar; usa los controles físicos"
            return wait
        if service.role == "windows" and self.input_budget.due():
            try:
                if service.connect(peer.get("capabilities")):
                    self.input_seen_channel = service.channel
                    self.input_online_since = time.monotonic()
                    self.input_budget.suspend()
                    self.input_error = None
            except Exception:
                self.input_budget.failed()
                self.input_error = "Entrada por red no disponible; revisa sesión y permisos"
        elif service.role == "mac":
            self.input_budget.suspend()  # Windows is the sole stream initiator.
        retry = self.input_budget.wait()
        return min(wait, retry) if wait is not None and retry is not None else wait if retry is None else retry

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
        if self.input_enabled:
            service = self.input_service
            if service and service._available():
                phase = service.session.phase
                if phase == "local" and service.session.failure in ("timeout", "capture", "peer", "release", "locks"):
                    return {
                        "timeout": "El otro equipo no confirmó el cruce a tiempo",
                        "capture": "No se pudo preparar la entrada local; revisa la sesión y permisos",
                        "peer": "El otro equipo no está preparado para recibir la entrada",
                        "release": "Restauración de entrada pendiente; usa los controles físicos",
                        "locks": "Restauración del teclado pendiente; usa los controles físicos",
                    }[service.session.failure]
                return {
                    "local": "Entrada por red lista · cruza el borde hacia el otro equipo",
                    "requesting": "Preparando entrada en el otro equipo…",
                    "sending": "Ratón y teclado controlando el otro equipo por red",
                    "receiving": "Recibiendo ratón y teclado por red",
                }[phase]
            return self.input_error or "Entrada por red · esperando conexión del compañero"
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

    def toggle_input(self):
        config = self.pairing.load()
        if not config or not config.get("peer"):
            raise ValueError("Vincula los equipos primero")
        if self._safety_pending():
            raise ValueError("Espera a que termine el traslado actual")
        self.input_requested = not self.input_enabled
        self.wake("input_options")

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
            "network_input": self.input_service.diagnostic() if self.input_service else {"enabled": self.input_enabled},
            "peer_reachable": self.peer_ready,
            "peer": self.peer_info,
            "devices": devices,
            "status": self.status(),
            "last_failure": coordinator.last_error if coordinator else None,
            "phase": coordinator.phase if coordinator else None,
            "transfer_elapsed_ms": coordinator.timings if coordinator else {},
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
        if self.input_enabled:
            if self.input_service and self.input_service.session:
                self.input_service.session.cancel("manual")
                self.wake("input")
                return True
            return False
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
