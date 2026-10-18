"""Keyboard/mouse arrival and removal, without polling or reading reports.

These observers live only as long as the explicitly enabled input backend.
They never change Bluetooth channels, pair devices or register input drivers.
"""

import ctypes as c
import uuid


class NotificationFilter(c.Structure):
    _fields_ = [
        ("size", c.c_uint32),
        ("flags", c.c_uint32),
        ("kind", c.c_uint32),
        ("reserved", c.c_uint32),
        ("payload", c.c_ubyte * 400),
    ]


class WindowsKeyboardMonitor:
    interface = "884b96c3-56ef-11d1-bc8c-00a0c91405dd"  # GUID_DEVINTERFACE_KEYBOARD

    def __init__(self, changed, api=None):
        self.changed = changed
        self.api = api if api is not None else c.WinDLL("cfgmgr32", use_last_error=True)
        self.handle = c.c_void_p()
        self.active = False
        callback = getattr(c, "WINFUNCTYPE", c.CFUNCTYPE)
        self.callback = callback(c.c_uint32, c.c_void_p, c.c_void_p, c.c_uint32, c.c_void_p, c.c_uint32)(self._event)
        self.api.CM_Register_Notification.argtypes = [
            c.POINTER(NotificationFilter),
            c.c_void_p,
            type(self.callback),
            c.POINTER(c.c_void_p),
        ]
        self.api.CM_Register_Notification.restype = c.c_uint32
        self.api.CM_Unregister_Notification.argtypes = [c.c_void_p]
        self.api.CM_Unregister_Notification.restype = c.c_uint32

    def _event(self, handle, context, action, data, size):
        if self.active and action in (0, 1):  # DEVICEINTERFACEARRIVAL / REMOVAL
            self.changed()
        return 0

    def start(self):
        info = NotificationFilter(size=c.sizeof(NotificationFilter), kind=0)
        # Observe only this input class, not all USB/HID interfaces.
        info.payload[:16] = uuid.UUID(self.interface).bytes_le
        self.active = True
        if self.api.CM_Register_Notification(c.byref(info), None, self.callback, c.byref(self.handle)) != 0:
            self.active = False
            raise OSError("No se pudo observar la reconexión del dispositivo")

    def close(self):
        self.active = False
        if self.handle.value:
            result = self.api.CM_Unregister_Notification(self.handle)
            if result != 0:
                raise OSError("No se pudo cerrar el observador del dispositivo")
            self.handle = c.c_void_p()


