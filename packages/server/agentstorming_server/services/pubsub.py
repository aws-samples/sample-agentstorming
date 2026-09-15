# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Pub/sub hub backed by Postgres LISTEN/NOTIFY.

Used by SSE handlers and by any other in-process component that needs
to be notified on new room events. One hub per server process. Each
subscriber gets a dedicated asyncio.Queue; the hub owns a single
LISTEN connection and fans NOTIFY payloads out to all queues for the
affected room.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, AsyncIterator

import asyncpg

log = logging.getLogger(__name__)


class PubSubHub:
    """Process-wide LISTEN/NOTIFY bridge."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: asyncpg.Connection | None = None
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._keepalive_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        self._conn = await asyncpg.connect(self._dsn)
        await self._conn.add_listener("room_events", self._on_notify)
        self._keepalive_task = asyncio.create_task(self._keepalive())

    async def stop(self) -> None:
        self._stopping.set()
        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._conn is not None:
            try:
                await self._conn.remove_listener("room_events", self._on_notify)
            except Exception:
                pass
            await self._conn.close()
            self._conn = None

    def _on_notify(self, _conn, _pid, channel: str, payload: str) -> None:
        if channel != "room_events":
            return
        try:
            data = json.loads(payload)
        except Exception:
            log.warning("bad NOTIFY payload: %r", payload)
            return
        room_id = data.get("room_id")
        if not room_id:
            return
        for q in list(self._subs.get(room_id, ())):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    async def _keepalive(self) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(30)

    def subscribe(self, room_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a fresh subscriber queue. Caller must call :meth:`unsubscribe`."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subs[room_id].add(q)
        return q

    def unsubscribe(self, room_id: str, q: asyncio.Queue) -> None:
        self._subs[room_id].discard(q)

    async def stream(self, room_id: str) -> AsyncIterator[dict[str, Any]]:
        """Async iterator yielding fresh-event notify payloads for ``room_id``.

        Yields lightweight ``{room_id, seq}`` notifications. The handler
        dereferences the actual events from the DB — we don't rely on
        NOTIFY for payload delivery (NOTIFY payloads are capped at 8 KiB
        by Postgres).
        """
        q = self.subscribe(room_id)
        try:
            while not self._stopping.is_set():
                payload = await q.get()
                yield payload
        finally:
            self.unsubscribe(room_id, q)
