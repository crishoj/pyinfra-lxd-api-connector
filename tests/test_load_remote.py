"""`_load_remote` — resolves a remote name to (base_url, ssl_context)."""

from __future__ import annotations

import datetime
import ssl
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pyinfra.api.exceptions import ConnectError

import pyinfra_lxd_api_connector as mod

from .conftest import write_lxc_config


def _make_self_signed_pair(common_name: str) -> tuple[bytes, bytes]:
    """Generate a throwaway self-signed cert + private key (PEM bytes)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


@pytest.fixture
def remote_with_certs(lxc_config_dir: Path) -> str:
    """Set up a complete lxc config with a self-signed cert pair for `r1`."""
    remote = "r1"
    cert_pem, key_pem = _make_self_signed_pair("test")
    (lxc_config_dir / "client.crt").write_bytes(cert_pem)
    (lxc_config_dir / "client.key").write_bytes(key_pem)
    server_cert_pem, _ = _make_self_signed_pair("server")
    (lxc_config_dir / "servercerts" / f"{remote}.crt").write_bytes(server_cert_pem)
    write_lxc_config(
        lxc_config_dir,
        remotes={remote: {"addr": "https://lxd.test:8443"}},
    )
    return remote


def test_happy_path_returns_url_and_ssl_context(remote_with_certs):
    base_url, ctx = mod._load_remote(remote_with_certs)
    assert base_url == "https://lxd.test:8443"
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False


def test_missing_config_raises(lxc_config_dir):
    # Config dir exists but no config.yml file.
    with pytest.raises(ConnectError, match="LXD client config not found"):
        mod._load_remote("anyremote")


def test_unknown_remote_raises(lxc_config_dir):
    write_lxc_config(lxc_config_dir, remotes={"known": {"addr": "https://x"}})
    with pytest.raises(ConnectError, match="not found"):
        mod._load_remote("unknown_remote_name")


def test_non_https_addr_rejected(lxc_config_dir):
    write_lxc_config(
        lxc_config_dir,
        remotes={"unixy": {"addr": "unix:/var/snap/lxd/common/lxd/unix.socket"}},
    )
    with pytest.raises(ConnectError, match="unsupported addr"):
        mod._load_remote("unixy")


def test_wrong_protocol_rejected(lxc_config_dir):
    write_lxc_config(
        lxc_config_dir,
        remotes={
            "simplestream": {
                "addr": "https://images.linuxcontainers.org",
                "protocol": "simplestreams",
            }
        },
    )
    with pytest.raises(ConnectError, match="protocol"):
        mod._load_remote("simplestream")


def test_missing_client_cert_raises(lxc_config_dir):
    write_lxc_config(
        lxc_config_dir,
        remotes={"r2": {"addr": "https://lxd.test:8443"}},
    )
    # No certs written at all.
    with pytest.raises(ConnectError, match="cert/key missing"):
        mod._load_remote("r2")