class MacKeyboardMonitor:
    usage = 6  # Generic Desktop / Keyboard

    def __init__(self, changed, io=None, cf=None, mode=None, dictionary_callbacks=None):
        self.changed = changed
        self.io = io if io is not None else c.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        self.cf = cf if cf is not None else c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.mode = mode if mode is not None else c.c_void_p.in_dll(self.cf, "kCFRunLoopCommonModes").value
        self.dictionary_callbacks = dictionary_callbacks or (
            c.addressof(c.c_byte.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks")),
            c.addressof(c.c_byte.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks")),
        )
        self.manager = self.loop = None
        self.devices = set()
        self.active = False
        callback = c.CFUNCTYPE(None, c.c_void_p, c.c_int32, c.c_void_p, c.c_void_p)
        self.arrived = callback(lambda context, result, sender, device: self._event(device, True))
        self.removed = callback(lambda context, result, sender, device: self._event(device, False))
        signatures = {
            "IOHIDManagerCreate": (c.c_void_p, [c.c_void_p, c.c_uint32]),
            "IOHIDManagerSetDeviceMatching": (None, [c.c_void_p, c.c_void_p]),
            "IOHIDManagerCopyDevices": (c.c_void_p, [c.c_void_p]),
            "IOHIDManagerRegisterDeviceMatchingCallback": (None, [c.c_void_p, callback, c.c_void_p]),
            "IOHIDManagerRegisterDeviceRemovalCallback": (None, [c.c_void_p, callback, c.c_void_p]),
            "IOHIDManagerScheduleWithRunLoop": (None, [c.c_void_p, c.c_void_p, c.c_void_p]),
            "IOHIDManagerUnscheduleFromRunLoop": (None, [c.c_void_p, c.c_void_p, c.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            getattr(self.io, name).restype, getattr(self.io, name).argtypes = result, arguments
        signatures = {
            "CFStringCreateWithCString": (c.c_void_p, [c.c_void_p, c.c_char_p, c.c_uint32]),
            "CFNumberCreate": (c.c_void_p, [c.c_void_p, c.c_int, c.c_void_p]),
            "CFDictionaryCreate": (c.c_void_p, [c.c_void_p, c.c_void_p, c.c_void_p, c.c_long, c.c_void_p, c.c_void_p]),
            "CFSetGetCount": (c.c_long, [c.c_void_p]),
            "CFSetGetValues": (None, [c.c_void_p, c.c_void_p]),
            "CFRunLoopGetCurrent": (c.c_void_p, []),
            "CFRelease": (None, [c.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            getattr(self.cf, name).restype, getattr(self.cf, name).argtypes = result, arguments

    def _event(self, device, arrived):
        if not self.active or bool(device in self.devices) == arrived:
            return
        self.devices.add(device) if arrived else self.devices.discard(device)
        self.changed()

    def start(self):
        temporary = []
        try:
            self.manager = self.io.IOHIDManagerCreate(None, 0)
            if not self.manager:
                raise OSError("No se pudo observar el dispositivo de entrada")
            keys, values = [], []
            for name, value in ((b"DeviceUsagePage", 1), (b"DeviceUsage", self.usage)):
                key = self.cf.CFStringCreateWithCString(None, name, 0x08000100)
                number = self.cf.CFNumberCreate(None, 9, c.byref(c.c_int32(value)))
                temporary.extend((key, number))
                keys.append(key)
                values.append(number)
            if not all(temporary):
                raise OSError("No se pudo preparar el observador de entrada")
            # The keys/values outlive this call and SetDeviceMatching retains
            # the dictionary. CFType callbacks make its ownership explicit.
            dictionary = self.cf.CFDictionaryCreate(
                None,
                (c.c_void_p * 2)(*keys),
                (c.c_void_p * 2)(*values),
                2,
                *self.dictionary_callbacks,
            )
            temporary.append(dictionary)
            if not dictionary:
                raise OSError("No se pudo preparar el filtro de entrada")
            self.io.IOHIDManagerSetDeviceMatching(self.manager, dictionary)
            existing = self.io.IOHIDManagerCopyDevices(self.manager)
            if existing:
                temporary.append(existing)
                count = self.cf.CFSetGetCount(existing)
                if not 0 <= count <= 256:
                    raise OSError("Demasiados dispositivos de entrada")
                devices = (c.c_void_p * count)()
                self.cf.CFSetGetValues(existing, devices)
                self.devices = set(devices)
            self.io.IOHIDManagerRegisterDeviceMatchingCallback(self.manager, self.arrived, None)
            self.io.IOHIDManagerRegisterDeviceRemovalCallback(self.manager, self.removed, None)
            self.loop = self.cf.CFRunLoopGetCurrent()
            self.active = True
            # Do not open the manager/devices or subscribe to input reports.
            self.io.IOHIDManagerScheduleWithRunLoop(self.manager, self.loop, self.mode)
        except Exception:
            self.close()
            raise
        finally:
            for value in reversed(temporary):
                if value:
                    self.cf.CFRelease(value)

    def close(self):
        self.active = False
        if self.manager:
            if self.loop:
                self.io.IOHIDManagerUnscheduleFromRunLoop(self.manager, self.loop, self.mode)
            self.cf.CFRelease(self.manager)
            self.manager = self.loop = None
        self.devices.clear()


class WindowsMouseMonitor(WindowsKeyboardMonitor):
    interface = "378de44c-56ef-11d1-bc8c-00a0c91405dd"  # GUID_DEVINTERFACE_MOUSE


class MacMouseMonitor(MacKeyboardMonitor):
    usage = 2  # Generic Desktop / Mouse


class InputDeviceMonitor:
    """Own both registrations and preserve device type without another thread."""

    def __init__(self, changed, keyboard, mouse):
        self.changed = changed
        self.active = self.registered = False
        self.monitors = (keyboard(lambda: self._event("keyboard")), mouse(lambda: self._event("mouse")))

    def _event(self, kind):
        if self.active:
            self.changed(kind)

    def start(self):
        if self.registered:
            raise RuntimeError("El observador de entrada anterior sigue registrado")
        self.active = self.registered = True
        try:
            for monitor in self.monitors:
                monitor.start()
        except Exception:
            self.close()
            raise

    def close(self):
        self.active = False
        error = None
        for monitor in self.monitors:
            try:
                monitor.close()
            except Exception as caught:
                error = caught
        if error is not None:
            raise error
        self.registered = False


class WindowsInputMonitor(InputDeviceMonitor):
    def __init__(self, changed):
        super().__init__(changed, WindowsKeyboardMonitor, WindowsMouseMonitor)


class MacInputMonitor(InputDeviceMonitor):
    def __init__(self, changed):
        super().__init__(changed, MacKeyboardMonitor, MacMouseMonitor)
