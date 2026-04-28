"""
LXD API connector for pyinfra.

Talks to the LXD HTTPS API directly via `httpx` — no `lxc` CLI
subprocess, no SSH hop, no websockets. Uses the
`container_exec_recording` API extension (`record-output: true`
mode) so commands run without opening any stdio websockets, and
holds one kept-alive HTTPS connection per host across all commands
and file transfers.

Inventory syntax:

    @lxd_api/<container>            # default remote: microcloud
    @lxd_api/<remote>:<container>   # explicit remote name

Reads remote URL and certs from the standard `lxc` client config
at `~/.config/lxc/`:

    config.yml                  → remotes['<name>'].addr
    client.crt / client.key     → mTLS client identity
    servercerts/<name>.crt      → pinned server cert (cafile)

A sibling package, `pyinfra-lxd-local-connector`, provides an
equivalent `@lxd_local` connector that shells out to the `lxc` CLI
binary instead. The `@lxd_api` connector here is significantly
faster from off-cluster callers because each `lxc exec` invocation
opens ~6–7 fresh TCP+TLS connections per command, whereas this one
reuses a single kept-alive HTTPS connection.

Tracks pyinfra issue #677. Originally drafted by Claude (Anthropic)
in collaboration with Christian Rishøj — Christian provided the
requirements, the silent-SFTP-truncation bug diagnosis that
motivated the original CLI-based connector, the empirical analysis
showing record-output is the right fast path
(github.com/imusic-dk/microcloud#206), and review.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path
from typing import TYPE_CHECKING

import click
import httpx
import yaml
from typing_extensions import TypedDict, Unpack, override

from pyinfra import logger
from pyinfra.api.exceptions import ConnectError, InventoryError
from pyinfra.api.util import get_file_io, memoize
from pyinfra.connectors.base import BaseConnector, DataMeta
from pyinfra.connectors.util import (
    CommandOutput,
    OutputLine,
    extract_control_arguments,
    make_unix_command_for_host,
)

if TYPE_CHECKING:
    from pyinfra.api.arguments import ConnectorArguments
    from pyinfra.api.command import StringCommand
    from pyinfra.api.host import Host
    from pyinfra.api.state import State


DEFAULT_LXD_REMOTE = "microcloud"
LXC_CONFIG_DIR = Path(os.path.expanduser("~/.config/lxc"))
REQUIRED_API_EXTENSIONS = frozenset({"container_exec_recording"})


class ConnectorData(TypedDict):
    lxd_container: str
    lxd_remote: str


connector_data_meta: dict[str, DataMeta] = {
    "lxd_container": DataMeta("LXD container name"),
    "lxd_remote": DataMeta(
        f"LXD remote name as configured in `lxc remote list` "
        f"(default: {DEFAULT_LXD_REMOTE!r})"
    ),
}


@memoize
def show_warning() -> None:
    logger.warning("The @lxd_api connector is alpha — feedback welcome.")


@memoize
def _load_remote(remote_name: str) -> tuple[str, ssl.SSLContext]:
    """Resolve a remote name to (base_url, ssl_context).

    Server cert is pinned via cafile; hostname verification is
    disabled because LXD's self-signed cert typically lacks a SAN
    matching the Tailscale / cluster hostname. Trust anchor is the
    pinned cafile, same trust model the `lxc` CLI uses.
    """
    config_path = LXC_CONFIG_DIR / "config.yml"
    if not config_path.exists():
        raise ConnectError(
            f"LXD client config not found at {config_path}; "
            f"run `lxc remote add` first."
        )
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    remotes = config.get("remotes") or {}
    if remote_name not in remotes:
        raise ConnectError(
            f"LXD remote {remote_name!r} not found in {config_path} "
            f"(known: {sorted(remotes)})"
        )
    remote = remotes[remote_name]
    addr = remote.get("addr", "")
    if not addr.startswith("https://"):
        raise ConnectError(
            f"LXD remote {remote_name!r} has unsupported addr {addr!r} "
            f"(only https:// remotes are supported by this connector)"
        )
    if remote.get("protocol", "lxd") != "lxd":
        raise ConnectError(
            f"LXD remote {remote_name!r} uses protocol "
            f"{remote.get('protocol')!r}; only 'lxd' is supported."
        )

    server_cert = LXC_CONFIG_DIR / "servercerts" / f"{remote_name}.crt"
    client_cert = LXC_CONFIG_DIR / "client.crt"
    client_key = LXC_CONFIG_DIR / "client.key"
    for path in (server_cert, client_cert, client_key):
        if not path.exists():
            raise ConnectError(f"Required LXD cert/key missing: {path}")

    ctx = ssl.create_default_context(cafile=str(server_cert))
    ctx.check_hostname = False
    ctx.load_cert_chain(certfile=str(client_cert), keyfile=str(client_key))
    return addr.rstrip("/"), ctx


class LxdApiConnector(BaseConnector):
    """LXD API connector — talks to the LXD HTTPS API directly."""

    handles_execution = True

    data_cls = ConnectorData
    data_meta = connector_data_meta
    data: ConnectorData

    client: httpx.Client | None
    base_url: str

    def __init__(self, state: "State", host: "Host"):
        super().__init__(state, host)
        self.client = None
        self.base_url = ""

    @override
    @staticmethod
    def make_names_data(name=None):
        if not name:
            raise InventoryError("No LXD container provided!")

        if ":" in name:
            remote, container = name.split(":", 1)
        else:
            remote, container = DEFAULT_LXD_REMOTE, name

        if not container:
            raise InventoryError("No LXD container name provided!")

        show_warning()

        yield (
            f"@lxd_api/{remote}:{container}",
            {"lxd_remote": remote, "lxd_container": container},
            ["@lxd_api"],
        )

    def _target(self) -> str:
        return f"{self.host.data.lxd_remote}:{self.host.data.lxd_container}"

    @override
    def connect(self) -> None:
        remote = self.host.data.lxd_remote
        container = self.host.data.lxd_container

        base_url, ssl_ctx = _load_remote(remote)
        self.base_url = base_url
        self.client = httpx.Client(
            base_url=base_url,
            verify=ssl_ctx,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0),
        )

        try:
            r = self.client.get("/1.0")
            r.raise_for_status()
        except httpx.HTTPError as e:
            self.disconnect()
            raise ConnectError(f"GET {base_url}/1.0 failed: {e}") from e

        meta = r.json().get("metadata", {})
        if meta.get("auth") != "trusted":
            self.disconnect()
            raise ConnectError(
                f"LXD at {base_url} did not trust the client cert "
                f"(auth={meta.get('auth')!r}); run `lxc remote add` to "
                f"register this cert with the cluster."
            )
        missing = REQUIRED_API_EXTENSIONS - set(meta.get("api_extensions", []))
        if missing:
            self.disconnect()
            raise ConnectError(
                f"LXD server is missing required API extensions: {sorted(missing)}"
            )

        try:
            r = self.client.get(f"/1.0/instances/{container}")
        except httpx.HTTPError as e:
            self.disconnect()
            raise ConnectError(f"GET /1.0/instances/{container} failed: {e}") from e
        if r.status_code == 404:
            self.disconnect()
            raise ConnectError(f"LXD container {self._target()} does not exist")
        r.raise_for_status()
        status = r.json().get("metadata", {}).get("status")
        if status != "Running":
            self.disconnect()
            raise ConnectError(
                f"LXD container {self._target()} is not running (status={status!r})"
            )

    @override
    def disconnect(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def _operation_wait(self, op_id: str) -> dict:
        assert self.client is not None
        r = self.client.get(f"/1.0/operations/{op_id}/wait")
        r.raise_for_status()
        return r.json().get("metadata", {})

    @override
    def run_shell_command(
        self,
        command: "StringCommand",
        print_output: bool = False,
        print_input: bool = False,
        **arguments: Unpack["ConnectorArguments"],
    ) -> tuple[bool, CommandOutput]:
        if arguments.get("_get_pty"):
            raise NotImplementedError(
                "@lxd_api does not support PTY/interactive sessions; "
                "the record-output API path is non-interactive only."
            )

        assert self.client is not None
        # Drain control args (sudo/etc) into the rendered command via
        # make_unix_command_for_host — same pattern as the SSH connector.
        extract_control_arguments(arguments)
        container = self.host.data.lxd_container

        unix_cmd = make_unix_command_for_host(
            self.state, self.host, command, **arguments
        )
        cmd_str = unix_cmd.get_raw_value()

        if print_input:
            click.echo(f"{self.host.print_prefix}>>> sh -c {cmd_str!r}", err=True)

        body = {
            "command": ["sh", "-c", cmd_str],
            "wait-for-websocket": False,
            "record-output": True,
            "interactive": False,
        }

        try:
            r = self.client.post(
                f"/1.0/instances/{container}/exec",
                json=body,
            )
            r.raise_for_status()
            op_id = r.json()["metadata"]["id"]
            op = self._operation_wait(op_id)
        except httpx.HTTPError as e:
            raise ConnectError(f"exec API call failed: {e}") from e

        op_meta = op.get("metadata") or {}
        return_code = op_meta.get("return", -1)
        outputs = op_meta.get("output") or {}

        stdout = self._fetch_output(outputs.get("1"))
        stderr = self._fetch_output(outputs.get("2"))

        if print_output:
            for line in stdout.splitlines():
                click.echo(f"{self.host.print_prefix}{line}", err=True)
            for line in stderr.splitlines():
                click.echo(f"{self.host.print_prefix}{line}", err=True)

        combined = [
            *(OutputLine("stdout", line) for line in stdout.splitlines()),
            *(OutputLine("stderr", line) for line in stderr.splitlines()),
        ]
        return return_code == 0, CommandOutput(combined)

    def _fetch_output(self, url: str | None) -> str:
        if not url:
            return ""
        assert self.client is not None
        r = self.client.get(url)
        r.raise_for_status()
        return r.text

    @override
    def put_file(
        self,
        filename_or_io,
        remote_filename,
        remote_temp_filename=None,  # ignored
        print_output=False,
        print_input=False,
        **kwargs,  # ignored (sudo/etc)
    ) -> bool:
        """Upload via `POST /1.0/instances/{name}/files`.

        Sets uid=0, gid=0, mode=0644 explicitly. Without these the
        existing local file's metadata leaks across the container
        boundary and breaks reads for any service running as a
        non-root user. pyinfra's downstream chmod / chown ops adjust
        further when the caller specified intent.
        """
        assert self.client is not None
        container = self.host.data.lxd_container

        with get_file_io(filename_or_io) as file_io:
            data = file_io.read()
            if isinstance(data, str):
                data = data.encode()

        try:
            r = self.client.post(
                f"/1.0/instances/{container}/files",
                params={"path": remote_filename},
                headers={
                    "X-LXD-uid": "0",
                    "X-LXD-gid": "0",
                    "X-LXD-mode": "0644",
                    "X-LXD-type": "file",
                },
                content=data,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise IOError(f"failed to put_file({remote_filename}): {e}") from e

        if print_output:
            click.echo(
                f"{self.host.print_prefix}file uploaded to container: {remote_filename}",
                err=True,
            )
        return True

    @override
    def get_file(
        self,
        remote_filename,
        filename_or_io,
        remote_temp_filename=None,  # ignored
        print_output=False,
        print_input=False,
        **kwargs,  # ignored (sudo/etc)
    ) -> bool:
        """Download via `GET /1.0/instances/{name}/files`."""
        assert self.client is not None
        container = self.host.data.lxd_container

        try:
            r = self.client.get(
                f"/1.0/instances/{container}/files",
                params={"path": remote_filename},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise IOError(f"failed to get_file({remote_filename}): {e}") from e

        with get_file_io(filename_or_io, "wb") as file_io:
            file_io.write(r.content)

        if print_output:
            click.echo(
                f"{self.host.print_prefix}file downloaded from container: {remote_filename}",
                err=True,
            )
        return True
