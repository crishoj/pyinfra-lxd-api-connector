"""Inventory parsing: @lxd_api/<remote>:<container> and bare-name fallback."""

from __future__ import annotations

import pytest
from pyinfra.api.exceptions import InventoryError

from pyinfra_lxd_api_connector import LxdApiConnector

from .conftest import write_lxc_config


def parse(name: str | None) -> tuple[str, dict, list[str]]:
    """Drive make_names_data and unpack its single yielded tuple."""
    return next(iter(LxdApiConnector.make_names_data(name)))


def test_qualified_host_parses():
    full_name, data, groups = parse("mycluster:web1")
    assert full_name == "@lxd_api/mycluster:web1"
    assert data == {"lxd_remote": "mycluster", "lxd_container": "web1"}
    assert groups == ["@lxd_api"]


def test_bare_host_uses_default_remote(lxc_config_dir):
    write_lxc_config(lxc_config_dir, **{"default-remote": "mycluster"})
    full_name, data, _ = parse("web1")
    assert full_name == "@lxd_api/mycluster:web1"
    assert data["lxd_remote"] == "mycluster"
    assert data["lxd_container"] == "web1"


def test_bare_host_no_default_remote_set(lxc_config_dir):
    # Config exists but has no `default-remote` field.
    write_lxc_config(lxc_config_dir, remotes={})
    with pytest.raises(InventoryError, match="default-remote"):
        parse("web1")


def test_bare_host_no_config_file_at_all(lxc_config_dir):
    # lxc_config_dir fixture redirects paths but doesn't write config.yml.
    # The error here is a ConnectError from _read_lxc_config, since we're
    # trying to read default-remote without any config present at all.
    from pyinfra.api.exceptions import ConnectError

    with pytest.raises(ConnectError, match="LXD client config not found"):
        parse("web1")


def test_empty_name_raises():
    with pytest.raises(InventoryError, match="No LXD container provided"):
        parse(None)
    with pytest.raises(InventoryError, match="No LXD container provided"):
        parse("")


def test_empty_remote_or_container_raises():
    # ":foo" — empty remote
    with pytest.raises(InventoryError):
        parse(":foo")
    # "foo:" — empty container
    with pytest.raises(InventoryError):
        parse("foo:")


def test_qualified_overrides_default_remote(lxc_config_dir):
    write_lxc_config(lxc_config_dir, **{"default-remote": "ignored"})
    full_name, data, _ = parse("explicit:web1")
    assert data["lxd_remote"] == "explicit"
