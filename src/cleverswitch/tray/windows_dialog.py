"""Small ctypes binding to IFileDialog; no extra COM runtime or shell commands."""

import ctypes
import pathlib
import uuid
from contextlib import contextmanager
from ctypes import wintypes

CANCELLED = 0x800704C7


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__()
        self.data[:] = uuid.UUID(value).bytes_le


class FileType(ctypes.Structure):
    _fields_ = [("name", wintypes.LPCWSTR), ("pattern", wintypes.LPCWSTR)]


def check(result):
    if result < 0:
        raise OSError(f"Windows dialog HRESULT 0x{result & 0xFFFFFFFF:08X}")
    return result


@contextmanager
def apartment():
    api = ctypes.WinDLL("ole32")
    api.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    api.CoInitializeEx.restype = ctypes.c_long
    api.CoUninitialize.argtypes = ()
    api.CoUninitialize.restype = None
    # COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE. S_FALSE still needs cleanup.
    check(api.CoInitializeEx(None, 2 | 4))
    try:
        yield api
    finally:
        api.CoUninitialize()


class Interface:
    def __init__(self, pointer):
        self.pointer = pointer

    def call(self, slot, types=(), *args):
        table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[slot])
        return method(self.pointer, *args)

    def release(self):
        if self.pointer:
            self.call(2)
            self.pointer = None


def create(api, save):
    clsid = GUID("c0b4e2f3-ba21-4773-8dba-335ec946eb8b" if save else "dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7")
    iid = GUID("42f85136-db7e-439c-85f1-e4075d135fc8")  # IFileDialog
    pointer = ctypes.c_void_p()
    api.CoCreateInstance.argtypes = (
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )
    api.CoCreateInstance.restype = ctypes.c_long
    check(api.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer)))
    return Interface(pointer)


def configure(dialog, save, title, name):
    flags = wintypes.DWORD()
    check(dialog.call(10, (ctypes.POINTER(wintypes.DWORD),), ctypes.byref(flags)))
    # Keep default flags, filesystem only, no cwd changes, valid parent, overwrite prompt.
    flags.value |= 0x40 | 8 | 0x800 | (2 if save else 0x1000)
    check(dialog.call(9, (wintypes.DWORD,), flags.value))
    types = (FileType * 1)(FileType("Enlace CleverSwitch (*.json)", "*.json"))
    check(dialog.call(4, (wintypes.UINT, ctypes.POINTER(FileType)), 1, types))
    check(dialog.call(22, (wintypes.LPCWSTR,), "json"))
    check(dialog.call(17, (wintypes.LPCWSTR,), title))
    if save:
        check(dialog.call(15, (wintypes.LPCWSTR,), name))


def result_path(api, dialog):
    pointer = ctypes.c_void_p()
    check(dialog.call(20, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(pointer)))
    item = Interface(pointer)
    text = ctypes.c_void_p()
    api.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    api.CoTaskMemFree.restype = None
    try:
        check(item.call(5, (wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)), 0x80058000, ctypes.byref(text)))
        return pathlib.Path(ctypes.wstring_at(text))
    finally:
        if text:
            api.CoTaskMemFree(text)
        item.release()


def choose_file(save, title, name):
    with apartment() as api:
        dialog = create(api, save)
        try:
            configure(dialog, save, title, name)
            # No cross-thread owner: the tray message loop remains independent.
            result = dialog.call(3, (wintypes.HWND,), None)
            if result & 0xFFFFFFFF == CANCELLED:
                return None
            check(result)
            return result_path(api, dialog)
        finally:
            dialog.release()


def downloads_directory():
    """Resolve the real Downloads folder, including relocated user profiles."""
    api = ctypes.WinDLL("shell32")
    api.SHGetKnownFolderPath.argtypes = (
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
    )
    api.SHGetKnownFolderPath.restype = ctypes.c_long
    folder = GUID("374de290-123f-4565-9164-39c4925e467b")
    text = ctypes.c_void_p()
    ole = ctypes.WinDLL("ole32")
    ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    ole.CoTaskMemFree.restype = None
    try:
        check(api.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(text)))
        return pathlib.Path(ctypes.wstring_at(text))
    finally:
        if text:
            ole.CoTaskMemFree(text)
