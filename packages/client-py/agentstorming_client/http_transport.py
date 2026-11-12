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

    async def get_bytes(self, path: str, *, token: str | None = None) -> httpx.Response:
        """GET a binary resource, following one redirect without the bearer.

        A server on the S3 attachment backend answers with a redirect to a
        presigned URL. That URL carries its own credential, so the bearer must
        not go with it: S3 rejects a request that has two, and the bearer has
        no business leaving the server's origin.
        """
        r = await self.request("GET", path, token=token, headers={"Accept": "*/*"})
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            target = r.url.join(r.headers["location"])
            # Follow only to HTTPS, or back to the server itself: a redirect is
            # the server's to give, but this keeps a misconfigured or hostile
            # one from steering the download to a cleartext third party.
            same_origin = (target.scheme, target.host, target.port) == (r.url.scheme, r.url.host, r.url.port)
            if target.scheme != "https" and not same_origin:
                raise error_from_body({"code": "org.agentstorming.err.http",
                                       "message": f"refusing to follow a redirect to {target.scheme}://"})
            r = await self._client.get(target, headers={"Accept": "*/*"})
        if 200 <= r.status_code < 300:
            return r
        _handle(r)
        raise error_from_body({"code": "org.agentstorming.err.http",
                               "message": f"HTTP {r.status_code}"})


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
    if not isinstance(body, dict):
        body = {"code": "org.agentstorming.err.http", "message": f"HTTP {resp.status_code}: {str(body)[:200]}"}
    err = error_from_body(body)
    err.status = resp.status_code
    raise err
