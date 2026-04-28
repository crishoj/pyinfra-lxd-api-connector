# /// script
# requires-python = ">=3.10"
# dependencies = ["pyinfra>=3,<4", "httpx>=0.27", "pyyaml>=6"]
# ///
"""Smoke test for the @lxd_api connector against a live container.

Run with: `uv run smoke_test.py [<remote>:<container>]`
Defaults to `microcloud:worker`.

Exercises run_shell_command, put_file, get_file. Times each call so
you can verify the kept-alive connection actually amortizes the
TLS handshake the way the README claims.
"""
import io
import sys
import time

# Allow `uv run` from anywhere, and direct `python smoke_test.py` from this dir.
sys.path.insert(0, ".")

from pyinfra_lxd_api_connector import LxdApiConnector  # noqa: E402


class FakeHostData:
    def __init__(self, remote: str, container: str):
        self.lxd_remote = remote
        self.lxd_container = container


class FakeHost:
    def __init__(self, remote: str, container: str):
        self.data = FakeHostData(remote, container)
        self.print_prefix = f"[{remote}:{container}] "


def time_call(label, fn):
    t0 = time.perf_counter()
    result = fn()
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"{label:40s} {elapsed:7.1f} ms")
    return result


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "microcloud:worker"
    remote, container = target.split(":", 1)

    host = FakeHost(remote, container)
    conn = LxdApiConnector.__new__(LxdApiConnector)
    conn.state = None
    conn.host = host
    conn.client = None
    conn.base_url = ""

    print(f"=== smoke test: @lxd_api/{target} ===\n")

    time_call("connect (first TLS handshake)", conn.connect)

    from pyinfra.api.command import StringCommand

    def run(cmd_str):
        ok, out = conn.run_shell_command(StringCommand(cmd_str))
        return ok, out

    print("\n--- run_shell_command timings (warm) ---")
    for i in range(5):
        ok, out = time_call(f"  whoami #{i+1}", lambda: run("whoami"))
        assert ok and out.stdout.strip() == "root", f"unexpected: {ok!r} {out.stdout!r}"

    print("\n--- exit code propagation ---")
    ok, out = time_call("  /bin/false", lambda: run("/bin/false"))
    assert not ok, f"expected failure, got {ok!r}"
    print(f"  → ok={ok}, stderr={out.stderr!r}")

    print("\n--- stderr capture ---")
    ok, out = time_call("  echo hello >&2", lambda: run("echo hello >&2; echo world"))
    assert ok
    assert "hello" in out.stderr, f"missing stderr: {out.stderr!r}"
    assert "world" in out.stdout, f"missing stdout: {out.stdout!r}"
    print(f"  → stdout={out.stdout!r} stderr={out.stderr!r}")

    print("\n--- put_file / get_file ---")
    payload = b"hello from smoke_test " + str(time.time()).encode() + b"\n"
    target_path = "/tmp/lxd_api_smoke.txt"

    time_call(
        "  put_file (POST /files)",
        lambda: conn.put_file(io.BytesIO(payload), target_path),
    )

    ok, out = time_call(
        "  verify ls -l /tmp/...",
        lambda: run(f"ls -l {target_path}"),
    )
    assert ok, f"ls failed: {out.stderr!r}"
    print(f"  → {out.stdout.strip()}")
    assert "-rw-r--r--" in out.stdout, f"expected mode 0644: {out.stdout!r}"
    assert " root root " in out.stdout, f"expected root:root ownership: {out.stdout!r}"

    buf = io.BytesIO()
    time_call(
        "  get_file (GET /files)",
        lambda: conn.get_file(target_path, buf),
    )
    assert buf.getvalue() == payload, f"roundtrip mismatch: {buf.getvalue()!r} != {payload!r}"
    print(f"  → roundtrip OK ({len(payload)} bytes)")

    run(f"rm -f {target_path}")
    conn.disconnect()
    print("\n=== all checks passed ===")


if __name__ == "__main__":
    main()
