# pyinfra-lxd-api-connector

A pyinfra connector that targets LXD containers via the LXD HTTPS API directly — no SSH hop, no paramiko, no `lxc` CLI subprocess, no websockets. One kept-alive HTTPS connection per host, exec via the [`container_exec_recording`](https://documentation.ubuntu.com/lxd/en/latest/api-extensions/#container-exec-recording) extension, file transfers via the native files API.

## Why?

This is the fast sibling of [pyinfra-lxd-local-connector](../pyinfra-lxd-local-connector/), which shells out to the `lxc` CLI for every command. The CLI approach pays a per-command cost of ~6–7 fresh TCP+TLS connections (capabilities probe + events websocket + exec POST + 4× stdio websockets + operation poll) — measured at **~870 ms per command** over Tailscale from a remote laptop.

Talking to the API directly with `record-output: true` mode collapses all of that to a single kept-alive HTTPS connection with **zero websockets**. Measured at **~150 ms per `run_shell_command`** from the same vantage point — ~5–6× faster, and within the same order of magnitude as warm SSH-multiplex.

See [microcloud#206](https://github.com/imusic-dk/microcloud/issues/206) for the diagnosis. Tracks pyinfra issue [#677](https://github.com/pyinfra-dev/pyinfra/issues/677); intended as upstream PR material.

## Performance

Per-call latency over Tailscale from a remote laptop (~27 ms RTT to the cluster), measured via `smoke_test.py` against a real container:

| Operation | Wall time |
|---|---|
| `connect()` (cold TLS + capability probe + container check) | ~335 ms |
| `run_shell_command` (warm, kept-alive) | ~130–260 ms |
| `put_file` (small payload) | ~80 ms |
| `get_file` (small payload) | ~30 ms |

For comparison, `@lxd_local` (CLI-subprocess) pays ~867 ms per `run_shell_command` from the same vantage point. From a node inside the cluster, both connectors are fast enough that the difference doesn't matter.

## Install

```fish
uv tool install pyinfra --with pyinfra-lxd-api-connector
```

## Usage

Prereq: an `lxc remote` configured locally:

```fish
lxc remote add microcloud https://your-cluster:8443 --token <token>
lxc list microcloud:        # verify
```

The connector reads the standard LXD client config at `~/.config/lxc/`:

- `config.yml` — remote URL
- `client.crt` + `client.key` — mTLS client identity
- `servercerts/<remote>.crt` — pinned server cert

Inventory:

```python
hosts = [
    "@lxd_api/mycluster:php01",
    "@lxd_api/some-other-cluster:web1",
]
```

Hosts must be qualified as `@lxd_api/<remote>:<container>`. The remote name must match an entry in `lxc remote list`.

## Requirements

- LXD server with the `container_exec_recording` API extension (LXD 5.0+).
- Local LXD client config at `~/.config/lxc/`. `lxc remote add` sets all of this up.

## Status

Alpha. Built for use against a 32-container production cluster. Feedback / bug reports welcome.

## Known limitations

To be addressed before opening the upstream PR for [pyinfra-dev/pyinfra#677](https://github.com/pyinfra-dev/pyinfra/issues/677):

- **`connect()` is too strict on transient LXD API errors** — should retry with backoff before failing. Tracked in [microcloud#207](https://github.com/imusic-dk/microcloud/issues/207).
- **No interactive / PTY support** — the connector raises `NotImplementedError` if `_get_pty=True`. pyinfra never needs PTY for facts/operations, so this is fine in practice; if you need an interactive shell, use `lxc shell` directly.
- **Per-command stdout/stderr is buffered, not streamed** — `record-output` mode means output arrives at the end of the command. For pyinfra's typical workload (facts and one-shot operations) this is invisible; for long-running commands you won't see live progress.
