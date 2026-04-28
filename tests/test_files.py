"""`put_file` / `get_file` — direct API uploads/downloads."""

from __future__ import annotations

import io

import httpx
import pytest

from pyinfra_lxd_api_connector import LxdApiConnector

from .conftest import FakeHost, make_mock_client


def make_conn(handler) -> LxdApiConnector:
    """A connector instance with its httpx client wired to `handler`.

    Skips `connect()` — file methods don't need the capability probe
    or container-status check.
    """
    conn = LxdApiConnector.__new__(LxdApiConnector)
    conn.state = None
    conn.host = FakeHost()
    conn.client = make_mock_client(handler)
    conn.base_url = "https://test.invalid"
    return conn


def test_put_file_sets_root_owner_and_644():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query_path"] = request.url.params["path"]
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200)

    conn = make_conn(handler)
    payload = b"hello"
    ok = conn.put_file(io.BytesIO(payload), "/etc/foo")

    assert ok is True
    assert captured["path"] == "/1.0/instances/testc/files"
    assert captured["query_path"] == "/etc/foo"
    assert captured["headers"]["x-lxd-uid"] == "0"
    assert captured["headers"]["x-lxd-gid"] == "0"
    assert captured["headers"]["x-lxd-mode"] == "0644"
    assert captured["headers"]["x-lxd-type"] == "file"
    assert captured["body"] == payload


def test_put_file_handles_string_input():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200)

    conn = make_conn(handler)
    conn.put_file(io.StringIO("hello, str"), "/tmp/x")
    assert captured["body"] == b"hello, str"


def test_put_file_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    conn = make_conn(handler)
    with pytest.raises(IOError, match="put_file"):
        conn.put_file(io.BytesIO(b"x"), "/tmp/x")


def test_get_file_returns_content():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1.0/instances/testc/files"
        assert request.url.params["path"] == "/etc/hostname"
        return httpx.Response(200, content=b"the-host\n")

    conn = make_conn(handler)
    buf = io.BytesIO()
    ok = conn.get_file("/etc/hostname", buf)
    assert ok is True
    assert buf.getvalue() == b"the-host\n"


def test_get_file_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    conn = make_conn(handler)
    with pytest.raises(IOError, match="get_file"):
        conn.get_file("/etc/missing", io.BytesIO())
