"""Public Quartz input APIs, only started after explicit network-input opt-in.

This module does not alter TCC, signing, system extensions or security software.
Construction does not install an event tap. Tests inject Quartz and readiness
doubles; native capture must be validated separately in an approved environment.
"""

import threading
import time

from .input_geometry import Desktop, Display
from .input_health import Readiness
from .input_keys import EVENT_TAG, MAC, MAC_DEVICE_FLAGS, MAC_MODIFIER_FLAGS, MAC_REVERSE
from .input_protocol import Kind


class MacInput:
    def __init__(
        self,
        api=None,
        ready=None,
        secure_input=None,
        desktop=None,
        clock=time.monotonic,
        double_click=None,
        monitor_factory=None,
        display_monitor_factory=None,
    ):
        if api is None:
            import ctypes

            import Quartz
            from AppKit import NSEvent

            from .cursor import MacCursor
            from .input_devices import MacInputMonitor
            from .input_displays import MacDisplayMonitor

            api = Quartz
            ready = MacCursor().session_ready
            carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
            carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
            secure_input = carbon.IsSecureEventInputEnabled
            double_click = NSEvent.doubleClickInterval
            monitor_factory = MacInputMonitor
            display_monitor_factory = MacDisplayMonitor
        self.q, self._ready, self.secure_input = api, ready, secure_input
        self.clock = clock
        self.health = Readiness(self._check_ready, lambda: self.clock())
        self.double_click = double_click or (lambda: 0.5)
        self._desktop = desktop
        self.monitor_factory = monitor_factory
        self.device_monitor = None
        self.display_monitor_factory = display_monitor_factory
        self.display_monitor = None
        self.display_invalidated = False
        self.sink = self.worker = self.tap = self.runloop = None
        self.started = threading.Event()
        self.stopping = threading.Event()
        self.failed = False
        self.captured = False
        self.hidden = False
        self.restore_pending = None
        self.anchor = self.saved = self.previous = None
        self.held_keys, self.held_buttons, self.injected_keys, self.injected_buttons = set(), set(), set(), set()
        self.caps = False
        self.last_click = None
        self.injected_point = None
        self.physical_position = None
        self.injected_caps = None

    @property
    def desktop(self):
        if self._desktop is not None:
            return self._desktop
        error, identifiers, count = self.q.CGGetActiveDisplayList(16, None, None)
        if error or not count:
            raise OSError("Pantallas no disponibles")
        result = []
        for identifier in identifiers[:count]:
            rect = self.q.CGDisplayBounds(identifier)
            result.append(Display(rect.origin.x, rect.origin.y, rect.size.width, rect.size.height))
        return Desktop(result)

    @property
    def display_events(self):
        return self.display_monitor is not None and self.display_monitor.active

    def display_changed(self, observer):
        if observer is self.display_monitor and not self.stopping.is_set():
            self.display_invalidated = True
            self.sink.fail("display")

    def ready(self):
        if self.display_invalidated:
            return False
        if self.secure_input():
            return False
        return self.health.ready()

    def _check_ready(self):
        return self._ready() and self.q.CGPreflightListenEventAccess() and self.q.CGPreflightPostEventAccess()

    def set_active(self, active):
        self.health.set_active(active)

    def lock_state(self):
        # Quartz posts are asynchronous. A new crossing must inherit the last
        # ordered toggle even before WindowServer updates its flag snapshot.
        # Physical Caps changes clear this value in the event callback.
        if self.injected_caps is not None:
            return int(self.injected_caps), 1
        flags = self.q.CGEventSourceFlagsState(self.q.kCGEventSourceStateCombinedSessionState)
        return int(bool(flags & self.q.kCGEventFlagMaskAlphaShift)), 1

    def lock_keys(self, state, mask):
        current, supported = self.lock_state()
        return (57,) if (state ^ current) & mask & supported else ()

    def start(self, sink):
        if self.worker is not None and self.worker.is_alive():
            if self.stopping.is_set() or self.failed:
                raise RuntimeError("El capturador anterior todavía está terminando")
            return
        if self.device_monitor is not None or self.display_monitor is not None:
            raise RuntimeError("Un observador anterior de entrada no ha terminado")
        if not self.ready():
            raise PermissionError("Entrada no disponible; comprueba los permisos del sistema")
        self.sink = sink
        self.failed = False
        self.started.clear()
        self.stopping.clear()
        self.health.start()
        self.worker = threading.Thread(target=self._run, name="flow-input-quartz", daemon=True)
        self.worker.start()
        if not self.started.wait(2) or self.failed or not self.worker.is_alive():
            self.close()
            raise PermissionError("No se pudo iniciar la entrada del Mac")

    def _run(self):
        q = self.q
        source = None
        monitor = display_monitor = None
        try:
            if self.stopping.is_set():
                return
            if self.display_monitor_factory is not None:
                display_monitor = self.display_monitor_factory(lambda: self.display_changed(display_monitor))
                self.display_monitor = display_monitor
                display_monitor.start()
            types = (
                q.kCGEventMouseMoved,
                q.kCGEventLeftMouseDragged,
                q.kCGEventRightMouseDragged,
                q.kCGEventOtherMouseDragged,
                q.kCGEventLeftMouseDown,
                q.kCGEventLeftMouseUp,
                q.kCGEventRightMouseDown,
                q.kCGEventRightMouseUp,
                q.kCGEventOtherMouseDown,
                q.kCGEventOtherMouseUp,
                q.kCGEventKeyDown,
                q.kCGEventKeyUp,
                q.kCGEventFlagsChanged,
                q.kCGEventScrollWheel,
            )
            mask = sum(1 << kind for kind in types)
            self.tap = q.CGEventTapCreate(
                q.kCGSessionEventTap,
                q.kCGHeadInsertEventTap,
                q.kCGEventTapOptionDefault,
                mask,
                self._callback,
                None,
            )
            if self.tap is None:
                raise PermissionError
            source = q.CFMachPortCreateRunLoopSource(None, self.tap, 0)
            if source is None:
                raise RuntimeError
            self.runloop = q.CFRunLoopGetCurrent()
            q.CFRunLoopAddSource(self.runloop, source, q.kCFRunLoopCommonModes)
            q.CGEventTapEnable(self.tap, True)
            state = q.kCGEventSourceStateCombinedSessionState
            self.held_keys = {usage for usage, native in MAC.items() if q.CGEventSourceKeyState(state, native)}
            self.held_buttons = {button for button in range(5) if q.CGEventSourceButtonState(state, button)}
            self.caps = bool(q.CGEventSourceFlagsState(state) & q.kCGEventFlagMaskAlphaShift)
            if self.monitor_factory is not None:
                monitor = self.monitor_factory(lambda kind="keyboard": self.device_changed(kind, monitor))
                self.device_monitor = monitor
                monitor.start()
            self.started.set()
            if not self.stopping.is_set():
                q.CFRunLoopRun()
        except Exception:
            self.failed = True
            if self.sink:
                self.sink.fail()
        finally:
            self.started.set()
            for observer, attribute in ((monitor, "device_monitor"), (display_monitor, "display_monitor")):
                if observer is not None:
                    try:
                        observer.close()
                        setattr(self, attribute, None)
                    except Exception:
                        # Retain failed native registrations, but still close the
                        # other observer and restore input on the owner thread.
                        self.failed = True
                        self.sink.fail()
            if self.tap is not None:
                q.CGEventTapEnable(self.tap, False)
                q.CFMachPortInvalidate(self.tap)
            if source is not None and self.runloop is not None:
                q.CFRunLoopRemoveSource(self.runloop, source, q.kCFRunLoopCommonModes)
            try:
                self.capture(False)
            except Exception:
                # Keep restore_pending for close()/the next availability
                # event when Quartz can report a valid desktop again.
                self.failed = True
                self.sink.fail("cleanup")
            finally:
                self.tap = self.runloop = None

    def _callback(self, proxy, kind, event, context):
        q = self.q
        if kind in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
            # Losing events while a key is down invalidates the focus. Restore
            # local control, rather than pretending the stream is still intact.
            self.sink.fail()
            return event
        if q.CGEventGetIntegerValueField(event, q.kCGEventSourceUserData) == EVENT_TAG:
            return event
        try:
            swallowed = self._physical(kind, event)
            return None if swallowed else event
        except Exception:
            self.sink.fail()
            return None if self.captured else event

    def _physical(self, kind, event):
        q = self.q
        if kind in (q.kCGEventKeyDown, q.kCGEventKeyUp, q.kCGEventFlagsChanged):
            native = q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)
            usage = MAC_REVERSE.get(native)
            if usage is None:
                if self.captured:
                    self.sink.fail()
                return self.captured
            if kind == q.kCGEventFlagsChanged:
                flags = q.CGEventGetFlags(event)
                if usage == 57:
                    self.injected_caps = None  # A physical toggle owns the new local state.
                    enabled = bool(flags & q.kCGEventFlagMaskAlphaShift)
                    if enabled == self.caps:
                        return self.captured
                    self.caps = enabled
                    swallowed = self.sink.key(usage, 1)
                    self.sink.key(usage, 0)
                    return swallowed
                if usage not in MAC_DEVICE_FLAGS:
                    return False
                down = int(bool(flags & MAC_DEVICE_FLAGS[usage]))
            else:
                down = int(kind == q.kCGEventKeyDown)
            self.held_keys.add(usage) if down else self.held_keys.discard(usage)
            return self.sink.key(usage, down)
        if kind == q.kCGEventScrollWheel:
            point = self._mouse_point(event)
            if not self.captured:
                # Native cursor placement need not generate a move. A local
                # wheel/click still provides the next motion's actual origin.
                self.previous = point
            # Quartz fixed-point deltas are exposed as doubles by this API.
            vertical = q.CGEventGetDoubleValueField(event, q.kCGScrollWheelEventFixedPtDeltaAxis1)
            horizontal = -q.CGEventGetDoubleValueField(event, q.kCGScrollWheelEventFixedPtDeltaAxis2)
            return self.sink.wheel(horizontal, vertical)
        if kind in (
            q.kCGEventMouseMoved,
            q.kCGEventLeftMouseDragged,
            q.kCGEventRightMouseDragged,
            q.kCGEventOtherMouseDragged,
        ):
            point = self._mouse_point(event)
            previous = self.anchor if self.captured else self.previous or point
            self.previous = point
            if self.captured and point == self.anchor:
                return False  # Allow our anchor warp without retransmitting it.
            return self.sink.motion(*point, point[0] - previous[0], point[1] - previous[1])
        buttons = {
            q.kCGEventLeftMouseDown: (0, 1),
            q.kCGEventLeftMouseUp: (0, 0),
            q.kCGEventRightMouseDown: (1, 1),
            q.kCGEventRightMouseUp: (1, 0),
        }
        if kind in (q.kCGEventOtherMouseDown, q.kCGEventOtherMouseUp):
            button = q.CGEventGetIntegerValueField(event, q.kCGMouseEventButtonNumber)
            buttons[kind] = button, int(kind == q.kCGEventOtherMouseDown)
        if kind in buttons:
            button, down = buttons[kind]
            point = self._mouse_point(event)
            if not self.captured:
                self.previous = point
            self.held_buttons.add(button) if down else self.held_buttons.discard(button)
            swallowed = self.sink.button(button, down)
            if down and not swallowed:
                # Quartz counted this local click. Keep it in the same history
                # as delivered remote clicks, without counting forwarded or
                # tagged events that never reached local applications.
                count = q.CGEventGetIntegerValueField(event, q.kCGMouseEventClickState)
                self.last_click = self.clock(), button, point, max(1, min(3, count))
            return swallowed
        return False

    def _mouse_point(self, event):
        location = self.q.CGEventGetLocation(event)
        point = location.x, location.y
        if not self.captured:
            # A local event can cancel remote dragging synchronously. Release
            # its buttons at this position, not at the last remote coordinate.
            self.injected_point = point
            self.physical_position = point
        return point

    def local_takeover(self):
        """Prepare releases and return any position to restore after them."""
        point = self.physical_position
        if point is not None:
            displaced = self.captured or self.injected_point != point
            self.injected_point = point
            self.previous = point
            if displaced:
                return point
        return None

    def _position(self):
        event = self.q.CGEventCreate(None)
        if event is None:
            raise PermissionError("Cursor no disponible")
        location = self.q.CGEventGetLocation(event)
        return location.x, location.y

    def restore(self, point):
        if self.q.CGWarpMouseCursorPosition(point) != 0:
            raise OSError("No se pudo colocar el cursor")
        self.previous = tuple(point)

    def capture(self, enabled):
        if bool(enabled) == self.captured and not (not enabled and (self.hidden or self.restore_pending is not None)):
            return
        if enabled:
            self.saved = self._position()
            display = min(
                self.desktop.displays,
                key=lambda d: sum((a - b) ** 2 for a, b in zip(d.clamp(*self.saved), self.saved, strict=True)),
            )
            self.anchor = display.x + display.width / 2, display.y + display.height / 2
            self.restore_pending = self.saved
            self.restore(self.anchor)
            if self.q.CGDisplayHideCursor(self.q.CGMainDisplayID()) != 0:
                self.restore(self.saved)
                self.restore_pending = None
                raise OSError("No se pudo preparar el cursor")
            self.hidden = True
            self.captured = True
        else:
            self.captured = False
            try:
                if self.restore_pending is not None:
                    point = self.restore_pending
                    if self.display_invalidated:
                        point = self.desktop.clamp(*point)
                    self.restore(point)
                    self.restore_pending = None
            finally:
                if self.hidden:
                    if self.q.CGDisplayShowCursor(self.q.CGMainDisplayID()) != 0:
                        raise OSError("El sistema no pudo restaurar el cursor")
                    self.hidden = False

    def _post(self, event):
        if event is None:
            raise PermissionError("No se pudo crear la entrada")
        q = self.q
        # Newly created events inherit flags from the current system state.
        # Clear only modifiers we own before rebuilding them; OR alone leaves
        # Shift/Ctrl/Alt/Cmd stuck on the key-up event.
        modifier_mask = 0
        for value in (*MAC_MODIFIER_FLAGS.values(), *MAC_DEVICE_FLAGS.values()):
            modifier_mask |= value
        flags = q.CGEventGetFlags(event) & ~modifier_mask
        if self.injected_caps is not None:
            flags &= ~q.kCGEventFlagMaskAlphaShift
            if self.injected_caps:
                flags |= q.kCGEventFlagMaskAlphaShift
        for usage in self.injected_keys | self.held_keys:
            flags |= MAC_MODIFIER_FLAGS.get(usage, 0) | MAC_DEVICE_FLAGS.get(usage, 0)
        q.CGEventSetFlags(event, flags)
        q.CGEventSetIntegerValueField(event, q.kCGEventSourceUserData, EVENT_TAG)
        q.CGEventPost(q.kCGHIDEventTap, event)

    def inject(self, packet):
        q = self.q
        if packet.kind == Kind.KEY:
            usage, down = packet.values
            if usage not in MAC:
                raise ValueError("Tecla no compatible")
            prior_caps = self.injected_caps, self.caps
            repeated = bool(down and usage in self.injected_keys)
            if usage == 57 and down and not repeated:
                current = self.injected_caps if self.injected_caps is not None else bool(self.lock_state()[0])
                self.injected_caps = not current
                self.caps = self.injected_caps
            self.injected_keys.add(usage) if down else self.injected_keys.discard(usage)
            if not down and usage in self.held_keys:
                return
            try:
                event = q.CGEventCreateKeyboardEvent(None, MAC[usage], bool(down))
                if event is not None:
                    q.CGEventSetIntegerValueField(event, q.kCGKeyboardEventAutorepeat, int(repeated))
                    if usage == 57:
                        q.CGEventSetType(event, q.kCGEventFlagsChanged)
                self._post(event)
            except Exception:
                if usage == 57:
                    self.injected_caps, self.caps = prior_caps
                raise
        elif packet.kind in (Kind.MOVE, Kind.BUTTON):
            if packet.kind == Kind.MOVE:
                point = packet.values
                button = min(self.injected_buttons) if self.injected_buttons else 0
                kind = q.kCGEventMouseMoved
                if self.injected_buttons:
                    kind = (q.kCGEventLeftMouseDragged, q.kCGEventRightMouseDragged, q.kCGEventOtherMouseDragged)[
                        min(button, 2)
                    ]
            else:
                point = self.injected_point if self.injected_point is not None else self._position()
                button, down = packet.values
                self.injected_buttons.add(button) if down else self.injected_buttons.discard(button)
                if not down and button in self.held_buttons:
                    return
                kinds = (
                    (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp),
                    (q.kCGEventRightMouseDown, q.kCGEventRightMouseUp),
                    (q.kCGEventOtherMouseDown, q.kCGEventOtherMouseUp),
                )
                kind = kinds[min(button, 2)][0 if down else 1]
            event = q.CGEventCreateMouseEvent(None, kind, point, button)
            if packet.kind == Kind.BUTTON and event is not None:
                count = 1
                now = self.clock()
                clicked = now if packet.stamp is None else packet.stamp / 1_000_000
                if packet.stamp is not None:
                    # Quartz and Python need not share a clock origin. Backdate
                    # a newly created event by its age, never post a future time.
                    age = int(max(0, now - clicked) * 1_000_000_000)
                    q.CGEventSetTimestamp(event, max(0, q.CGEventGetTimestamp(event) - age))
                if down:
                    if self.last_click is not None:
                        previous_time, previous_button, previous_point, previous_count = self.last_click
                        if (
                            0 <= clicked - previous_time <= self.double_click()
                            and previous_button == button
                            and abs(previous_point[0] - point[0]) <= 4
                            and abs(previous_point[1] - point[1]) <= 4
                        ):
                            count = min(3, previous_count + 1)
                    self.last_click = clicked, button, point, count
                elif self.last_click is not None and self.last_click[1] == button:
                    count = self.last_click[3]
                q.CGEventSetIntegerValueField(event, q.kCGMouseEventClickState, count)
            self._post(event)
            if packet.kind == Kind.MOVE:
                # Posting is asynchronous: a following click must use this
                # ordered position, not a stale WindowServer cursor snapshot.
                self.injected_point = point
                self.previous = tuple(point)
        elif packet.kind == Kind.WHEEL:
            horizontal, vertical = packet.values
            event = q.CGEventCreateScrollWheelEvent(
                None, q.kCGScrollEventUnitLine, 2, round(vertical), -round(horizontal)
            )
            if event is not None:
                # A posted move may still be waiting in WindowServer. Wheel
                # hit testing must use its ordered position, not the cursor
                # snapshot inherited while this event was being created.
                if self.injected_point is not None:
                    q.CGEventSetLocation(event, self.injected_point)
                q.CGEventSetDoubleValueField(event, q.kCGScrollWheelEventFixedPtDeltaAxis1, vertical)
                q.CGEventSetDoubleValueField(event, q.kCGScrollWheelEventFixedPtDeltaAxis2, -horizontal)
            self._post(event)

    def close(self):
        self.stopping.set()
        if self.runloop is not None:
            self.q.CFRunLoopStop(self.runloop)
            self.q.CFRunLoopWakeUp(self.runloop)
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)
            if self.worker.is_alive():
                raise RuntimeError("El capturador del Mac no ha terminado")
        try:
            self.capture(False)
        finally:
            self.health.close()
        self.held_keys.clear()
        self.held_buttons.clear()
        self.injected_point = None
        self.physical_position = None
        self.injected_caps = None
        self.last_click = None
        if self.device_monitor is None and self.display_monitor is None:
            self.display_invalidated = False

    def keyboard_changed(self):
        self.held_keys.clear()
        try:
            self.sink.keyboard_changed()
        except Exception:
            self.sink.fail()

    def device_changed(self, kind, observer):
        if observer is not self.device_monitor or self.stopping.is_set():
            return
        if kind == "keyboard":
            self.keyboard_changed()
        elif kind == "mouse":
            self.held_buttons.clear()
            try:
                self.sink.mouse_changed()
            except Exception:
                self.sink.fail()
