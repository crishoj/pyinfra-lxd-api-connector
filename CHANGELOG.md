# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-04-28

First public release.

### Added

- `@lxd_api` pyinfra connector — talks directly to the LXD HTTPS API via `httpx`, with no SSH hop, no `lxc` CLI subprocess, and no exec websockets.
- `record-output: true` exec mode — collapses pyinfra's per-command cost from ~6–7 fresh TCP+TLS handshakes (~870 ms) to a single kept-alive HTTPS connection (~150 ms) measured from off-cluster.
- Inventory parsing — `@lxd_api/<remote>:<container>` (explicit) and `@lxd_api/<container>` (resolves the remote via the `default-remote` field in `~/.config/lxc/config.yml`, mirroring `lxc`'s own behavior).
- File transfer via the native LXD files API (`POST` / `GET /1.0/instances/{c}/files`), with `uid=0`, `gid=0`, `mode=0644` defaults to avoid leaking host-side metadata.
- Transient-error retry in `connect()` — three attempts with exponential backoff (0.5s → 1s → 2s) on `httpx.RequestError` and 5xx responses; permanent 4xx fails fast.
- mTLS via the standard LXD client config (`~/.config/lxc/`) — same trust model the `lxc` CLI uses, with the server cert pinned via `cafile` and hostname verification disabled (LXD self-signed certs typically lack a SAN matching the cluster hostname).

### Known limitations

- No interactive / PTY support — `_get_pty=True` raises `NotImplementedError`. pyinfra never needs PTY for facts/operations.
- Per-command stdout/stderr is buffered, not streamed — `record-output` mode delivers output at command completion.
- Run-time HTTP calls (`run_shell_command`, `put_file`, `get_file`) don't retry on transient errors. Tracked in [#2](https://github.com/crishoj/pyinfra-lxd-api-connector/issues/2).
- No unix-socket transport for local LXD — currently requires an `https://` remote. Tracked in [#1](https://github.com/crishoj/pyinfra-lxd-api-connector/issues/1).

[Unreleased]: https://github.com/crishoj/pyinfra-lxd-api-connector/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/crishoj/pyinfra-lxd-api-connector/releases/tag/v0.1.0
