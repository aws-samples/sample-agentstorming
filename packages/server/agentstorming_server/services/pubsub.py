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
    """Process-wide LISTEN/NOTIFY bridge.

    The LISTEN connection is checked every ``check_interval`` seconds and
    re-established if it has gone. It used to be opened once and never
    watched, so after a database failover or an idle-connection reset every
    SSE stream in the process went silent for good while ``/readyz`` kept
    answering 200. NOTIFYs sent while it was down are lost, so after a
    reconnect every subscriber is woken once to re-read from its cursor.
    """

    def __init__(self, dsn: str, *, check_interval: float = 10.0, probe_timeout: float = 5.0) -> None:
        self._dsn = dsn
        self._conn: asyncpg.Connection | None = None
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._keepalive_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._check_interval = check_interval
        self._probe_timeout = probe_timeout
        self._healthy = False

    @property
    def healthy(self) -> bool:
        """Whether the LISTEN connection was alive at the last check."""
        return self._healthy and self._conn is not None and not self._conn.is_closed()

    async def start(self) -> None:
        await self._connect()
        self._keepalive_task = asyncio.create_task(self._keepalive())

    async def _connect(self) -> None:
        conn = await asyncpg.connect(self._dsn, timeout=self._probe_timeout * 2)
        try:
            await asyncio.wait_for(
                conn.add_listener("room_events", self._on_notify), timeout=self._probe_timeout
            )
        except BaseException:
            conn.terminate()
            raise
        self._conn = conn
        self._healthy = True

    async def _close_conn(self, *, graceful: bool) -> None:
        """Drop the LISTEN connection.

        A connection that failed its probe is terminated outright. Closing it
        politely sends UNLISTEN and a Terminate message and waits for the
        server, and on a peer that has gone silent (a failover, or a NAT that
        dropped the flow) that wait never ends: the hub then never reconnected.
        Only a healthy connection, at shutdown, is closed gracefully, and even
        then under a timeout.
        """
        conn, self._conn = self._conn, None
        if conn is None:
            return
        if not graceful:
            conn.terminate()
            return
        try:
            await asyncio.wait_for(
                conn.remove_listener("room_events", self._on_notify), timeout=self._probe_timeout
            )
            await conn.close(timeout=self._probe_timeout)
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
            conn.terminate()

    async def stop(self) -> None:
        self._stopping.set()
        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except (asyncio.CancelledError, Exception):
                pass
        self._healthy = False
        await self._close_conn(graceful=True)

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
        self._wake(room_id, data)

    def _wake(self, room_id: str, data: dict[str, Any]) -> None:
        for q in list(self._subs.get(room_id, ())):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    async def _alive(self) -> bool:
        conn = self._conn
        if conn is None or conn.is_closed():
            return False
        try:
            # is_closed() stays False on a connection whose peer vanished
            # without a FIN, so ask it something.
            await asyncio.wait_for(conn.execute("SELECT 1"), timeout=self._probe_timeout)
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
            return False
        return True

    async def check(self) -> bool:
        """Probe the LISTEN connection once, reconnecting if it is gone."""
        if await self._alive():
            self._healthy = True
            return True
        self._healthy = False
        log.warning("LISTEN connection lost; reconnecting")
        await self._close_conn(graceful=False)
        try:
            await self._connect()
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
            log.warning("LISTEN reconnect failed; retrying in %ss", self._check_interval, exc_info=True)
            return False
        log.info("LISTEN connection re-established")
        for room_id in [r for r, qs in self._subs.items() if qs]:
            self._wake(room_id, {"room_id": room_id, "resync": True})
        return True

    async def _keepalive(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._check_interval)
                return
            except TimeoutError:
                pass
            try:
                await self.check()
            except Exception:
                log.exception("LISTEN health check failed")

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
