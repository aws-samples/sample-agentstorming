# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Per-room owner keys (§9.3, §13.3).

Each row is an Ed25519 pubkey registered by the room-owner after
claiming the owner invite. The key is used for later out-of-band
operations — e.g. regenerating owner tokens via a signed challenge,
claiming moderator on a frozen room, etc.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import Database


class RoomOwnerKeyRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, room_id: str, pubkey: bytes, label: str | None) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO room_owner_keys(room_id, pubkey, label)
                VALUES ($1, $2, $3)
                ON CONFLICT (room_id, pubkey) DO UPDATE
                    SET label = EXCLUDED.label, revoked_at = NULL
                """,
                room_id, pubkey, label,
            )

    async def exists_active(self, room_id: str, pubkey: bytes) -> bool:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM room_owner_keys WHERE room_id=$1 AND pubkey=$2 AND revoked_at IS NULL",
                room_id, pubkey,
            )
        return row is not None

    async def revoke(self, room_id: str, pubkey: bytes, when: datetime) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE room_owner_keys SET revoked_at=$3 WHERE room_id=$1 AND pubkey=$2",
                room_id, pubkey, when,
            )

    async def list_active(self, room_id: str) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT pubkey, label, registered_at FROM room_owner_keys WHERE room_id=$1 AND revoked_at IS NULL ORDER BY registered_at ASC",
                room_id,
            )
        return [dict(r) for r in rows]
