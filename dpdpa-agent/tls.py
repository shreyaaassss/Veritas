"""
Veritas TLS Configuration
==========================
Manages TLS certificates for the Veritas Server.

Behaviour:
  - VERITAS_TLS=auto (default): use HTTPS if cert/key exist; generate self-signed
    cert on first run; fall back to HTTP with a clear warning if generation fails.
  - VERITAS_TLS=true : require HTTPS; exit if cert cannot be loaded/generated.
  - VERITAS_TLS=false: HTTP only (development/testing — not for production).

Certificate locations (override via env vars):
  VERITAS_TLS_CERT — path to the server certificate (PEM)
  VERITAS_TLS_KEY  — path to the server private key (PEM)

By default these are placed in data_root()/certs/.

Self-signed certificate properties:
  - RSA 2048-bit key
  - Valid for 825 days (~2.25 years — Apple's limit for trusted certs)
  - Subject Alternative Names: localhost, 127.0.0.1, machine hostname, and
    any extra SANs from VERITAS_TLS_SANS (comma-separated)
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("veritas.tls")

_CERT_VALIDITY_DAYS = 825  # Apple-imposed limit for browser-trusted certs


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def tls_mode() -> str:
    """Returns 'auto', 'true', or 'false'."""
    return os.environ.get("VERITAS_TLS", "auto").strip().lower()


def tls_enabled() -> bool:
    mode = tls_mode()
    if mode == "false":
        return False
    # auto or true: enabled if we can get valid cert paths
    return True


def cert_path() -> Path:
    from runtime_paths import data_root
    env = os.environ.get("VERITAS_TLS_CERT", "").strip()
    return Path(env) if env else data_root() / "certs" / "server.crt"


def key_path() -> Path:
    from runtime_paths import data_root
    env = os.environ.get("VERITAS_TLS_KEY", "").strip()
    return Path(env) if env else data_root() / "certs" / "server.key"


def extra_sans() -> list[str]:
    """Extra Subject Alternative Names from VERITAS_TLS_SANS env var."""
    raw = os.environ.get("VERITAS_TLS_SANS", "").strip()
    return [s.strip() for s in raw.split(",") if s.strip()] if raw else []


# ---------------------------------------------------------------------------
# Self-signed certificate generation
# ---------------------------------------------------------------------------

def generate_self_signed_cert(cert_file: Path, key_file: Path) -> bool:
    """
    Generate a self-signed TLS certificate for the Veritas Server.
    Returns True on success, False on failure.

    The certificate includes SANs for:
      - localhost
      - 127.0.0.1
      - the machine's hostname
      - any extra SANs from VERITAS_TLS_SANS

    This certificate is suitable for on-premise deployments where a
    trusted CA cert cannot be obtained. Clients (Agents) must be
    configured to trust this certificate (or skip verification in dev).
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import ipaddress
    except ImportError:
        logger.error("cryptography package not available — cannot generate TLS certificate.")
        return False

    cert_file.parent.mkdir(parents=True, exist_ok=True)

    # Generate private key
    logger.info("Generating self-signed TLS certificate at %s ...", cert_file)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build Subject Alternative Names
    san_list = []
    san_list.append(x509.DNSName("localhost"))
    try:
        san_list.append(x509.DNSName(socket.gethostname()))
    except Exception:
        pass
    san_list.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
        if host_ip != "127.0.0.1":
            san_list.append(x509.IPAddress(ipaddress.IPv4Address(host_ip)))
    except Exception:
        pass
    for extra in extra_sans():
        try:
            san_list.append(x509.IPAddress(ipaddress.ip_address(extra)))
        except ValueError:
            san_list.append(x509.DNSName(extra))

    # Build certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Veritas Technologies"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Veritas DPDPA Server"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=_CERT_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )

    # Write certificate (PEM)
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert_file.chmod(0o644)

    # Write private key (PEM, owner read-only)
    key_file.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)

    logger.info(
        "Self-signed TLS certificate generated (valid %d days). "
        "Distribute %s to Veritas Agents so they can verify the server.",
        _CERT_VALIDITY_DAYS,
        cert_file,
    )
    return True


# ---------------------------------------------------------------------------
# SSL context builder for uvicorn
# ---------------------------------------------------------------------------

def get_ssl_params() -> Optional[dict]:
    """
    Returns SSL keyword arguments for uvicorn.run(), or None if TLS is disabled.

    Priority:
      1. VERITAS_TLS=false → return None (HTTP)
      2. Existing cert/key files → return ssl params
      3. No cert/key → try to generate self-signed cert
      4. Generation fails → log warning and return None (HTTP fallback)
    """
    mode = tls_mode()

    if mode == "false":
        logger.warning(
            "TLS is explicitly disabled (VERITAS_TLS=false). "
            "Running over HTTP — DO NOT use in production."
        )
        return None

    crt = cert_path()
    key = key_path()

    if not crt.exists() or not key.exists():
        logger.info("No TLS certificate found at %s — generating self-signed certificate.", crt)
        if not generate_self_signed_cert(crt, key):
            if mode == "true":
                raise RuntimeError(
                    "VERITAS_TLS=true but certificate generation failed. "
                    "Provide VERITAS_TLS_CERT and VERITAS_TLS_KEY or check the cryptography package."
                )
            logger.warning("TLS certificate generation failed — falling back to HTTP.")
            return None

    return {"ssl_certfile": str(crt), "ssl_keyfile": str(key)}


def cert_fingerprint() -> Optional[str]:
    """Return the SHA-256 fingerprint of the current server certificate (for display)."""
    crt = cert_path()
    if not crt.exists():
        return None
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        cert = x509.load_pem_x509_certificate(crt.read_bytes())
        fp = cert.fingerprint(hashes.SHA256()).hex()
        return ":".join(fp[i:i+2].upper() for i in range(0, len(fp), 2))
    except Exception:
        return None
