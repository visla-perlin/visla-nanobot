"""Visla SSO token exchange for the WebUI gateway."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from nanobot.channels.websocket.runtime import WebSocketConfig
from nanobot.webui.gateway_tokens import GatewayTokenStore
from nanobot.webui.visla_auth import (
    VislaAttemptLimiter,
    VislaAuthRejectedError,
    VislaAuthUnavailableError,
    VislaUser,
    validate_visla_token,
)
from nanobot.webui.ws_http import GatewayHTTPHandler

_VISLA_OK_PAYLOAD = {
    "code": 0,
    "msg": "SUCCESS",
    "data": {
        "id": 26,
        "email": "perlin.gan@visla.us",
        "userName": "perlin.gan",
        "type": "org_admin",
        "status": "active",
    },
}


def _visla_transport(responder) -> httpx.MockTransport:
    return httpx.MockTransport(responder)


def _remote_request(path: str, headers: dict[str, str] | None = None) -> Request:
    merged = {"Host": "gateway.example:8765"}
    if headers:
        merged.update(headers)
    return Request(path, Headers(merged))


def _exchange_handler(
    *,
    enabled: bool = True,
    max_attempts: int = 10,
) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = WebSocketConfig(
        token_issue_secret="fixture-secret",
        visla_auth_enabled=enabled,
    )
    handler.tokens = GatewayTokenStore()
    handler._visla_attempts = VislaAttemptLimiter(max_attempts=max_attempts)
    handler._log = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        debug=lambda *a, **k: None,
    )
    return handler


# -- validate_visla_token -----------------------------------------------------


async def test_validate_visla_token_success_returns_user():
    seen: dict[str, str] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token_header"] = request.headers["token"]
        return httpx.Response(200, json=_VISLA_OK_PAYLOAD)

    user = await validate_visla_token(
        "https://visla.example/api/my/current-user",
        "visla-jwt",
        transport=_visla_transport(responder),
    )
    assert user == VislaUser(
        id=26,
        email="perlin.gan@visla.us",
        user_name="perlin.gan",
        type="org_admin",
        status="active",
    )
    assert seen["token_header"] == "visla-jwt"
    assert seen["url"] == "https://visla.example/api/my/current-user"


@pytest.mark.parametrize(
    "payload",
    [
        {**_VISLA_OK_PAYLOAD, "code": 1, "msg": "ERROR"},
        {**_VISLA_OK_PAYLOAD, "data": {**_VISLA_OK_PAYLOAD["data"], "status": "disabled"}},
        {**_VISLA_OK_PAYLOAD, "data": {**_VISLA_OK_PAYLOAD["data"], "email": ""}},
        {**_VISLA_OK_PAYLOAD, "data": {"id": 26}},
        {"code": 0},
    ],
)
async def test_validate_visla_token_rejects_bad_payloads(payload):
    transport = _visla_transport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(VislaAuthRejectedError):
        await validate_visla_token("https://visla.example/api", "visla-jwt", transport=transport)


async def test_validate_visla_token_rejects_upstream_401():
    transport = _visla_transport(lambda request: httpx.Response(401, json={"code": 401}))
    with pytest.raises(VislaAuthRejectedError):
        await validate_visla_token("https://visla.example/api", "bad", transport=transport)


async def test_validate_visla_token_unavailable_on_server_error():
    transport = _visla_transport(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(VislaAuthUnavailableError):
        await validate_visla_token("https://visla.example/api", "visla-jwt", transport=transport)


async def test_validate_visla_token_unavailable_on_connect_error():
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    transport = _visla_transport(responder)
    with pytest.raises(VislaAuthUnavailableError):
        await validate_visla_token("https://visla.example/api", "visla-jwt", transport=transport)


# -- /webui/auth/visla endpoint ------------------------------------------------


async def test_visla_exchange_disabled_returns_404():
    handler = _exchange_handler(enabled=False)
    connection = SimpleNamespace(remote_address=("10.0.0.8", 5000))
    request = _remote_request("/webui/auth/visla", {"X-Nanobot-Auth": "visla-jwt"})
    response = await handler._handle_visla_auth(connection, request)
    assert response.status_code == 404
    assert not handler.tokens.issued_tokens


async def test_visla_exchange_requires_token():
    handler = _exchange_handler()
    connection = SimpleNamespace(remote_address=("10.0.0.8", 5000))
    response = await handler._handle_visla_auth(connection, _remote_request("/webui/auth/visla"))
    assert response.status_code == 401


async def test_visla_exchange_issues_one_shot_bootstrap_token(monkeypatch):
    async def fake_validate(url: str, token: str) -> VislaUser:
        assert token == "visla-jwt"
        return VislaUser(id=26, email="e@x", user_name="u", type="org_admin", status="active")

    handler = _exchange_handler()
    monkeypatch.setattr("nanobot.webui.ws_http.validate_visla_token", fake_validate)
    connection = SimpleNamespace(remote_address=("10.0.0.8", 5000))
    request = _remote_request("/webui/auth/visla", {"X-Nanobot-Auth": "visla-jwt"})
    response = await handler._handle_visla_auth(connection, request)
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["token"] in handler.tokens.issued_tokens
    assert handler.tokens.peek_issued_token_audience(body["token"]) == "bootstrap"
    assert body["expires_in"] == handler.config.visla_exchange_ttl_s
    # One-shot: consuming the exchange token leaves nothing behind.
    assert handler.tokens.take_issued_token_audience(body["token"]) == "bootstrap"
    assert handler.tokens.peek_issued_token_audience(body["token"]) is None


async def test_visla_exchange_rejects_invalid_token(monkeypatch):
    async def fake_validate(url: str, token: str) -> VislaUser:
        raise VislaAuthRejectedError("nope")

    handler = _exchange_handler()
    monkeypatch.setattr("nanobot.webui.ws_http.validate_visla_token", fake_validate)
    connection = SimpleNamespace(remote_address=("10.0.0.8", 5000))
    request = _remote_request("/webui/auth/visla", {"X-Nanobot-Auth": "bad-token"})
    response = await handler._handle_visla_auth(connection, request)
    assert response.status_code == 401
    assert not handler.tokens.issued_tokens


async def test_visla_exchange_rate_limited():
    handler = _exchange_handler(max_attempts=2)
    connection = SimpleNamespace(remote_address=("10.0.0.8", 5000))
    for _ in range(2):
        response = await handler._handle_visla_auth(
            connection, _remote_request("/webui/auth/visla")
        )
        assert response.status_code == 401
    response = await handler._handle_visla_auth(connection, _remote_request("/webui/auth/visla"))
    assert response.status_code == 429


async def test_visla_methods_reports_enabled_state():
    handler = _exchange_handler(enabled=True)
    assert json.loads(handler._handle_visla_methods().body) == {"visla": True}
    handler.config.visla_auth_enabled = False
    assert json.loads(handler._handle_visla_methods().body) == {"visla": False}


# -- bootstrap accepts the exchange token ---------------------------------------


def _bootstrap_handler() -> GatewayHTTPHandler:
    handler = _exchange_handler()
    handler.ingress = SimpleNamespace(bootstrap_limits=lambda **_: {"transport": {}})
    handler.settings = SimpleNamespace(config=SimpleNamespace(path=Path("~/.nanobot/config.json")))
    handler.runtime_model_name = lambda: "test-model"
    handler._runtime_surface = "browser"
    handler._capabilities = {}
    return handler


def _bootstrap_with(handler: GatewayHTTPHandler, headers: dict[str, str] | None):
    request = _remote_request("/webui/bootstrap", headers)
    connection = SimpleNamespace(remote_address=("10.0.0.8", 5000))
    return handler._handle_bootstrap(connection, request)


async def test_bootstrap_accepts_one_shot_visla_exchange_token(monkeypatch):
    async def fake_validate(url: str, token: str) -> VislaUser:
        return VislaUser(id=26, email="e@x", user_name="u", type="org_admin", status="active")

    handler = _bootstrap_handler()
    monkeypatch.setattr("nanobot.webui.ws_http.validate_visla_token", fake_validate)
    connection = SimpleNamespace(remote_address=("10.0.0.8", 5000))
    exchange_response = await handler._handle_visla_auth(
        connection,
        _remote_request("/webui/auth/visla", {"X-Nanobot-Auth": "visla-jwt"}),
    )
    exchange = json.loads(exchange_response.body)["token"]

    response = _bootstrap_with(handler, {"X-Nanobot-Auth": exchange})
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["token"]  # webui WS token
    assert body["api_token"]  # HTTP API token is issued for the visla path too
    assert "no-store" in response.headers["Cache-Control"]

    # The exchange token is spent: replaying it must fail.
    replay = _bootstrap_with(handler, {"X-Nanobot-Auth": exchange})
    assert replay.status_code == 401


def test_bootstrap_rejects_wrong_secret_without_exchange():
    handler = _bootstrap_handler()
    response = _bootstrap_with(handler, {"X-Nanobot-Auth": "not-the-secret"})
    assert response.status_code == 401
    response = _bootstrap_with(handler, None)
    assert response.status_code == 401


def test_bootstrap_client_token_is_not_burned_by_bootstrap_attempt():
    handler = _bootstrap_handler()
    client_token = handler.tokens.issue_token(60, audience="client")
    attempt = _bootstrap_with(handler, {"X-Nanobot-Auth": client_token})
    # A non-bootstrap audience must fail bootstrap without being consumed.
    assert attempt.status_code == 401
    assert handler.tokens.peek_issued_token_audience(client_token) == "client"


async def test_terminal_probe_peeks_without_consuming_exchange_token(monkeypatch):
    async def fake_validate(url: str, token: str) -> VislaUser:
        return VislaUser(id=26, email="e@x", user_name="u", type="org_admin", status="active")

    handler = _bootstrap_handler()
    monkeypatch.setattr("nanobot.webui.ws_http.validate_visla_token", fake_validate)
    connection = SimpleNamespace(remote_address=("10.0.0.8", 5000))
    exchange_response = await handler._handle_visla_auth(
        connection,
        _remote_request("/webui/auth/visla", {"X-Nanobot-Auth": "visla-jwt"}),
    )
    exchange = json.loads(exchange_response.body)["token"]

    probe_request = _remote_request("/webui/terminal", {"X-Nanobot-Auth": exchange})
    for _ in range(2):
        probe = handler._handle_bootstrap(connection, probe_request, terminal_probe=True)
        assert probe.status_code == 200
    assert handler.tokens.peek_issued_token_audience(exchange) == "bootstrap"
