# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The LISTEN connection recovers, and governance ticks release their lock.

- ``PubSubHub`` opened its LISTEN connection once and never watched it. After a
  database failover or an idle-connection reset, every SSE stream in the
  process went silent for good while ``/readyz`` kept answering 200.
- ``GovernanceTicker`` took its session-scoped advisory lock on a pooled
  connection and returned the connection at once. asyncpg resets a connection
  on release with ``pg_advisory_unlock_all()``, so the lock was gone before the
  tick ran and never kept a second replica out.
"""

from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest

from agentstorming_server.repo.base import Database
from agentstorming_server.services.governance import GovernanceTicker
from agentstorming_server.services.pubsub import PubSubHub

from ..conftest import DSN

#: Not the production id, so a server running on the test database does not
#: hold it while these tests try to.
TEST_LOCK_ID = 778_042


async def _notify(room_id: str, seq: int) -> None:
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("SELECT pg_notify('room_events', $1)",
                           json.dumps({"room_id": room_id, "seq": seq}))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_the_hub_reconnects_and_wakes_its_subscribers(reset_db):
    hub = PubSubHub(DSN, check_interval=3600)
    await hub.start()
    try:
        q = hub.subscribe("room-1")
        await _notify("room-1", 1)
        assert (await asyncio.wait_for(q.get(), 5))["seq"] == 1

        # Kill the LISTEN backend from outside, as a failover would.
        pid = hub._conn.get_server_pid()
        admin = await asyncpg.connect(DSN)
        try:
            await admin.execute("SELECT pg_terminate_backend($1)", pid)
        finally:
            await admin.close()
        await asyncio.sleep(0.2)
        assert not hub.healthy

        assert await hub.check() is True
        assert hub.healthy and hub._conn.get_server_pid() != pid
        wake = await asyncio.wait_for(q.get(), 5)
        assert wake == {"room_id": "room-1", "resync": True}, "subscribers must re-read after a gap"

        await _notify("room-1", 2)
        assert (await asyncio.wait_for(q.get(), 5))["seq"] == 2
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_the_keepalive_loop_reconnects_on_its_own(reset_db):
    hub = PubSubHub(DSN, check_interval=0.2, probe_timeout=2)
    await hub.start()
    try:
        hub._conn.terminate()
        for _ in range(50):
            await asyncio.sleep(0.1)
            if hub.healthy:
                break
        assert hub.healthy
    finally:
        await hub.stop()


async def _advisory_lock_holders(conn) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = $1 AND granted",
        TEST_LOCK_ID,
    )


@pytest.mark.asyncio
async def test_the_governance_lock_keeps_a_second_replica_out_for_the_whole_tick(reset_db):
    db = Database(DSN)
    await db.connect()
    in_tick = asyncio.Event()
    ticks = {"n": 0}

    class Ticker(GovernanceTicker):
        async def _tick(self) -> None:
            ticks["n"] += 1
            in_tick.set()
            await asyncio.sleep(0.6)

    ticker = Ticker(db, None, None, None, None, None, None, None,
                    tick_seconds=0.05, lock_id=TEST_LOCK_ID)
    admin = await asyncpg.connect(DSN)
    try:
        await ticker.start()
        await asyncio.wait_for(in_tick.wait(), 5)
        await asyncio.sleep(0.2)
        # Mid-tick: another session, standing in for another replica, is kept out.
        assert await admin.fetchval("SELECT pg_try_advisory_lock($1)", TEST_LOCK_ID) is False
        await ticker.stop()
        assert ticks["n"] >= 1
        # After the tick the lock is free again.
        assert await _advisory_lock_holders(admin) == 0
        assert await admin.fetchval("SELECT pg_try_advisory_lock($1)", TEST_LOCK_ID) is True
        await admin.execute("SELECT pg_advisory_unlock($1)", TEST_LOCK_ID)
    finally:
        await admin.close()
        await db.close()


@pytest.mark.asyncio
async def test_the_ticker_purges_nonces_at_most_once_per_interval(reset_db):
    calls = {"n": 0}

    class Nonces:
        async def purge_expired(self) -> int:
            calls["n"] += 1
            return 0

    ticker = GovernanceTicker(None, None, None, None, None, None, None, None,
                              owner_nonces=Nonces(), nonce_purge_interval_seconds=3600)
    for _ in range(5):
        await ticker._purge_nonces()
    assert calls["n"] == 1
