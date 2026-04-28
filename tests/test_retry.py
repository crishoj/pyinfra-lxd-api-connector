"""Transient-retry behaviour of `_retrying_get`."""

from __future__ import annotations

import httpx
import pytest

from pyinfra_lxd_api_connector import (
    CONNECT_RETRY_ATTEMPTS,
    _retrying_get,
)

from .conftest import make_mock_client


def test_returns_immediately_on_2xx(no_sleep):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    client = make_mock_client(handler)
    response = _retrying_get(client, "/1.0", label="test")
    assert response.status_code == 200
    assert len(calls) == 1


def test_returns_immediately_on_4xx(no_sleep):
    """4xx is permanent; do not retry."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404, json={"error": "no such thing"})

    client = make_mock_client(handler)
    response = _retrying_get(client, "/1.0/instances/missing", label="test")
    assert response.status_code == 404
    assert len(calls) == 1, "4xx must not be retried"


def test_retries_on_5xx_then_succeeds(no_sleep):
    statuses = [503, 502, 200]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses[len(calls)]
        calls.append(status)
        return httpx.Response(status, json={})

    client = make_mock_client(handler)
    response = _retrying_get(client, "/1.0", label="test")
    assert response.status_code == 200
    assert calls == [503, 502, 200]


def test_retries_on_request_error_then_succeeds(no_sleep):
    state = {"failures_left": 2}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["failures_left"] > 0:
            state["failures_left"] -= 1
            raise httpx.ConnectError("kaboom")
        return httpx.Response(200, json={"ok": True})

    client = make_mock_client(handler)
    response = _retrying_get(client, "/1.0", label="test")
    assert response.status_code == 200


def test_exhausts_retries_on_persistent_5xx(no_sleep):
    """After CONNECT_RETRY_ATTEMPTS attempts, return the last 5xx response."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, json={})

    client = make_mock_client(handler)
    response = _retrying_get(client, "/1.0", label="test")
    assert response.status_code == 503
    assert len(calls) == CONNECT_RETRY_ATTEMPTS


def test_exhausts_retries_on_persistent_request_error(no_sleep):
    """After CONNECT_RETRY_ATTEMPTS attempts, re-raise the RequestError."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectError("kaboom")

    client = make_mock_client(handler)
    with pytest.raises(httpx.ConnectError):
        _retrying_get(client, "/1.0", label="test")
    assert len(calls) == CONNECT_RETRY_ATTEMPTS
