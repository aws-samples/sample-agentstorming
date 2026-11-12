# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""FastAPI dependencies — expose app.state components."""

from __future__ import annotations

from fastapi import Header, Path, Request


def get_state(request: Request):
    return request.app.state


def get_auth(request: Request):
    return request.app.state.auth


def bearer(authorization: str = Header(default=None)) -> str | None:
    return authorization


def idempotency_key(idempotency_key: str = Header(default="", alias="Idempotency-Key")) -> str:
    return idempotency_key


def public_base_url(request: Request) -> str:
    """The origin to build outward-facing links from, without a trailing slash.

    Prefers the configured ``public_base_url`` over ``request.base_url``, which
    Starlette reconstructs from the Host header. See the setting's comment in
    config.py for why that matters for links carrying an invite token.
    """
    configured = getattr(request.app.state.settings, "public_base_url", "") or ""
    if configured.strip():
        return configured.strip().rstrip("/")
    return str(request.base_url).rstrip("/")


#: Upper bound on an invite's lifetime. ``timedelta`` overflows a little past
#: 10**9 days, which used to surface as a 500.
MAX_INVITE_TTL_SECONDS = 365 * 86_400


def invite_link(base: str, room_id: str, token: str) -> str:
    """The shareable link for an invite: ``{base}/#/r/{room}/join?t={token}``.

    The token sits in the URL fragment, which browsers never send, so it
    reaches no server log: CloudFront and S3 access logs, WAF logs and ALB
    logs all record the query string, and the query form, still accepted by
    the SPA, put a live invite into each of them whenever a link was opened.
    """
    return f"{base}/#/r/{room_id}/join?t={token}"


def is_db_text(value: object) -> bool:
    """A ``str`` Postgres can store: no NUL and no lone surrogate.

    Either one used to reach asyncpg and answer 500.
    """
    if not isinstance(value, str) or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
