"""Offline two-file pairing; private keys never leave their computer."""

import base64
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import pathlib
import secrets
import socket
import ssl
import uuid

PORT = 47654
MAX_FILE = 16384
NETWORKS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_ip(value, testing=False):
    ip = ipaddress.IPv4Address(value)
    if not any(ip in net for net in NETWORKS) and not (testing and ip.is_loopback):
        raise ValueError("Elige una dirección IPv4 de tu red doméstica")
    return str(ip)


def local_addresses():
    import psutil

    stats = psutil.net_if_stats()
    result = []
    for name, addresses in psutil.net_if_addrs().items():
        if name not in stats or not stats[name].isup:
            continue
        for address in addresses:
            if address.family == socket.AF_INET:
                try:
                    ip = private_ip(address.address)
                    prefix = ipaddress.IPv4Network(f"{ip}/{address.netmask}", strict=False)
                    result.append((name, ip, str(prefix)))
                except ValueError:
                    pass
    return result


def private_network(value, address, testing=False):
    network = ipaddress.IPv4Network(value)
    allowed = any(network.subnet_of(net) for net in NETWORKS)
    if testing and network.subnet_of(ipaddress.ip_network("127.0.0.0/8")):
        allowed = True
    if not allowed or ipaddress.ip_address(address) not in network or network.prefixlen > 30:
        raise ValueError("Subred local inválida")
    return str(network)


