"""Pointer position only. No character keys, clicks or clipboard are captured."""

import dataclasses
import logging
import sys
import time

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Sample:
    x: float
    y: float
    left: float
    top: float
    width: float
    height: float
    safe: bool = True


class Edge:
    def __init__(self, side, dwell=0.4, clock=time.monotonic):
        if side not in ("left", "right"):
            raise ValueError("Borde inválido")
        self.side, self.dwell, self.clock = side, dwell, clock
        self.since = None
        self.blocked_until = 0
        self.latched = False

    def cooldown(self, seconds=2):
        self.blocked_until = self.clock() + seconds
        self.since = None
        self.latched = True

    def poll(self, sample):
        now = self.clock()
        in_screen = (
            sample.left <= sample.x < sample.left + sample.width and sample.top <= sample.y < sample.top + sample.height
        )
        at_edge = sample.x <= sample.left + 1 if self.side == "left" else sample.x >= sample.left + sample.width - 2
        corners = not sample.top + 40 <= sample.y < sample.top + sample.height - 40
        if self.latched:
            # Arrival parks the pointer 24 px inside the screen. It must travel
            # farther inward before another crossing, even after cooldown ends.
            inward = sample.x - sample.left if self.side == "left" else sample.left + sample.width - 1 - sample.x
            if in_screen and sample.safe and inward >= 80:
                self.latched = False
                log.info("Edge rearmed after inward movement side=%s", self.side)
            self.since = None
            return None
        if not at_edge or not in_screen:
            self.since = None
            return None
        if not sample.safe or corners or now < self.blocked_until or self.latched:
            self.since = None
            return None
        if self.since is None:
            self.since = now
        if now - self.since >= self.dwell:
            self.latched = True
            log.info("Edge triggered side=%s", self.side)
            return max(0.0, min(1.0, (sample.y - sample.top) / sample.height))
        return None


class WindowsCursor:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.api.OpenInputDesktop.restype = wintypes.HANDLE
        self.api.CloseDesktop.argtypes = (wintypes.HANDLE,)
        self.api.GetUserObjectInformationW.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        self.api.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
        self.api.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self.api.SetThreadDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        self.api.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p

    def session_ready(self):
        desktop = self.api.OpenInputDesktop(0, False, 1)
        if not desktop:
            return False
        try:
            name = self.ctypes.create_unicode_buffer(128)
            size = self.wintypes.DWORD()
            return bool(
                self.api.GetUserObjectInformationW(desktop, 2, name, self.ctypes.sizeof(name), self.ctypes.byref(size))
                and name.value.casefold() == "default"
            )
        finally:
            self.api.CloseDesktop(desktop)

    def sample(self):
        # The TLS arrival worker and edge worker are separate native threads.
        self.api.SetThreadDpiAwarenessContext(self.ctypes.c_void_p(-4))
        point = self.wintypes.POINT()
        if not self.api.GetCursorPos(self.ctypes.byref(point)):
            raise OSError("No se puede leer la posición del cursor")
        # Mouse buttons and modifiers only; never letters or content being typed.
        held = any(self.api.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4, 5, 6, 16, 17, 18, 91, 92))
        return Sample(
            point.x,
            point.y,
            0,
            0,
            self.api.GetSystemMetrics(0),
            self.api.GetSystemMetrics(1),
            self.session_ready() and not held,
        )

    def enter(self, side, y):
        sample = self.sample()
        if not sample.safe:
            raise OSError("La sesión no permite colocar el cursor")
        x = 24 if side == "left" else sample.width - 25
        if not self.api.SetCursorPos(int(x), int(max(40, min(sample.height - 41, y * sample.height)))):
            raise OSError("No se pudo colocar el cursor")


class MacCursor:
    def __init__(self):
        import ApplicationServices
        import Quartz

        self.api = Quartz
        self.accessibility = ApplicationServices

    def session_ready(self):
        info = self.api.CGSessionCopyCurrentDictionary()
        from AppKit import NSWorkspace

        foreground = NSWorkspace.sharedWorkspace().frontmostApplication()
        return bool(
            info
            and info.get(self.api.kCGSessionOnConsoleKey, False)
            and info.get(self.api.kCGSessionLoginDoneKey, False)
            and not info.get("CGSSessionScreenIsLocked", False)
            and foreground
            and foreground.bundleIdentifier() != "com.apple.loginwindow"
            and self.accessibility.AXIsProcessTrusted()
        )

    def sample(self):
        q = self.api
        event = q.CGEventCreate(None)
        if event is None:
            raise OSError("Concede Accesibilidad a CleverSwitch")
        point = q.CGEventGetLocation(event)
        rect = q.CGDisplayBounds(q.CGMainDisplayID())
        flags = q.CGEventSourceFlagsState(q.kCGEventSourceStateCombinedSessionState)
        mask = (
            q.kCGEventFlagMaskShift
            | q.kCGEventFlagMaskControl
            | q.kCGEventFlagMaskAlternate
            | q.kCGEventFlagMaskCommand
        )
        held = any(q.CGEventSourceButtonState(q.kCGEventSourceStateCombinedSessionState, b) for b in range(5))
        return Sample(
            point.x,
            point.y,
            rect.origin.x,
            rect.origin.y,
            rect.size.width,
            rect.size.height,
            self.session_ready() and not held and not (flags & mask),
        )

    def enter(self, side, y):
        sample = self.sample()
        if not sample.safe:
            raise OSError("La sesión no permite colocar el cursor")
        x = sample.left + (24 if side == "left" else sample.width - 25)
        result = self.api.CGWarpMouseCursorPosition(
            (x, sample.top + max(40, min(sample.height - 41, y * sample.height)))
        )
        if result != 0:
            raise OSError("No se pudo colocar el cursor")


def native_cursor():
    if sys.platform == "win32":
        return WindowsCursor()
    if sys.platform == "darwin":
        return MacCursor()
    raise RuntimeError("Plataforma no compatible")
