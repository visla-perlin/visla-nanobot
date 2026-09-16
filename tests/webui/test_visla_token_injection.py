"""Per-conversation VISLA_TOKEN injection: store → bootstrap binding → exec env."""

import json
from pathlib import Path
from types import SimpleNamespace

from websockets.datastructures import Headers
from websockets.http11 import Request

from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.shell import ExecTool
from nanobot.channels.websocket.runtime import WebSocketConfig
from nanobot.webui.gateway_tokens import GatewayTokenStore
from nanobot.webui.visla_auth import VislaTokenStore
from nanobot.webui.ws_http import GatewayHTTPHandler


def test_visla_token_store_rotates_and_expires_empty():
    store = VislaTokenStore()
    assert store.latest("u1") == ""
    store.put("u1", "jwt-1")
    assert store.latest("u1") == "jwt-1"
    store.put("u1", "jwt-2")  # rotation keeps only the newest token
    assert store.latest("u1") == "jwt-2"
    store.put("", "jwt-x")  # empty user id / token are ignored
    store.put("u2", "")
    assert store.latest("u2") == ""


def test_issue_token_binds_and_consumes_visla_user():
    tokens = GatewayTokenStore()
    bootstrap = tokens.issue_token(60, audience="bootstrap", visla_user_id="42")
    assert tokens.take_issued_visla_user(bootstrap) == "42"
    # One-shot: the binding is gone after the first consume.
    assert tokens.take_issued_visla_user(bootstrap) is None
    assert tokens.take_issued_visla_user("nbwt_unknown") is None


def test_issue_token_without_visla_user_has_no_binding():
    tokens = GatewayTokenStore()
    bootstrap = tokens.issue_token(60, audience="client")
    assert tokens.take_issued_visla_user(bootstrap) is None


def test_endpoint_binds_connection_to_visla_token():
    tokens = GatewayTokenStore()
    endpoint = SimpleNamespace(
        connection_visla_users={},
        take=lambda t: tokens.take_issued_visla_user(t),
        store=tokens.visla_tokens,
    )
    store: VislaTokenStore = endpoint.store
    store.put("42", "jwt-live")
    bootstrap = tokens.issue_token(60, audience="bootstrap", visla_user_id="42")
    user_id = endpoint.take(bootstrap)
    assert user_id == "42"
    endpoint.connection_visla_users["conn-1"] = user_id
    assert store.latest(endpoint.connection_visla_users["conn-1"]) == "jwt-live"
    # Unbound connection resolves to no token.
    assert store.latest(endpoint.connection_visla_users.get("conn-2")) == ""


def test_build_env_injects_visla_token_over_static():
    ctx = RequestContext(
        channel="websocket",
        chat_id="c1",
        metadata={"visla_token": "jwt-live"},
    )
    token = bind_request_context(ctx)
    try:
        tool = ExecTool()
        env = tool._build_env()
        assert env["VISLA_TOKEN"] == "jwt-live"
    finally:
        reset_request_context(token)


def test_build_env_visla_token_absent_keeps_minimal_env():
    token = bind_request_context(RequestContext(channel="websocket", chat_id="c1", metadata={}))
    try:
        env = ExecTool()._build_env()
        assert "VISLA_TOKEN" not in env
    finally:
        reset_request_context(token)


# -- _handle_bootstrap end-to-end ---------------------------------------------


def _bootstrap_handler() -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = WebSocketConfig(token_issue_secret="fixture-secret")
    handler.tokens = GatewayTokenStore()
    handler.ingress = SimpleNamespace(
        bootstrap_limits=lambda max_frame_bytes: {"maxFrameBytes": max_frame_bytes}
    )
    handler.runtime_model_name = "test-model"
    handler.settings = SimpleNamespace(config=SimpleNamespace(path=Path("/tmp/fixture")))
    handler._runtime_surface = {}
    handler._capabilities = {}
    return handler


def _bootstrap_request(credential: str) -> Request:
    return Request(
        "/webui/bootstrap",
        Headers({"Host": "127.0.0.1:8765", "X-Nanobot-Auth": credential}),
    )


async def test_bootstrap_carries_visla_binding_into_webui_token():
    """The binding must survive the exchange → bootstrap → WS handshake chain.

    Regression test: _handle_bootstrap used to drop the visla_user binding when
    re-issuing the webui token, so connections were never bound and VISLA_TOKEN
    was never injected.
    """
    handler = _bootstrap_handler()
    handler.tokens.visla_tokens.put("42", "jwt-user-42")
    bootstrap = handler.tokens.issue_token(60, audience="bootstrap", visla_user_id="42")

    connection = SimpleNamespace(remote_address=("127.0.0.1", 10000))
    response = handler._handle_bootstrap(connection, _bootstrap_request(bootstrap))
    assert response.status_code == 200
    webui_token = json.loads(response.body)["token"]

    # What the WS handshake would do (consume_issued_token):
    visla_user = handler.tokens.take_issued_visla_user(webui_token)
    assert visla_user == "42"
    assert handler.tokens.visla_tokens.latest(visla_user) == "jwt-user-42"


async def test_bootstrap_multi_user_binding_stays_isolated():
    """Two users each bootstrap on their own connection; no cross-binding."""
    handler = _bootstrap_handler()
    tokens = handler.tokens
    tokens.visla_tokens.put("1", "jwt-A")
    tokens.visla_tokens.put("2", "jwt-B")

    boot_a = tokens.issue_token(60, audience="bootstrap", visla_user_id="1")
    boot_b = tokens.issue_token(60, audience="bootstrap", visla_user_id="2")
    resp_a = handler._handle_bootstrap(
        SimpleNamespace(remote_address=("10.0.0.1", 1)), _bootstrap_request(boot_a)
    )
    resp_b = handler._handle_bootstrap(
        SimpleNamespace(remote_address=("10.0.0.2", 2)), _bootstrap_request(boot_b)
    )
    webui_a = json.loads(resp_a.body)["token"]
    webui_b = json.loads(resp_b.body)["token"]

    assert tokens.take_issued_visla_user(webui_a) == "1"
    assert tokens.take_issued_visla_user(webui_b) == "2"
    assert tokens.visla_tokens.latest("1") == "jwt-A"
    assert tokens.visla_tokens.latest("2") == "jwt-B"
    # Distinct one-shot tokens — no shared credential between the two users.
    assert webui_a != webui_b


async def test_password_bootstrap_has_no_visla_binding():
    """Secret-logins consume a non-Visla bootstrap and stay unbound."""
    handler = _bootstrap_handler()
    plain = handler.tokens.issue_token(60, audience="bootstrap")
    connection = SimpleNamespace(remote_address=("127.0.0.1", 10000))
    response = handler._handle_bootstrap(connection, _bootstrap_request(plain))
    assert response.status_code == 200
    webui_token = json.loads(response.body)["token"]
    assert handler.tokens.take_issued_visla_user(webui_token) is None
