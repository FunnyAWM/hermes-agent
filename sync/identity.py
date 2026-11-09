"""TLS identity for the sync chain's master role.

Mirrors the Android ``SyncIdentity.kt``: a self-signed EC P-256 certificate generated
locally, the private key never leaving the machine. The peer binds the certificate
fingerprint via trust-on-first-use (the Android VALIDATION §3 exemption) — in the
self-signed setting there is no CA chain to verify, the fingerprint *is* the identity.
"""

from __future__ import annotations

import hashlib
import ssl
from datetime import datetime, timedelta, timezone, UTC
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from hermes_constants import secure_parent_dir

_KEY_FILE = "identity-key.pem"
_CERT_FILE = "identity-cert.pem"
_SUBJECT = "KouriChat Sync"
_VALIDITY_DAYS = 20 * 365


def _pristine_context(protocol: int, configure) -> ssl.SSLContext:
    """Build a context with truststore's global injection lifted for the duration.

    On CPython 3.14 the ssl module's ``check_hostname``/``verify_mode`` setters resolve
    ``SSLContext`` from module globals, so once truststore's injected class is in place
    (hermes installs it at boot, ``agent.ssl_verify``) those assignments recurse into a
    stack overflow — and the injected class cannot wrap *server-side* sockets at all.
    ``extract_from_ssl``/``inject_into_ssl`` are truststore's own round-trip, so outbound
    calls in the same process keep their platform trust store.
    """
    if ssl.SSLContext.__module__ == "ssl":
        context = ssl.SSLContext(protocol)
        configure(context)
        return context
    try:
        import truststore
    except ImportError:
        context = ssl.SSLContext(protocol)
        configure(context)
        return context
    truststore.extract_from_ssl()
    try:
        context = ssl.SSLContext(protocol)
        configure(context)
        return context
    finally:
        truststore.inject_into_ssl()


def _configure_tofu(context: ssl.SSLContext) -> None:
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE


def master_tls_context(identity: FileSyncIdentity) -> ssl.SSLContext:
    """Server-side context presenting the chain's self-signed identity."""

    def configure(context: ssl.SSLContext) -> None:
        context.load_cert_chain(str(identity.cert_path), str(identity.key_path))

    return _pristine_context(ssl.PROTOCOL_TLS_SERVER, configure)


def tofu_client_ssl_context() -> ssl.SSLContext:
    """Client-side context for the pinned-fingerprint (TOFU) trust model: no CA chain
    verification — the fingerprint check in sync.client is the whole trust decision."""
    return _pristine_context(ssl.PROTOCOL_TLS_CLIENT, _configure_tofu)


def fingerprint_of_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


class FileSyncIdentity:
    """Self-signed identity persisted as PEM under ``<sync root>/.meta/identity/``.

    Created once per chain membership and reused across restarts (the Android keystore
    alias behaves the same way); the key file's directory is restricted owner-only.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.key_path = self.directory / _KEY_FILE
        self.cert_path = self.directory / _CERT_FILE
        if not self.key_path.is_file() or not self.cert_path.is_file():
            self._generate()
        self._cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        self._der = self._cert.public_bytes(serialization.Encoding.DER)

    def fingerprint(self) -> str:
        return fingerprint_of_der(self._der)

    def certificate_der(self) -> bytes:
        return self._der

    def _generate(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _SUBJECT)])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=_VALIDITY_DAYS))
            .sign(key, hashes.SHA256())
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        # The PEM files' parent is the secret-bearing directory; restrict it (no-op where
        # the platform has no POSIX bits, enforced where it has).
        secure_parent_dir(self.key_path)
        self.key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
