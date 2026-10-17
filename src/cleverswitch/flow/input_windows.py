"""Public user-session Windows input APIs; no drivers, services or elevation.

Nothing is registered until start(). Tests inject an API object and never call
the actual user32 hooks or SendInput. Injected events keep their OS-visible tag.
"""

import ctypes as c
import threading
import time

from .input_dispatch import Dispatch
from .input_geometry import Desktop, Display
from .input_keys import EVENT_TAG, WINDOWS, WINDOWS_REVERSE
from .input_protocol import Kind

DWORD, LONG, WORD, PTR = c.c_uint32, c.c_int32, c.c_uint16, c.c_size_t


class Point(c.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class Rect(c.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class MouseHook(c.Structure):
    _fields_ = [("point", Point), ("data", DWORD), ("flags", DWORD), ("time", DWORD), ("extra", PTR)]


class KeyHook(c.Structure):
    _fields_ = [("vk", DWORD), ("scan", DWORD), ("flags", DWORD), ("time", DWORD), ("extra", PTR)]


class MouseInput(c.Structure):
    _fields_ = [("x", LONG), ("y", LONG), ("data", DWORD), ("flags", DWORD), ("time", DWORD), ("extra", PTR)]


class KeyInput(c.Structure):
    _fields_ = [("vk", WORD), ("scan", WORD), ("flags", DWORD), ("time", DWORD), ("extra", PTR)]


class InputValue(c.Union):
    _fields_ = [("mouse", MouseInput), ("key", KeyInput)]


class Input(c.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("kind", DWORD), ("value", InputValue)]


class Message(c.Structure):
    _fields_ = [
        ("window", c.c_void_p),
        ("message", DWORD),
        ("wparam", PTR),
        ("lparam", c.c_ssize_t),
        ("time", DWORD),
        ("point", Point),
        ("private", DWORD),
    ]


class WindowsInput:
    def __init__(self, api=None, kernel=None, ready=None, desktop=None, clock=time.monotonic):
        if api is None:
            api = c.WinDLL("user32", use_last_error=True)
            kernel = c.WinDLL("kernel32", use_last_error=True)
            from .cursor import WindowsCursor

            ready = WindowsCursor().session_ready
        self.api, self.kernel, self._ready = api, kernel, ready
        self.clock, self.ready_until, self.ready_value = clock, 0, False
        self._desktop = desktop
        self.sink = None
        self.worker = None
        self.thread_id = None
        self.started = threading.Event()
        self.stopping = threading.Event()
        self.failed = False
        self.captured = False
        self.hidden = False
        self.restore_pending = None
        self.anchor = self.saved = self.previous = None
        self.held_keys, self.held_buttons = set(), set()
        self.wheel_remainder = [0.0, 0.0]
        self.dispatch = Dispatch(self._wake_control)
        callback = getattr(c, "WINFUNCTYPE", c.CFUNCTYPE)
        self.hook_type = callback(c.c_ssize_t, c.c_int, PTR, c.c_ssize_t)
        self.monitor_type = callback(c.c_int, c.c_void_p, c.c_void_p, c.POINTER(Rect), c.c_ssize_t)
        self._mouse_hook = self.hook_type(self._mouse)
        self._key_hook = self.hook_type(self._key)
        self._configure()

    def _configure(self):
        signatures = {
            "SetWindowsHookExW": (c.c_void_p, [c.c_int, self.hook_type, c.c_void_p, DWORD]),
            "CallNextHookEx": (c.c_ssize_t, [c.c_void_p, c.c_int, PTR, c.c_ssize_t]),
            "UnhookWindowsHookEx": (c.c_int, [c.c_void_p]),
            "SendInput": (c.c_uint, [c.c_uint, c.POINTER(Input), c.c_int]),
            "GetCursorPos": (c.c_int, [c.POINTER(Point)]),
            "GetAsyncKeyState": (c.c_int16, [c.c_int]),
            "GetKeyState": (c.c_int16, [c.c_int]),
            "MapVirtualKeyW": (c.c_uint, [c.c_uint, c.c_uint]),
            "ShowCursor": (c.c_int, [c.c_int]),
            "GetMessageW": (c.c_int, [c.POINTER(Message), c.c_void_p, c.c_uint, c.c_uint]),
            "PeekMessageW": (c.c_int, [c.POINTER(Message), c.c_void_p, c.c_uint, c.c_uint, c.c_uint]),
            "PostThreadMessageW": (c.c_int, [DWORD, c.c_uint, PTR, c.c_ssize_t]),
            "TranslateMessage": (c.c_int, [c.POINTER(Message)]),
            "DispatchMessageW": (c.c_ssize_t, [c.POINTER(Message)]),
            "GetSystemMetrics": (c.c_int, [c.c_int]),
            "SetThreadDpiAwarenessContext": (c.c_void_p, [c.c_void_p]),
            "EnumDisplayMonitors": (c.c_int, [c.c_void_p, c.c_void_p, self.monitor_type, c.c_ssize_t]),
        }
        for name, (restype, argtypes) in signatures.items():
            method = getattr(self.api, name)
            method.restype, method.argtypes = restype, argtypes
        self.kernel.GetCurrentThreadId.restype = DWORD
        self.kernel.GetModuleHandleW.restype = c.c_void_p
        self.kernel.GetModuleHandleW.argtypes = [c.c_wchar_p]

    @property
    def desktop(self):
        if self._desktop is not None:
            return self._desktop
        prior = self.api.SetThreadDpiAwarenessContext(c.c_void_p(-4))
        displays = []

        def found(monitor, dc, rect, data):
            r = rect.contents
            displays.append(Display(r.left, r.top, r.right - r.left, r.bottom - r.top))
            return 1

        try:
            if not self.api.EnumDisplayMonitors(None, None, self.monitor_type(found), 0):
                raise OSError("No se pudieron consultar las pantallas")
            return Desktop(displays)
        finally:
            if prior:
                self.api.SetThreadDpiAwarenessContext(prior)

    def ready(self):
        now = self.clock()
        if now >= self.ready_until:
            self.ready_value = bool(self._ready())
            self.ready_until = now + 0.1
        return self.ready_value

    def lock_state(self):
        def read():
            return sum(bit for bit, vk in ((1, 0x14), (2, 0x90), (4, 0x91)) if self.api.GetKeyState(vk) & 1), 7

        if self.worker is not None and self.worker.is_alive():
            return self.dispatch.call(read)
        return read()

    def lock_keys(self, state, mask):
        current, supported = self.lock_state()
        difference = (state ^ current) & mask & supported
        return tuple(code for code, bit in ((57, 1), (83, 2), (71, 4)) if difference & bit)

    def start(self, sink):
        if self.worker is not None and self.worker.is_alive():
            if self.stopping.is_set() or self.failed:
                raise RuntimeError("El capturador anterior todavía está terminando")
            return
        if not self.ready():
            raise PermissionError("Sesión de entrada no disponible")
        self.sink = sink
        self.failed = False
        self.started.clear()
        self.stopping.clear()
        self.dispatch = Dispatch(self._wake_control)
        self.worker = threading.Thread(target=self._run, name="flow-input-win32", daemon=True)
        self.worker.start()
        if not self.started.wait(2) or self.failed or not self.worker.is_alive():
            self.close()
            raise PermissionError("No se pudo iniciar la entrada de Windows")

    def _run(self):
        hooks = []
        try:
            if self.stopping.is_set():
                return
            self.api.SetThreadDpiAwarenessContext(c.c_void_p(-4))
            self.thread_id = self.kernel.GetCurrentThreadId()
            self.dispatch.owner = threading.get_ident()
            message = Message()
            self.api.PeekMessageW(c.byref(message), None, 0, 0, 0)
            module = self.kernel.GetModuleHandleW(None)
            for kind, callback in ((14, self._mouse_hook), (13, self._key_hook)):
                hook = self.api.SetWindowsHookExW(kind, callback, module, 0)
                if not hook:
                    raise OSError
                hooks.append(hook)
            self.held_keys = set()
            for usage, scan in WINDOWS.items():
                vk = self.api.MapVirtualKeyW((0xE000 if scan & 0x100 else 0) | (scan & 0xFF), 3)
                if vk and self.api.GetAsyncKeyState(vk) & 0x8000:
                    self.held_keys.add(usage)
            self.held_buttons = {i for i, vk in enumerate((1, 2, 4, 5, 6)) if self.api.GetAsyncKeyState(vk) & 0x8000}
            self.started.set()
            while not self.stopping.is_set():
                result = self.api.GetMessageW(c.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result < 0:
                    raise OSError
                if message.message == 0x8011:
                    self.dispatch.drain()
                    continue
                self.api.TranslateMessage(c.byref(message))
                self.api.DispatchMessageW(c.byref(message))
        except Exception:
            self.failed = True
            if self.sink:
                self.sink.fail()
        finally:
            self.started.set()
            for hook in hooks:
                self.api.UnhookWindowsHookEx(hook)
            try:
                self._capture(False)
            finally:
                self.dispatch.close()
                self.thread_id = None

    def _wake_control(self):
        if self.thread_id is None or not self.api.PostThreadMessageW(self.thread_id, 0x8011, 0, 0):
            raise ConnectionError("Trabajador de entrada no disponible")

    def _next(self, code, message, raw):
        return self.api.CallNextHookEx(None, code, message, raw)

    def _key(self, code, message, raw):
        if code < 0 or self.sink is None:
            return self._next(code, message, raw)
        event = c.cast(raw, c.POINTER(KeyHook)).contents
        if event.extra == EVENT_TAG or event.flags & 0x10:
            return self._next(code, message, raw)
        try:
            usage = 72 if event.vk == 0x13 else WINDOWS_REVERSE.get(event.scan | (0x100 if event.flags & 1 else 0))
            if usage is None:
                if self.captured:
                    self.sink.fail()
                    return 1
                return self._next(code, message, raw)
            down = 0 if event.flags & 0x80 else 1
            self.held_keys.add(usage) if down else self.held_keys.discard(usage)
            if self.sink.key(usage, down):
                return 1
        except Exception:
            self.sink.fail()
            return 1 if self.captured else self._next(code, message, raw)
        return self._next(code, message, raw)

    def _mouse(self, code, message, raw):
        if code < 0 or self.sink is None:
            return self._next(code, message, raw)
        event = c.cast(raw, c.POINTER(MouseHook)).contents
        if event.extra == EVENT_TAG or event.flags & 1:
            return self._next(code, message, raw)
        try:
            swallowed = False
            if message == 0x200:
                point = event.point.x, event.point.y
                previous = self.anchor if self.captured else self.previous or point
                self.previous = point
                swallowed = self.sink.motion(*point, point[0] - previous[0], point[1] - previous[1])
            elif message in (0x20A, 0x20E):
                delta = c.c_int16(event.data >> 16).value / 120
                swallowed = self.sink.wheel(delta if message == 0x20E else 0, delta if message == 0x20A else 0)
            else:
                buttons = {0x201: (0, 1), 0x202: (0, 0), 0x204: (1, 1), 0x205: (1, 0), 0x207: (2, 1), 0x208: (2, 0)}
                if message in (0x20B, 0x20C):
                    button = (event.data >> 16) + 2
                    buttons[message] = button, int(message == 0x20B)
                if message in buttons:
                    button, down = buttons[message]
                    self.held_buttons.add(button) if down else self.held_buttons.discard(button)
                    swallowed = self.sink.button(button, down)
            if swallowed:
                return 1
        except Exception:
            self.sink.fail()
            return 1 if self.captured else self._next(code, message, raw)
        return self._next(code, message, raw)

    def _submit(self, event):
        # UIPI failures are terminal for this focus. Never retry in a busy loop.
        if self.api.SendInput(1, c.byref(event), c.sizeof(Input)) != 1:
            raise PermissionError("Windows rechazó la entrada")

    def _mouse_input(self, flags, data=0, x=0, y=0):
        event = Input(kind=0)
        event.mouse = MouseInput(x, y, data & 0xFFFFFFFF, flags, 0, EVENT_TAG)
        self._submit(event)

    def restore(self, point):
        prior = self.api.SetThreadDpiAwarenessContext(c.c_void_p(-4))
        try:
            left, top, width, height = (self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79))
            if width < 2 or height < 2:
                raise OSError("Escritorio no disponible")
            x = round((point[0] - left) * 65535 / (width - 1))
            y = round((point[1] - top) * 65535 / (height - 1))
            self._mouse_input(0x8000 | 0x4000 | 1, x=max(0, min(65535, x)), y=max(0, min(65535, y)))
        finally:
            if prior:
                self.api.SetThreadDpiAwarenessContext(prior)

    def capture(self, enabled):
        # ShowCursor uses the calling thread's counter. Hide and show must run
        # on the same thread, including a disconnect from the TLS worker.
        # Even an apparently idle release must join this queue: a timed-out
        # capture may still be inside a native API before it sets its flags.
        if self.worker is not None and self.worker.is_alive():
            return self.dispatch.call(
                lambda: self._capture(enabled), rollback=(lambda: self._capture(False)) if enabled else None
            )
        if enabled:
            raise ConnectionError("Capturador de entrada no iniciado")
        self._capture(False)

    def _capture(self, enabled):
        if bool(enabled) == self.captured and not (not enabled and (self.hidden or self.restore_pending is not None)):
            return
        if enabled:
            point = Point()
            if not self.api.GetCursorPos(c.byref(point)):
                raise OSError("Cursor no disponible")
            self.saved = point.x, point.y
            display = min(
                self.desktop.displays,
                key=lambda d: sum((a - b) ** 2 for a, b in zip(d.clamp(*self.saved), self.saved, strict=True)),
            )
            self.anchor = display.x + display.width / 2, display.y + display.height / 2
            self.restore_pending = self.saved
            self.restore(self.anchor)
            self.api.ShowCursor(False)
            self.hidden = True
            self.captured = True
        else:
            self.captured = False
            try:
                if self.restore_pending is not None:
                    self.restore(self.restore_pending)
                    self.restore_pending = None
            finally:
                if self.hidden:
                    self.api.ShowCursor(True)
                    self.hidden = False
                self.previous = None

    def inject(self, packet):
        if packet.kind == Kind.MOVE:
            self.restore(packet.values)
        elif packet.kind == Kind.KEY:
            usage, down = packet.values
            if not down and usage in self.held_keys:
                return  # A physical local key still owns its down state.
            native = WINDOWS.get(usage)
            if native is None and usage != 72:
                raise ValueError("Tecla no compatible")
            event = Input(kind=1)
            event.key = KeyInput(
                0x13 if usage == 72 else 0,
                0 if usage == 72 else native & 0xFF,
                (0 if usage == 72 else 8 | (1 if native & 0x100 else 0)) | (0 if down else 2),
                0,
                EVENT_TAG,
            )
            self._submit(event)
        elif packet.kind == Kind.BUTTON:
            button, down = packet.values
            if not down and button in self.held_buttons:
                return
            flags = ((2, 4), (8, 16), (32, 64), (128, 256), (128, 256))[button][0 if down else 1]
            self._mouse_input(flags, button - 2 if button >= 3 else 0)
        elif packet.kind == Kind.WHEEL:
            for axis, value in enumerate(packet.values):
                self.wheel_remainder[axis] += value * 120
                delta = int(self.wheel_remainder[axis])
                if delta:
                    self._mouse_input(0x1000 if axis == 0 else 0x800, delta)
                    self.wheel_remainder[axis] -= delta

    def close(self):
        self.stopping.set()
        if self.thread_id is not None:
            self.api.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)
            if self.worker.is_alive():
                raise RuntimeError("El capturador de Windows no ha terminado")
        self.held_keys.clear()
        self.held_buttons.clear()
        self.capture(False)
