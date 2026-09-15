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
