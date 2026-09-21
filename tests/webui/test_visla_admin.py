"""Visla admin flag: visla_admin_users config, profile store, bootstrap marker."""

from __future__ import annotations

import json
from types import SimpleNamespace

from websockets.datastructures import Headers
from websockets.http11 import Request

from nanobot.channels.websocket.runtime import WebSocketConfig
from nanobot.webui.gateway_tokens import GatewayTokenStore
from nanobot.webui.visla_auth import VislaAttemptLimiter, VislaTokenStore, VislaUser
from nanobot.webui.ws_http import GatewayHTTPHandler


def _user(user_name: str = "perlin.gan", email: str = "perlin.gan@visla.us") -> VislaUser:
    return VislaUser(id=26, email=email, user_name=user_name, type="org_admin", status="active")


def _handler(admin_users: list[str]) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = WebSocketConfig(visla_auth_enabled=True, visla_admin_users=admin_users)
    handler.tokens = GatewayTokenStore()
    handler._visla_attempts = VislaAttemptLimiter()
    handler._log = SimpleNamespace(
        info=lambda *_a, **_k: None,
        warning=lambda *_a, **_k: None,
        error=lambda *_a, **_k: None,
        debug=lambda *_a, **_k: None,
    )
    return handler


def test_config_normalizes_visla_admin_users():
    config = WebSocketConfig(visla_admin_users=[" Alice ", "", "BOB@Visla.US", "alice"])
    assert config.visla_admin_users == ["alice", "bob@visla.us"]
    assert WebSocketConfig().visla_admin_users == []


def test_visla_token_store_keeps_latest_profile():
    store = VislaTokenStore()
    assert store.profile("26") is None
    store.put_profile(_user(user_name="first"))
    store.put_profile(_user(user_name="second"))
    profile = store.profile("26")
    assert profile is not None and profile.user_name == "second"
    expired = VislaTokenStore(entry_ttl_s=-1.0)
    expired.put_profile(_user())
    assert expired.profile("26") is None


def test_is_visla_admin_empty_list_or_operator_paths_allowed():
    assert _handler([])._is_visla_admin("26") is True
    assert _handler(["alice"])._is_visla_admin(None) is True
    assert _handler(["alice"])._is_visla_admin("") is True


def test_is_visla_admin_matches_name_or_email_case_insensitively():
    handler = _handler(["Alice", "BOB@x.io"])
    handler.tokens.visla_tokens.put_profile(_user(user_name="alice", email="a@x.io"))
    assert handler._is_visla_admin("26") is True
    handler.tokens.visla_tokens.put_profile(_user(user_name="bob", email="bob@X.io"))
    assert handler._is_visla_admin("26") is True


def test_is_visla_admin_denies_unlisted_or_unknown_user():
    handler = _handler(["alice"])
    handler.tokens.visla_tokens.put_profile(_user(user_name="mallory", email="m@x.io"))
    assert handler._is_visla_admin("26") is False
    # Unknown user id without a stored profile fails closed.
    assert handler._is_visla_admin("99") is False


async def test_visla_exchange_stores_profile_and_reports_admin(monkeypatch):
    async def fake_validate(url: str, token: str) -> VislaUser:
        return _user()

    handler = _handler(["perlin.gan"])
    monkeypatch.setattr("nanobot.webui.ws_http.validate_visla_token", fake_validate)
    request = Request(
        "/webui/auth/visla",
        Headers({"Host": "gateway.example:8765", "X-Nanobot-Auth": "visla-jwt"}),
    )

    response = await handler._handle_visla_auth(
        SimpleNamespace(remote_address=("10.0.0.8", 5000)), request
    )

    assert response.status_code == 200
    assert json.loads(response.body)["user_id"] == "26"
    profile = handler.tokens.visla_tokens.profile("26")
    assert profile is not None and profile.user_name == "perlin.gan"
    assert handler._is_visla_admin("26") is True
