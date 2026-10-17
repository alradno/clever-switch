"""Display-change notifications only; never change modes or inspect user input."""

import ctypes as c
import threading


class MacDisplayMonitor:
    def __init__(self, changed, api=None):
        if api is None:
            import Quartz

            api = Quartz
        self.api, self.changed = api, changed
        self.active = self.registered = False
        # Keep the same callable alive until Quartz confirms its removal.
        self.callback = self._event

    def _event(self, display, flags, context):
        if self.active:
            self.changed()

    def start(self):
        if self.registered:
            raise RuntimeError("El observador de pantallas ya está registrado")
        self.active = True
        try:
            if self.api.CGDisplayRegisterReconfigurationCallback(self.callback, None) != 0:
                raise OSError("No se pudieron observar las pantallas")
        except Exception:
            self.active = False
            raise
        self.registered = True

    def close(self):
        self.active = False
        if self.registered:
            if self.api.CGDisplayRemoveReconfigurationCallback(self.callback, None) != 0:
                raise OSError("No se pudo cerrar el observador de pantallas")
            self.registered = False


CALLBACK = getattr(c, "WINFUNCTYPE", c.CFUNCTYPE)(c.c_ssize_t, c.c_void_p, c.c_uint32, c.c_size_t, c.c_ssize_t)


class WindowClass(c.Structure):
    _fields_ = [
        ("style", c.c_uint32),
        ("procedure", CALLBACK),
        ("class_extra", c.c_int32),
        ("window_extra", c.c_int32),
        ("instance", c.c_void_p),
        ("icon", c.c_void_p),
        ("cursor", c.c_void_p),
        ("background", c.c_void_p),
        ("menu", c.c_wchar_p),
        ("name", c.c_wchar_p),
    ]


class WindowsDisplayMonitor:
    def __init__(self, changed, api=None, kernel=None):
        self.changed = changed
        self.api = api if api is not None else c.WinDLL("user32", use_last_error=True)
        self.kernel = kernel if kernel is not None else c.WinDLL("kernel32", use_last_error=True)
        self.callback = CALLBACK(self._event)
        self.name = f"CleverSwitchDisplays-{id(self)}"
        self.window = self.instance = self.owner = None
        self.atom = 0
        self.active = False
        signatures = {
            "RegisterClassW": (c.c_uint16, [c.POINTER(WindowClass)]),
            "UnregisterClassW": (c.c_int, [c.c_wchar_p, c.c_void_p]),
            "CreateWindowExW": (
                c.c_void_p,
                [
                    c.c_uint32,
                    c.c_wchar_p,
                    c.c_wchar_p,
                    c.c_uint32,
                    c.c_int,
                    c.c_int,
                    c.c_int,
                    c.c_int,
                    c.c_void_p,
                    c.c_void_p,
                    c.c_void_p,
                    c.c_void_p,
                ],
            ),
            "DestroyWindow": (c.c_int, [c.c_void_p]),
            "DefWindowProcW": (c.c_ssize_t, [c.c_void_p, c.c_uint32, c.c_size_t, c.c_ssize_t]),
        }
        for name, (result, arguments) in signatures.items():
            getattr(self.api, name).restype, getattr(self.api, name).argtypes = result, arguments
        self.kernel.GetModuleHandleW.argtypes = [c.c_wchar_p]
        self.kernel.GetModuleHandleW.restype = c.c_void_p

    def _event(self, window, message, wparam, lparam):
        # A hidden top-level window receives broadcast display changes. A
        # message-only window does not. DPI changes also invalidate the mapping.
        if self.active and message in (0x007E, 0x02E0):
            self.changed()
        return self.api.DefWindowProcW(window, message, wparam, lparam)

    def start(self):
        if self.window or self.atom:
            raise RuntimeError("El observador de pantallas ya está registrado")
        self.owner = threading.get_ident()
        self.instance = self.kernel.GetModuleHandleW(None)
        info = WindowClass(procedure=self.callback, instance=self.instance, name=self.name)
        self.atom = self.api.RegisterClassW(c.byref(info))
        if not self.atom:
            raise OSError("No se pudieron observar las pantallas")
        # No WS_VISIBLE, activation, input hook or separate message pump. The
        # existing input owner's GetMessage/DispatchMessage loop services it.
        self.active = True
        try:
            self.window = self.api.CreateWindowExW(
                0, self.name, "", 0x80000000, 0, 0, 0, 0, None, None, self.instance, None
            )
            if not self.window:
                raise OSError("No se pudo preparar el observador de pantallas")
        except Exception:
            self.active = False
            raise

    def close(self):
        self.active = False
        if (self.window or self.atom) and self.owner != threading.get_ident():
            raise RuntimeError("El observador de pantallas debe cerrarse en su hilo")
        if self.window:
            if not self.api.DestroyWindow(self.window):
                raise OSError("No se pudo cerrar el observador de pantallas")
            self.window = None
        if self.atom:
            if not self.api.UnregisterClassW(self.name, self.instance):
                raise OSError("No se pudo liberar el observador de pantallas")
            self.atom = 0
