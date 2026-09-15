# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Mutes repo."""

from __future__ import annotations

from datetime import datetime

from .base import Database


class MuteRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def is_muted(self, room_id: str, pid: str) -> bool:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM mutes WHERE room_id=$1 AND pid=$2 AND expires_at > now()",
                room_id, pid,
            )
        return row is not None

    async def mute(self, room_id: str, pid: str, started_at: datetime, expires_at: datetime) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "INSERT INTO mutes(room_id, pid, started_at, expires_at) VALUES ($1,$2,$3,$4)",
                room_id, pid, started_at, expires_at,
            )

    async def unmute(self, room_id: str, pid: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE mutes SET expires_at = now() WHERE room_id=$1 AND pid=$2 AND expires_at > now()",
                room_id, pid,
            )

    async def list_expiring(self) -> list[dict]:
        """Return mutes that have expired (for the governance ticker)."""
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT room_id, pid FROM mutes WHERE expires_at <= now()",
            )
        return [dict(r) for r in rows]
