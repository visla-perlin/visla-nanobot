"""Visla SSO token exchange for the embedded WebUI gateway.

The gateway accepts a Visla user token (JWT) from the browser, validates it
against the Visla ``current-user`` endpoint, and — on success — mints a
one-shot short-lived bootstrap token. The static gateway secret never leaves
the server.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

import httpx
from loguru import logger

DEFAULT_VISLA_CURRENT_USER_URL = "https://admin-api.prod01.visla.us/api/my/current-user"

_VISLA_TIMEOUT_S = 10.0
_MAX_VISLA_TOKEN_CHARS = 8192


class VislaAuthError(Exception):
    """Base class for Visla authentication failures."""


class VislaAuthRejectedError(VislaAuthError):
    """Visla rejected the supplied token, or the user is not active."""


class VislaAuthUnavailableError(VislaAuthError):
    """The Visla current-user endpoint was unreachable or unusable."""


@dataclass(slots=True, frozen=True)
class VislaUser:
    """The fields nanobot requires from a successful Visla current-user lookup."""

    id: int | str
    email: str
    user_name: str
    type: str
    status: str


async def validate_visla_token(
    current_user_url: str,
    token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> VislaUser:
    """Validate a Visla user token against the current-user endpoint.

    Raises :class:`VislaAuthRejectedError` when Visla rejects the token (bad,
    expired, or inactive user) and :class:`VislaAuthUnavailableError` when the
    endpoint itself cannot be reached or returns unusable data.
    """
    if not token or len(token) > _MAX_VISLA_TOKEN_CHARS:
        raise VislaAuthRejectedError("malformed visla token")
    try:
        async with httpx.AsyncClient(
            timeout=_VISLA_TIMEOUT_S,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.get(current_user_url, headers={"token": token})
    except (httpx.HTTPError, ValueError) as exc:
        raise VislaAuthUnavailableError(f"visla endpoint unreachable: {exc}") from exc

    if response.status_code in (401, 403):
        raise VislaAuthRejectedError("visla rejected token")
    if response.status_code != 200:
        raise VislaAuthUnavailableError(f"visla endpoint returned HTTP {response.status_code}")
    try:
        raw_payload = response.json()
    except ValueError as exc:
        raise VislaAuthUnavailableError("visla endpoint returned invalid JSON") from exc
    if not isinstance(raw_payload, dict):
        raise VislaAuthUnavailableError("visla endpoint returned unexpected JSON shape")
    payload = cast(dict[str, Any], raw_payload)
    if payload.get("code") != 0:
        raise VislaAuthRejectedError("visla reported failure")
    data = cast("dict[str, Any] | None", payload.get("data"))
    if data is None:
        raise VislaAuthRejectedError("visla response missing user data")
    user = VislaUser(
        id=cast(Any, data.get("id")) or "",
        email=str(data.get("email") or "").strip(),
        user_name=str(data.get("userName") or "").strip(),
        type=str(data.get("type") or "").strip(),
        status=str(data.get("status") or "").strip().lower(),
    )
    if not user.id or not user.email or not user.type:
        raise VislaAuthRejectedError("visla response missing required user fields")
    if user.status != "active":
        raise VislaAuthRejectedError("visla user is not active")
    return user


class VislaAttemptLimiter:
    """Sliding-window per-peer limiter for the token exchange endpoint."""

    def __init__(self, max_attempts: int = 10, window_s: float = 60.0) -> None:
        self.max_attempts = max_attempts
        self.window_s = window_s
        self._attempts: dict[str, list[float]] = {}

    def allow(self, peer: str) -> bool:
        """Record one attempt for ``peer`` and report whether it is allowed."""
        now = time.monotonic()
        window_start = now - self.window_s
        recent = [stamp for stamp in self._attempts.get(peer, ()) if stamp > window_start]
        if len(recent) >= self.max_attempts:
            self._attempts[peer] = recent
            return False
        recent.append(now)
        self._attempts[peer] = recent
        if len(self._attempts) > 10_000:
            self._prune_peers(window_start)
        return True

    def _prune_peers(self, window_start: float) -> None:
        stale = [
            peer
            for peer, stamps in self._attempts.items()
            if not stamps or stamps[-1] <= window_start
        ]
        for peer in stale:
            del self._attempts[peer]
        logger.debug("visla attempt limiter pruned {} stale peers", len(stale))


class VislaTokenStore:
    """In-memory registry of the latest Visla JWT per user id.

    Used to inject the conversing user's credentials into skill subprocesses
    (``VISLA_TOKEN``). Tokens never touch disk and are dropped on process
    restart; users simply re-run the SSO exchange in that case. The entry TTL
    is only a retention bound — the upstream JWT's own expiry remains the real
    validity limit, and expired tokens surface as admin-api 3004/3005 to the
    caller, prompting a fresh SSO exchange.
    """

    def __init__(self, max_users: int = 10_000, entry_ttl_s: float = 24 * 3600.0) -> None:
        self.max_users = max_users
        self.entry_ttl_s = entry_ttl_s
        self._tokens: dict[str, tuple[str, float]] = {}

    def put(self, user_id: str, token: str) -> None:
        """Record (or rotate) the latest JWT for *user_id*."""
        if not user_id or not token:
            return
        self._prune()
        if len(self._tokens) >= self.max_users and user_id not in self._tokens:
            logger.warning("visla token store full ({}), rejecting new entry", self.max_users)
            return
        self._tokens[user_id] = (token, time.monotonic())

    def latest(self, user_id: str | None) -> str:
        """Return the newest stored JWT for *user_id*, or "" when unknown/expired."""
        if not user_id:
            return ""
        entry = self._tokens.get(user_id)
        if entry is None:
            return ""
        token, stored_at = entry
        if time.monotonic() - stored_at > self.entry_ttl_s:
            self._tokens.pop(user_id, None)
            return ""
        return token

    def clear(self) -> None:
        self._tokens.clear()

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [
            user_id
            for user_id, (_, stored_at) in self._tokens.items()
            if now - stored_at > self.entry_ttl_s
        ]
        for user_id in stale:
            del self._tokens[user_id]
