# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Hands repo."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from .base import Database


class HandRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def raise_hand(self, room_id: str, pid: str, hint: str) -> tuple[UUID, bool, UUID | None]:
        """Return (hand_id, created, existing_hand_id_if_any).

        If the participant already has an active (PENDING|GRANTED) hand,
        created=False and existing_hand_id is returned.
        """
        async with self._db.tx() as conn:
            existing = await conn.fetchrow(
                """
                SELECT hand_id FROM hands
                 WHERE room_id=$1 AND pid=$2 AND state IN ('PENDING','GRANTED')
                 ORDER BY raised_ts DESC LIMIT 1
                """,
                room_id, pid,
            )
            if existing is not None:
                return existing["hand_id"], False, existing["hand_id"]

            hand_id = uuid4()
            await conn.execute(
                """
                INSERT INTO hands(room_id, hand_id, pid, state, raised_ts, hint)
                VALUES ($1, $2, $3, 'PENDING', $4, $5)
                """,
                room_id, hand_id, pid, datetime.now(timezone.utc), hint,
            )
            return hand_id, True, None

    async def lower_hand(self, room_id: str, hand_id: UUID, pid: str) -> bool:
        """Return True if the hand was owned by pid and transitioned to WITHDRAWN."""
        async with self._db.acquire() as conn:
            res = await conn.execute(
                """
                UPDATE hands
                   SET state = 'WITHDRAWN', closed_ts = now()
                 WHERE room_id = $1 AND hand_id = $2 AND pid = $3
                   AND state IN ('PENDING','GRANTED')
                """,
                room_id, hand_id, pid,
            )
        return res.endswith("1")

    async def dismiss(self, room_id: str, hand_id: UUID) -> bool:
        async with self._db.acquire() as conn:
            res = await conn.execute(
                """
                UPDATE hands
                   SET state = 'DISMISSED', closed_ts = now()
                 WHERE room_id = $1 AND hand_id = $2 AND state = 'PENDING'
                """,
                room_id, hand_id,
            )
        return res.endswith("1")

    async def mark_granted(self, room_id: str, hand_id: UUID) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE hands SET state='GRANTED' WHERE room_id=$1 AND hand_id=$2",
                room_id, hand_id,
            )

    async def mark_consumed(self, room_id: str, hand_id: UUID) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE hands SET state='CONSUMED', closed_ts=now() WHERE room_id=$1 AND hand_id=$2",
                room_id, hand_id,
            )

    async def mark_expired(self, room_id: str, hand_id: UUID) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE hands SET state='EXPIRED', closed_ts=now() WHERE room_id=$1 AND hand_id=$2",
                room_id, hand_id,
            )

    async def list_pending(self, room_id: str) -> list[dict]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT hand_id, pid, raised_ts, hint FROM hands WHERE room_id=$1 AND state='PENDING' ORDER BY raised_ts ASC",
                room_id,
            )
        return [dict(r) for r in rows]

    async def list_active(self, room_id: str) -> list[dict]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT hand_id, pid, raised_ts, hint, state FROM hands WHERE room_id=$1 AND state IN ('PENDING','GRANTED') ORDER BY raised_ts ASC",
                room_id,
            )
        return [dict(r) for r in rows]