def protect(path):
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    # An explicit protected DACL: file owner and SYSTEM, no inherited extra users.
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = api.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
    convert.restype = wintypes.BOOL
    set_security = api.SetFileSecurityW
    set_security.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    set_security.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    inheritance = "OICI" if path.is_dir() else ""
    sddl = f"D:P(A;{inheritance};FA;;;SY)(A;{inheritance};FA;;;OW)"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not set_security(str(path), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = (ctypes.c_void_p,)
        free(descriptor)


def write_private(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    # Exclusive creation prevents following a preexisting temporary symlink.
    with temporary.open("xb") as handle:
        protect(temporary)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    protect(path)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_file(path):
    with pathlib.Path(path).open("rb") as handle:
        raw = handle.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError("Archivo de enlace demasiado grande")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Archivo de enlace inválido")
    return value


def validate_cert(pem):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec

    if not isinstance(pem, str) or len(pem) > 4096:
        raise ValueError("Certificado inválido")
    cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
    if not isinstance(cert.public_key(), ec.EllipticCurvePublicKey) or not isinstance(
        cert.public_key().curve, ec.SECP256R1
    ):
        raise ValueError("Identidad no compatible")
    cert.public_key().verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(cert.signature_hash_algorithm))
    now = datetime.datetime.now(datetime.UTC)
    if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
        raise ValueError("Identidad caducada")
    return pem


class Pairing:
    def __init__(self, directory=None, testing=False):
        self.directory = pathlib.Path(directory or pathlib.Path.home() / ".config/cleverswitch-flow")
        self.testing = testing

    def load(self):
        if not (self.directory / "link.json").exists():
            return None
        value = read_file(self.directory / "link.json")
        if value.get("version") != 1 or value.get("role") not in ("windows", "mac"):
            raise ValueError("Configuración de enlace inválida")
        uuid.UUID(value["link"])
        if len(base64.b64decode(value["secret"], validate=True)) != 32:
            raise ValueError("Secreto inválido")
        private_ip(value["address"], self.testing)
        private_network(value["network"], value["address"], self.testing)
        if value.get("peer"):
            private_ip(value["peer"]["address"], self.testing)
            validate_cert(value["peer"]["cert"])
        return value

    def save(self, config):
        self.directory.mkdir(parents=True, exist_ok=True)
        protect(self.directory)
        write_private(self.directory / "link.json", canonical(config))

    def identity(self, link, role):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        self.directory.mkdir(parents=True, exist_ok=True)
        protect(self.directory)
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"CleverSwitch-{link}-{role}")])
        now = datetime.datetime.now(datetime.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=730))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]), False
            )
            .sign(key, hashes.SHA256())
        )
        write_private(
            self.directory / "identity.key",
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ),
        )
        pem = cert.public_bytes(serialization.Encoding.PEM)
        write_private(self.directory / "identity.pem", pem)
        return pem.decode("ascii")

    def create_invitation(self, path, address, network):
        if self.load():
            raise ValueError("Ya existe un enlace; conserva la identidad actual")
        link = str(uuid.uuid4())
        config = {
            "version": 1,
            "link": link,
            "role": "windows",
            "address": private_ip(address, self.testing),
            "network": private_network(network, address, self.testing),
            "secret": base64.b64encode(secrets.token_bytes(32)).decode(),
        }
        config["cert"] = self.identity(link, "windows")
        self.save(config)
        self.export_invitation(path)

    def export_invitation(self, path):
        config = self.load()
        if not config or config["role"] != "windows" or config.get("peer"):
            raise ValueError("No hay un enlace pendiente para exportar")
        # The invitation is sensitive: it authenticates the one-time response.
        write_private(path, canonical({**config, "kind": "invitation"}))

    def accept_invitation(self, path, response_path, address, network):
        if self.load():
            raise ValueError("Ya existe un enlace")
        invitation = read_file(path)
        if invitation.get("kind") != "invitation" or invitation.get("role") != "windows":
            raise ValueError("Selecciona el enlace creado en Windows")
        uuid.UUID(invitation["link"])
        secret = base64.b64decode(invitation["secret"], validate=True)
        if len(secret) != 32 or invitation.get("version") != 1:
            raise ValueError("Enlace inválido")
        peer_address = private_ip(invitation["address"], self.testing)
        cert = validate_cert(invitation["cert"])
        config = {
            "version": 1,
            "link": invitation["link"],
            "role": "mac",
            "secret": invitation["secret"],
            "address": private_ip(address, self.testing),
            "network": private_network(network, address, self.testing),
            "peer": {"address": peer_address, "cert": cert},
        }
        config["cert"] = self.identity(config["link"], "mac")
        response = {
            "version": 1,
            "kind": "response",
            "link": config["link"],
            "role": "mac",
            "address": config["address"],
            "cert": config["cert"],
        }
        response["proof"] = hmac.new(secret, canonical(response), "sha256").hexdigest()
        write_private(response_path, canonical(response))
        self.save(config)

    def accept_response(self, path):
        config = self.load()
        if not config or config["role"] != "windows" or config.get("peer"):
            raise ValueError("No hay un enlace pendiente en Windows")
        response = read_file(path)
        proof = response.pop("proof", "")
        expected = hmac.new(base64.b64decode(config["secret"]), canonical(response), "sha256").hexdigest()
        if not isinstance(proof, str) or not hmac.compare_digest(proof, expected):
            raise ValueError("La respuesta no pertenece al enlace que creaste")
        if (response.get("link"), response.get("kind"), response.get("role"), response.get("version")) != (
            config["link"],
            "response",
            "mac",
            1,
        ):
            raise ValueError("Respuesta de otro enlace")
        config["peer"] = {
            "address": private_ip(response["address"], self.testing),
            "cert": validate_cert(response["cert"]),
        }
        self.save(config)

    def tls(self, server=False):
        config = self.load()
        if not config or not config.get("peer"):
            raise ValueError("Completa el enlace con el otro equipo")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = False  # Exact certificate pin, independent of DHCP address.
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=config["peer"]["cert"])
        context.load_cert_chain(self.directory / "identity.pem", self.directory / "identity.key")
        return context

    def verify_pin(self, connection):
        config = self.load()
        expected = ssl.PEM_cert_to_DER_cert(config["peer"]["cert"])
        if not hmac.compare_digest(
            hashlib.sha256(connection.getpeercert(binary_form=True)).digest(), hashlib.sha256(expected).digest()
        ):
            raise ssl.SSLError("Identidad del equipo incorrecta")
