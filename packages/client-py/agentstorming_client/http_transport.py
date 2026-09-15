# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Thin httpx wrapper handling auth + error mapping."""

from __future__ import annotations

from typing import Any

import httpx

from .errors import error_from_body


class Transport:
    def __init__(self, base_url: str, *, verify_tls: bool = True, user_agent: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            verify=verify_tls,
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, *, token: str | None = None, **kwargs) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = await self._client.request(method, path, headers=headers, **kwargs)
        return resp

    async def get_json(self, path: str, *, token: str | None = None, **kwargs) -> Any:
        r = await self.request("GET", path, token=token, **kwargs)
        return _handle(r)

    async def post_json(self, path: str, body: Any, *, token: str | None = None, idempotency_key: str | None = None, **kwargs) -> Any:
        headers = dict(kwargs.pop("headers", {}) or {})
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        r = await self.request("POST", path, token=token, json=body, headers=headers, **kwargs)
        return _handle(r)

    async def put_json(self, path: str, body: Any, *, token: str | None = None, **kwargs) -> Any:
        r = await self.request("PUT", path, token=token, json=body, **kwargs)
        return _handle(r)

    async def post_multipart(self, path: str, files, *, token: str | None = None) -> Any:
        r = await self.request("POST", path, token=token, files=files)
        return _handle(r)


def _handle(resp: httpx.Response) -> Any:
    if 200 <= resp.status_code < 300:
        try:
            return resp.json()
        except Exception:
            return None
    try:
        body = resp.json()
    except Exception:
        body = {"code": "org.agentstorming.err.http", "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    raise error_from_body(body)
