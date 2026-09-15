# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Rooms repo."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..domain.room import Room, RoomConfig
from .base import Database


def _row_to_room(row: Any) -> Room:
    cfg = row["config"]
    if not isinstance(cfg, dict):
        cfg = json.loads(cfg)
    return Room(
        id=row["id"],
        state=row["state"],
        config=RoomConfig.from_json(cfg),
        server_pubkey=row["server_pubkey"],
        server_privkey=row["server_privkey"],
        created_at=row["created_at"],
        freeze_since=row["freeze_since"],
        terminated_at=row["terminated_at"],
        title=row.get("title"),
        description=row.get("description"),
    )


class RoomRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, room: Room) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO rooms(id, state, config, server_pubkey, server_privkey,
                                  created_at, title, description)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8)
                """,
                room.id, room.state, json.dumps(room.config.to_json()),
                room.server_pubkey, room.server_privkey, room.created_at,
                room.title, room.description,
            )

    async def get(self, room_id: str) -> Room | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM rooms WHERE id=$1", room_id)
        return _row_to_room(row) if row else None

    async def list_public_active(self) -> list[Room]:
        """List rooms that are publicly discoverable + currently active."""
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM rooms
                WHERE state IN ('CREATED', 'ACTIVE', 'FROZEN')
                  AND (config->>'visibility') = 'public'
                ORDER BY created_at DESC
                LIMIT 200
                """
            )
        return [_row_to_room(r) for r in rows]

    async def list_all_active(self) -> list[Room]:
        """Owner-facing list (includes private rooms)."""
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM rooms
                WHERE state IN ('CREATED', 'ACTIVE', 'FROZEN')
                ORDER BY created_at DESC
                LIMIT 500
                """
            )
        return [_row_to_room(r) for r in rows]

    async def set_state(self, room_id: str, state: str, *, freeze_since: datetime | None = None) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE rooms SET state=$2, freeze_since=$3 WHERE id=$1",
                room_id, state, freeze_since,
            )

    async def update_config(self, room_id: str, config: RoomConfig) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE rooms SET config=$2::jsonb WHERE id=$1",
                room_id, json.dumps(config.to_json()),
            )

    async def update_metadata(self, room_id: str, *, title: str | None = None,
                              description: str | None = None) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE rooms SET title=COALESCE($2, title), description=COALESCE($3, description) WHERE id=$1",
                room_id, title, description,
            )

    async def terminate(self, room_id: str, when: datetime) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE rooms SET state='TERMINATED', terminated_at=$2 WHERE id=$1",
                room_id, when,
            )
