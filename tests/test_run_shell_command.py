"""Retry behaviour of `run_shell_command`'s `POST /exec` path."""

from __future__ import annotations

import httpx
import pytest
from pyinfra.api.command import StringCommand
from pyinfra.api.exceptions import ConnectError

import pyinfra_lxd_api_connector as mod
from pyinfra_lxd_api_connector import LxdApiConnector

from .conftest import FakeHost, make_mock_client


class _FakeHostWithConnectorData(FakeHost):
    def __init__(self, **kwargs):
        super().__init__()
        self.connector_data: dict = {}
        for key, value in kwargs.items():
            setattr(self.data, key, value)


def make_conn(handler, **host_data) -> LxdApiConnector:
    conn = LxdApiConnector.__new__(LxdApiConnector)
    conn.state = None
    conn.host = _FakeHostWithConnectorData(**host_data)
    conn.client = make_mock_client(handler)
    conn.base_url = "https://test.invalid"
    return conn


@pytest.fixture(autouse=True)
def stub_unix_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`make_unix_command_for_host` needs a real pyinfra Host; stub it.

    The retry behaviour under test is at the HTTP layer — what
    pyinfra renders into the shell command string is orthogonal.
    """
    monkeypatch.setattr(
        mod,
        "make_unix_command_for_host",
        lambda state, host, command, **kwargs: command,
    )
    monkeypatch.setattr(mod, "extract_control_arguments", lambda args: args)


def _exec_handler(exec_responses, op_meta=None):
    """Build a handler that walks `POST /exec` through the response list,
    then answers the `GET /operations/.../wait` poll with op_meta."""
    op_meta = op_meta or {"return": 0, "output": {}}
    state = {"exec_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/exec"):
            idx = state["exec_calls"]
            state["exec_calls"] += 1
            r = exec_responses[idx]
            if isinstance(r, Exception):
                raise r
            return r
        if "/operations/" in request.url.path:
            return httpx.Response(
                200, json={"metadata": {"metadata": op_meta}}
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    handler.state = state  # type: ignore[attr-defined]
    return handler


def test_exec_default_does_not_retry_5xx(no_sleep):
    """Default `connect_only` policy must not retry 5xx on POST /exec."""
    handler = _exec_handler([httpx.Response(503, json={})])
    conn = make_conn(handler)
    with pytest.raises(ConnectError, match="exec API call failed"):
        conn.run_shell_command(StringCommand("echo", "hi"))
    assert handler.state["exec_calls"] == 1  # type: ignore[attr-defined]


def test_exec_default_does_not_retry_read_error(no_sleep):
    """Default `connect_only` policy must not retry ReadError on POST /exec."""
    handler = _exec_handler([httpx.ReadError("server reset mid-read")])
    conn = make_conn(handler)
    with pytest.raises(ConnectError, match="exec API call failed"):
        conn.run_shell_command(StringCommand("echo", "hi"))
    assert handler.state["exec_calls"] == 1  # type: ignore[attr-defined]


def test_exec_default_retries_connect_error(no_sleep):
    """Default policy retries connect-class errors — request never reached server."""
    handler = _exec_handler(
        [
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            httpx.Response(200, json={"metadata": {"id": "op-1"}}),
        ]
    )
    conn = make_conn(handler)
    ok, _ = conn.run_shell_command(StringCommand("echo", "hi"))
    assert ok is True
    assert handler.state["exec_calls"] == 3  # type: ignore[attr-defined]


def test_exec_with_opt_in_retries_5xx(no_sleep):
    """With `lxd_exec_retry_on_read_errors=True`, POST /exec retries 5xx."""
    handler = _exec_handler(
        [
            httpx.Response(503, json={}),
            httpx.Response(200, json={"metadata": {"id": "op-1"}}),
        ]
    )
    conn = make_conn(handler, lxd_exec_retry_on_read_errors=True)
    ok, _ = conn.run_shell_command(StringCommand("echo", "hi"))
    assert ok is True
    assert handler.state["exec_calls"] == 2  # type: ignore[attr-defined]
