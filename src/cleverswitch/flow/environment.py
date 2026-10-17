"""Read local network state and receive native network/sleep events, without scans."""

import ctypes
import dataclasses
import ipaddress
import re
import socket
import struct
import subprocess
import sys

from .identity import local_addresses, private_ip


@dataclasses.dataclass(frozen=True)
class Network:
    address: str | None = None
    network: str | None = None
    gateway: str | None = None
    router_mac: str | None = None


def normalized_mac(value):
    parts = value.lower().split(":")
    if len(parts) != 6 or any(not 1 <= len(part) <= 2 for part in parts):
        return None
    try:
        raw = bytes(int(part, 16) for part in parts)
    except ValueError:
        return None
    return ":".join(f"{part:02x}" for part in raw) if any(raw) else None


def home_state(config, network):
    """(Can listen, can actively search). Router MAC is a power hint, not authentication."""
    if not network.address or network.network != config["network"]:
        return False, False
    saved = config.get("home_router")
    if not saved:
        return True, True  # Legacy/unlearned profile: finite search; learn after mutual TLS.
    if network.gateway != saved["gateway"]:
        return False, False
    if network.router_mac is None:
        return True, False  # Missing ARP entry: passive only; never send an ARP probe.
    matches = network.router_mac == saved["mac"]
    return matches, matches


def windows_router(address):
    # IPv4 MIB structures have DWORD alignment on x86 and x64. Reading these
    # tables does not emit packets, change routes or request administrator rights.
    api = ctypes.WinDLL("iphlpapi", use_last_error=True)

    def table(name, row_size):
        function = getattr(api, name)
        function.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_int)
        function.restype = ctypes.c_uint32
        size = ctypes.c_uint32()
        result = function(None, ctypes.byref(size), False)
        if result not in (0, 122) or not 4 <= size.value <= 2_000_000:
            return []
        buffer = ctypes.create_string_buffer(size.value)
        if function(buffer, ctypes.byref(size), False) != 0:
            return []
        count = struct.unpack_from("<I", buffer)[0]
        if count * row_size + 4 > size.value:
            raise OSError("Tabla de red local inválida")
        return [buffer.raw[4 + i * row_size : 4 + (i + 1) * row_size] for i in range(count)]

    def to_ip(raw):
        return socket.inet_ntoa(raw)

    indices = {struct.unpack_from("<I", row, 4)[0] for row in table("GetIpAddrTable", 24) if to_ip(row[:4]) == address}
    routes = [struct.unpack("<14I", row) for row in table("GetIpForwardTable", 56)]
    routes = [row for row in routes if row[0] == 0 and row[1] == 0 and row[4] in indices and row[3]]
    if not routes:
        return None, None
    route = min(routes, key=lambda row: row[9])
    gateway = to_ip(struct.pack("<I", route[3]))
    for row in table("GetIpNetTable", 24):
        index, length = struct.unpack_from("<II", row)
        if index == route[4] and to_ip(row[16:20]) == gateway and length == 6:
            if struct.unpack_from("<I", row, 20)[0] in (3, 4):
                return gateway, normalized_mac(":".join(f"{byte:02x}" for byte in row[8:14]))
    return gateway, None


def mac_router(address):
    import SystemConfiguration as sc

    store = sc.SCDynamicStoreCreate(None, "CleverSwitch read-only network", None, None)
    for key in sc.SCDynamicStoreCopyKeyList(store, "State:/Network/Service/.*/IPv4") or []:
        value = sc.SCDynamicStoreCopyValue(store, key) or {}
        if address not in value.get("Addresses", ()) or not value.get("Router"):
            continue
        gateway = private_ip(value["Router"])
        # -n avoids DNS; arp reads the OS neighbour cache only, without probing.
        try:
            result = subprocess.run(
                ["/usr/sbin/arp", "-n", gateway], capture_output=True, text=True, timeout=2, check=False
            )
            match = re.search(r"\bat\s+([0-9a-fA-F:]+)\b", result.stdout)
            return gateway, normalized_mac(match[1]) if match else None
        except (OSError, subprocess.TimeoutExpired):
            return gateway, None
    return None, None


def snapshot(config):
    available = local_addresses()
    matches = [(ip, net) for _, ip, net in available if net == config["network"]]
    exact = next((value for value in matches if value[0] == config["address"]), None)
    selected = exact or (matches[0] if len(matches) == 1 else None)
    if selected is None:
        return Network()
    address, network = selected
    if sys.platform == "win32":
        gateway, mac = windows_router(address)
    elif sys.platform == "darwin":
        gateway, mac = mac_router(address)
    else:
        gateway, mac = None, None
    if gateway and ipaddress.ip_address(gateway) not in ipaddress.ip_network(network):
        return Network(address, network)  # VPN gateway is not the selected home router.
    return Network(address, network, gateway, mac)


