# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Per-moderator ignore-entry repo (§12.1a).

See docs/specification.md §12.1a for semantics: each row says
"moderator_pid in room_id does not want to receive whisper traffic
(and optionally registration_requests) from ignored_pid in their
personal view."
"""

from __future__ import annotations

from typing import Any, Literal

from .base import Database


IgnoreKind = Literal["muted", "candidate"]


class IgnoreEntryRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, room_id: str, moderator_pid: str, ignored_pid: str, kind: IgnoreKind) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ignore_entries(room_id, moderator_pid, ignored_pid, kind)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (room_id, moderator_pid, ignored_pid)
                    DO UPDATE SET kind = EXCLUDED.kind
                """,
                room_id, moderator_pid, ignored_pid, kind,
            )

    async def remove(self, room_id: str, moderator_pid: str, ignored_pid: str) -> bool:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "DELETE FROM ignore_entries WHERE room_id=$1 AND moderator_pid=$2 AND ignored_pid=$3 RETURNING ignored_pid",
                room_id, moderator_pid, ignored_pid,
            )
        return row is not None

    async def list_for(self, room_id: str, moderator_pid: str) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT ignored_pid, kind, created_at FROM ignore_entries WHERE room_id=$1 AND moderator_pid=$2 ORDER BY created_at ASC",
                room_id, moderator_pid,
            )
        return [dict(r) for r in rows]

    async def fetch_map(self, room_id: str, moderator_pid: str) -> dict[str, str]:
        """Return {ignored_pid: kind} for the given moderator.

        Used by the stream/sync/messages visibility filter on every read.
        Hot path — keep the query tight. Callers cache within a single
        request.
        """
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT ignored_pid, kind FROM ignore_entries WHERE room_id=$1 AND moderator_pid=$2",
                room_id, moderator_pid,
            )
        return {r["ignored_pid"]: r["kind"] for r in rows}
