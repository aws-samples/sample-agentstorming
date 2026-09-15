# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Interview + registration-door repos (§8.3-§8.4)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import Database


class InterviewRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        *,
        interview_id: str,
        room_id: str,
        candidate_pid: str,
        moderator_pid: str | None,
        declared: str | None,
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO interviews(id, room_id, candidate_pid, moderator_pid, state, declared)
                VALUES ($1, $2, $3, $4, 'PENDING', $5)
                """,
                interview_id, room_id, candidate_pid, moderator_pid, declared,
            )

    async def get(self, interview_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM interviews WHERE id=$1", interview_id)
        return dict(row) if row else None

    async def decide(self, interview_id: str, final_state: str) -> None:
        assert final_state in ("ACCEPTED", "REJECTED", "ABANDONED")
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE interviews SET state=$2, decided_at=now() WHERE id=$1",
                interview_id, final_state,
            )

    async def list_pending(self, room_id: str) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM interviews WHERE room_id=$1 AND state IN ('PENDING','ACTIVE') ORDER BY started_at ASC",
                room_id,
            )
        return [dict(r) for r in rows]


class RegistrationDoorRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_closed(self, room_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM registration_doors WHERE room_id=$1 AND reopens_at > now()",
                room_id,
            )
        return dict(row) if row else None

    async def close(self, room_id: str, reopens_at: datetime, reason: str | None) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO registration_doors(room_id, reopens_at, reason)
                VALUES ($1, $2, $3)
                ON CONFLICT (room_id) DO UPDATE SET closed_at=now(), reopens_at=EXCLUDED.reopens_at, reason=EXCLUDED.reason
                """,
                room_id, reopens_at, reason,
            )

    async def open(self, room_id: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute("DELETE FROM registration_doors WHERE room_id=$1", room_id)
