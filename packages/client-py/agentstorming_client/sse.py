# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""SSE stream worker.

Replaces the old long-poll loop. Uses ``httpx-sse`` to consume the
server's ``/v1/rooms/{id}/stream`` endpoint. Auto-reconnects on
network errors; resumes from the last seen seq via the
``Last-Event-ID`` header (and as a belt-and-braces ``?since=`` query).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

import httpx
from httpx_sse import aconnect_sse

from .errors import AuthError, StormError

if TYPE_CHECKING:  # pragma: no cover
    from .client import StormClient

log = logging.getLogger(__name__)


class SSEWorker:
    def __init__(self, client: "StormClient") -> None:
        self._client = client
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 1.0
            except AuthError:
                try:
                    await self._client.refresh_tokens()
                except Exception:
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, 60)
            except StormError as e:
                log.warning("stream error: %s", e)
                await self._sleep(backoff)
                backoff = min(backoff * 2, 60)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("stream exception: %s", e)
                await self._sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _refresh_if_near_expiry(self, margin_seconds: int = 60) -> None:
        """Refresh the access token proactively if it expires within ``margin_seconds``.

        Keeps the SSE stream connected across long sessions without
        waiting for a 401 reconnect. Uses the existing refresh flow.
        """
        expires_at = self._client.vault.data.access_expires_at
        if not expires_at:
            return
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return
        now = datetime.now(timezone.utc)
        if (exp - now).total_seconds() <= margin_seconds:
            try:
                await self._client.refresh_tokens()
            except Exception as e:
                log.warning("proactive token refresh failed: %s", e)

    async def _connect_once(self) -> None:
        cfg = self._client.config
        # Refresh proactively so we don't drop the connection mid-stream.
        await self._refresh_if_near_expiry()
        since = self._client.vault.data.since_cursor
        url = f"{cfg.base_url.rstrip('/')}/v1/rooms/{cfg.room_id}/stream"
        headers = {"Authorization": f"Bearer {self._client.vault.data.access_token}"}
        if since >= 0:
            headers["Last-Event-ID"] = str(since)
        params: dict[str, Any] = {"since": since, "limit": 200}

        timeout = httpx.Timeout(None, connect=10)
        async with httpx.AsyncClient(timeout=timeout, verify=cfg.verify_tls) as client:
            async with aconnect_sse(
                client, "GET", url, headers=headers, params=params
            ) as event_source:
                if event_source.response.status_code == 401:
                    raise AuthError("unauthorised")
                event_source.response.raise_for_status()
                # Launch a background refresher that keeps the token
                # fresh for the lifetime of this SSE connection. The
                # server cannot swap the bearer mid-stream, but when
                # the connection drops for any reason the reconnect
                # will use the refreshed token.
                refresher = asyncio.create_task(self._mid_stream_refresher())
                try:
                    async for sse_event in event_source.aiter_sse():
                        if self._stop.is_set():
                            return
                        if sse_event.event not in ("event", "snapshot"):
                            continue
                        try:
                            raw = json.loads(sse_event.data)
                        except Exception:
                            log.warning("bad SSE data: %r", sse_event.data[:100])
                            continue
                        if (
                            self._client.config.verify_signatures
                            and not await self._client.verify_incoming(raw)
                        ):
                            log.warning("dropped event seq=%s: bad signature", raw.get("seq"))
                            continue
                        await self._client.buffer.append(raw)
                        await self._client.metadata.apply(raw)
                        await self._client.dispatch_hooks(raw)
                        if sse_event.id and sse_event.id.isdigit():
                            self._client.vault.data.since_cursor = int(sse_event.id)
                            self._client.vault.save()
                        elif isinstance(raw.get("seq"), int):
                            self._client.vault.data.since_cursor = int(raw["seq"])
                            self._client.vault.save()
                finally:
                    refresher.cancel()
                    try:
                        await refresher
                    except (asyncio.CancelledError, Exception):
                        pass

    async def _mid_stream_refresher(self) -> None:
        """Periodically call :meth:`_refresh_if_near_expiry` while the
        stream is open. Sleeps for ~half the access-token lifetime (or
        60s, whichever is smaller) between checks so we always refresh
        well before expiry."""
        while not self._stop.is_set():
            await self._refresh_if_near_expiry(margin_seconds=120)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                continue

    async def _sleep(self, base: float) -> None:
        jitter = 0.5 + random.random()
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=base * jitter)
        except asyncio.TimeoutError:
            pass
