# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Grants repo."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from .base import Database


class GrantRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def active(self, room_id: str) -> dict | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT grant_id, pid, hand_id, granted_ts, ttl_expires_at FROM grants WHERE room_id=$1 AND state='ACTIVE'",
                room_id,
            )
        return dict(row) if row else None

    async def active_for_pid(self, room_id: str, pid: str) -> dict | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT grant_id, pid, hand_id, granted_ts, ttl_expires_at FROM grants WHERE room_id=$1 AND pid=$2 AND state='ACTIVE'",
                room_id, pid,
            )
        return dict(row) if row else None

    async def create(self, room_id: str, pid: str, hand_id: UUID | None, ttl_expires_at: datetime) -> UUID:
        async with self._db.tx() as conn:
            # Replace any existing active grant (they conflict).
            exists = await conn.fetchrow(
                "SELECT grant_id FROM grants WHERE room_id=$1 AND state='ACTIVE'", room_id,
            )
            if exists:
                raise ValueError("another grant is already active")
            gid = uuid4()
            await conn.execute(
                """
                INSERT INTO grants(room_id, grant_id, pid, hand_id, granted_ts, ttl_expires_at, state)
                VALUES ($1, $2, $3, $4, $5, $6, 'ACTIVE')
                """,
                room_id, gid, pid, hand_id, datetime.now(timezone.utc), ttl_expires_at,
            )
        return gid

    async def consume(self, room_id: str, grant_id: UUID) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE grants SET state='CONSUMED', consumed_ts=now() WHERE room_id=$1 AND grant_id=$2",
                room_id, grant_id,
            )

    async def expire(self, room_id: str, grant_id: UUID) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE grants SET state='EXPIRED' WHERE room_id=$1 AND grant_id=$2",
                room_id, grant_id,
            )

    async def list_active(self) -> list[dict]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT room_id, grant_id, pid, hand_id, granted_ts, ttl_expires_at FROM grants WHERE state='ACTIVE'",
            )
        return [dict(r) for r in rows]

    async def extend(self, room_id: str, old_grant_id: UUID, new_ttl_expires_at: datetime) -> dict | None:
        """Mark old grant EXTENDED, issue a fresh grant for the same pid + hand.

        Returns the new grant row. The new grant keeps the target_pid +
        hand_id of the old grant, carries a fresh grant_id + ttl.
        """
        async with self._db.tx() as conn:
            old = await conn.fetchrow(
                "SELECT room_id, grant_id, pid, hand_id FROM grants WHERE room_id=$1 AND grant_id=$2 AND state IN ('ACTIVE', 'CONSUMED')",
                room_id, old_grant_id,
            )
            if old is None:
                return None
            # Expire the old grant.
            await conn.execute(
                "UPDATE grants SET state='EXPIRED' WHERE room_id=$1 AND grant_id=$2",
                room_id, old_grant_id,
            )
            gid = uuid4()
            await conn.execute(
                """
                INSERT INTO grants(room_id, grant_id, pid, hand_id, granted_ts, ttl_expires_at, state)
                VALUES ($1, $2, $3, $4, $5, $6, 'ACTIVE')
                """,
                room_id, gid, old["pid"], old["hand_id"], datetime.now(timezone.utc), new_ttl_expires_at,
            )
            return {"grant_id": gid, "target_pid": old["pid"], "hand_id": old["hand_id"]}
