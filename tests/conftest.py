"""Shared fixtures for the @lxd_api connector tests."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml


@pytest.fixture
def lxc_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated `~/.config/lxc` for tests, with paths re-pointed at it.

    The connector module reads `LXC_CONFIG_DIR` and `LXC_CONFIG_PATH`
    at function-call time (not import time), so swapping them via
    monkeypatch is enough to redirect all config / cert lookups to a
    per-test scratch directory.
    """
    import pyinfra_lxd_api_connector as mod

    monkeypatch.setattr(mod, "LXC_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(mod, "LXC_CONFIG_PATH", tmp_path / "config.yml")
    (tmp_path / "servercerts").mkdir()
    return tmp_path


def write_lxc_config(config_dir: Path, **fields) -> None:
    """Write a minimal lxc client config.yml into the given dir."""
    (config_dir / "config.yml").write_text(yaml.safe_dump(fields))


def write_remote_certs(config_dir: Path, remote: str) -> None:
    """Write placeholder client + server cert files for a remote.

    The contents don't need to be valid PEM for tests that don't
    actually open a TLS connection — but tests of `_load_remote`
    that build an SSLContext do need real cert material. Use
    `_make_self_signed_pem()` for those.
    """
    (config_dir / "client.crt").write_text("")
    (config_dir / "client.key").write_text("")
    (config_dir / "servercerts" / f"{remote}.crt").write_text("")


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the retry helper's `time.sleep` a no-op so tests run instantly."""
    import pyinfra_lxd_api_connector as mod

    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)


def make_mock_client(handler) -> httpx.Client:
    """Build an httpx.Client whose requests are served by `handler`.

    The handler is `(httpx.Request) -> httpx.Response` — pass any
    callable, including a `pytest.fixture`'d list of canned
    responses.
    """
    return httpx.Client(
        base_url="https://test.invalid",
        transport=httpx.MockTransport(handler),
    )


class FakeHostData:
    def __init__(self, remote: str = "testremote", container: str = "testc"):
        self.lxd_remote = remote
        self.lxd_container = container


class FakeHost:
    """Minimal stand-in for pyinfra.api.host.Host for connector tests."""

    def __init__(self, remote: str = "testremote", container: str = "testc"):
        self.data = FakeHostData(remote, container)
        self.print_prefix = f"[{remote}:{container}] "