class WindowsMonitor:
    def __init__(self, changed):
        self.changed = changed
        self.sleeping = False
        self.handles = []
        self.api = ctypes.WinDLL("iphlpapi", use_last_error=True)
        self.power = ctypes.WinDLL("powrprof", use_last_error=True)
        self.network_callback = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)(
            lambda context, row, type_: self.changed("network")
        )
        self.power_callback = ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)(
            self._power
        )

        class Parameters(ctypes.Structure):
            _fields_ = [("callback", ctypes.c_void_p), ("context", ctypes.c_void_p)]

        self.parameters = Parameters(ctypes.cast(self.power_callback, ctypes.c_void_p), None)
        self.power_handle = ctypes.c_void_p()

    def _power(self, context, type_, setting):
        if type_ == 4:
            self.sleeping = True
            self.changed("sleep")
        elif type_ in (7, 18):
            self.sleeping = False
            self.changed("resume")
        return 0

    def start(self):
        try:
            for name in ("NotifyIpInterfaceChange", "NotifyUnicastIpAddressChange", "NotifyRouteChange2"):
                function = getattr(self.api, name)
                function.argtypes = (
                    ctypes.c_uint16,
                    type(self.network_callback),
                    ctypes.c_void_p,
                    ctypes.c_ubyte,
                    ctypes.POINTER(ctypes.c_void_p),
                )
                function.restype = ctypes.c_uint32
                handle = ctypes.c_void_p()
                result = function(socket.AF_INET, self.network_callback, None, False, ctypes.byref(handle))
                if result:
                    raise OSError(result, "No se pudo observar la red")
                self.handles.append(handle)
            function = self.power.PowerRegisterSuspendResumeNotification
            function.argtypes = (ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            function.restype = ctypes.c_uint32
            result = function(2, ctypes.byref(self.parameters), ctypes.byref(self.power_handle))
            if result:
                raise OSError(result, "No se pudo observar el reposo")
        except Exception:
            self.close()
            raise

    def close(self):
        cancel = self.api.CancelMibChangeNotify2
        cancel.argtypes = (ctypes.c_void_p,)
        cancel.restype = ctypes.c_uint32
        for handle in self.handles:
            cancel(handle)
        self.handles.clear()
        if self.power_handle.value:
            cancel = self.power.PowerUnregisterSuspendResumeNotification
            cancel.argtypes = (ctypes.c_void_p,)
            cancel.restype = ctypes.c_uint32
            cancel(self.power_handle)
            self.power_handle = ctypes.c_void_p()


class MacMonitor:
    def __init__(self, changed):
        self.changed = changed
        self.sleeping = False
        self.store = self.source = self.center = None
        self.observers = []

    def start(self):
        import CoreFoundation as cf
        import SystemConfiguration as sc
        from AppKit import NSWorkspace, NSWorkspaceDidWakeNotification, NSWorkspaceWillSleepNotification
        from Foundation import NSOperationQueue

        self.store = sc.SCDynamicStoreCreate(
            None, "CleverSwitch network events", lambda store, keys, context: self.changed("network"), None
        )
        if not self.store or not sc.SCDynamicStoreSetNotificationKeys(
            self.store,
            None,
            [
                "State:/Network/Global/IPv4",
                "State:/Network/Service/.*/IPv4",
                "State:/Network/Interface/.*/IPv4",
                "State:/Network/Interface/.*/Link",
            ],
        ):
            raise OSError("No se pudo observar la red")
        self.source = sc.SCDynamicStoreCreateRunLoopSource(None, self.store, 0)
        if not self.source:
            raise OSError("No se pudo observar la red")
        cf.CFRunLoopAddSource(cf.CFRunLoopGetMain(), self.source, cf.kCFRunLoopCommonModes)
        self.center = NSWorkspace.sharedWorkspace().notificationCenter()
        for name, sleeping in ((NSWorkspaceWillSleepNotification, True), (NSWorkspaceDidWakeNotification, False)):

            def callback(notification, sleeping=sleeping):
                self.sleeping = sleeping
                self.changed("sleep" if sleeping else "resume")

            self.observers.append(
                self.center.addObserverForName_object_queue_usingBlock_(
                    name, None, NSOperationQueue.mainQueue(), callback
                )
            )

    def close(self):
        if self.source:
            import CoreFoundation as cf

            cf.CFRunLoopRemoveSource(cf.CFRunLoopGetMain(), self.source, cf.kCFRunLoopCommonModes)
        if self.center:
            for observer in self.observers:
                self.center.removeObserver_(observer)
        self.observers.clear()
        self.source = self.store = self.center = None


def native_monitor(changed):
    if sys.platform == "win32":
        return WindowsMonitor(changed)
    if sys.platform == "darwin":
        return MacMonitor(changed)
    raise RuntimeError("Plataforma no compatible")
