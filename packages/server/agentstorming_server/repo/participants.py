# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Participants repo."""

from __future__ import annotations

from datetime import datetime

from ..domain.participant import Participant
from .base import Database


class ParticipantRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, p: Participant) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO participants(room_id, pid, pubkey, affiliation, deputy_rank,
                                         joined_at, last_seen_at, runs_as)
                VALUES ($1, $2, $3, $4, $5, $6, $6, $7)
                ON CONFLICT (room_id, pid) DO NOTHING
                """,
                p.room_id, p.pid, p.pubkey, p.affiliation, p.deputy_rank, p.joined_at,
                getattr(p, "runs_as", "agent"),
            )

    async def get(self, room_id: str, pid: str) -> Participant | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM participants WHERE room_id=$1 AND pid=$2",
                room_id, pid,
            )
        return _row_to_participant(row)

    async def get_by_pubkey(self, room_id: str, pubkey: bytes) -> Participant | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM participants WHERE room_id=$1 AND pubkey=$2",
                room_id, pubkey,
            )
        return _row_to_participant(row)

    async def list_all(self, room_id: str) -> list[Participant]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM participants WHERE room_id=$1 AND left_at IS NULL",
                room_id,
            )
        return [_row_to_participant(r) for r in rows]

    async def set_revoked(self, room_id: str, pid: str, when: datetime) -> None:
        """§4.7 — mark the participant's key as revoked. After this, all
        further events + token uses are rejected."""
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE participants SET revoked_at=$3 WHERE room_id=$1 AND pid=$2",
                room_id, pid, when,
            )

    async def is_pubkey_penned(self, pubkey: bytes) -> bool:
        """§12.3 — true if this pubkey belongs to a participant whose pen
        has not yet elapsed (in any room on this server). Used to block
        invite redemption and public registration."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM participants "
                "WHERE pubkey=$1 AND penned_until IS NOT NULL AND penned_until > now() "
                "LIMIT 1",
                pubkey,
            )
        return row is not None

    async def set_pubkey(self, room_id: str, pid: str, new_pubkey: bytes) -> None:
        """Swap a participant's signing pubkey (key-rotation side effect)."""
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE participants SET pubkey=$3 WHERE room_id=$1 AND pid=$2",
                room_id, pid, new_pubkey,
            )

    async def set_affiliation(self, room_id: str, pid: str, affiliation: str,
                              deputy_rank: int | None = None) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                UPDATE participants
                   SET affiliation = $3, deputy_rank = $4
                 WHERE room_id = $1 AND pid = $2
                """,
                room_id, pid, affiliation, deputy_rank,
            )

    async def touch_seen(self, room_id: str, pid: str, when: datetime) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE participants SET last_seen_at=$3 WHERE room_id=$1 AND pid=$2",
                room_id, pid, when,
            )

    async def set_left(self, room_id: str, pid: str, when: datetime) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE participants SET left_at=$3 WHERE room_id=$1 AND pid=$2",
                room_id, pid, when,
            )

    async def set_penned_until(self, room_id: str, pid: str, until: datetime) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE participants SET penned_until=$3 WHERE room_id=$1 AND pid=$2",
                room_id, pid, until,
            )

    async def clear_penned_until(self, room_id: str, pid: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE participants SET penned_until=NULL WHERE room_id=$1 AND pid=$2",
                room_id, pid,
            )

    async def get_by_affiliation(self, room_id: str, affiliation: str) -> Participant | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM participants WHERE room_id=$1 AND affiliation=$2 AND left_at IS NULL LIMIT 1",
                room_id, affiliation,
            )
        return _row_to_participant(row)

    async def get_deputy(self, room_id: str, rank: int) -> Participant | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM participants WHERE room_id=$1 AND deputy_rank=$2 AND left_at IS NULL",
                room_id, rank,
            )
        return _row_to_participant(row)

    async def get_acting_moderator(self, room_id: str) -> Participant | None:
        """The participant currently holding the ``moderating`` role (§6.2, §6.4).

        Resolution order — identical to the one ``services/snapshot.py``
        publishes as ``moderator_pid``, so authorisation and the roster
        clients see can never disagree:

        1. the ``original-moderator``, when one is seated;
        2. otherwise the highest-ranked connected deputy (``deputy_rank``
           ascending — rank 1 outranks rank 2), which is the seat the
           governance ticker promotes into after a freeze (§12.5).

        Returns None when the seat is vacant, e.g. after the owner calls
        ``DELETE /v1/owner/rooms/{id}/moderator`` and no deputy exists.
        Only the room-owner can moderate a room in that state.
        """
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM participants
                 WHERE room_id=$1 AND left_at IS NULL
                   AND (affiliation='original-moderator' OR deputy_rank IS NOT NULL)
                 ORDER BY (affiliation='original-moderator') DESC,
                          deputy_rank ASC NULLS LAST
                 LIMIT 1
                """,
                room_id,
            )
        return _row_to_participant(row)

    async def count_connected(self, room_id: str, grace_seconds: int) -> int:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS c FROM participants WHERE room_id=$1 AND left_at IS NULL AND "
                "last_seen_at > now() - ($2::text || ' seconds')::interval",
                room_id, str(grace_seconds),
            )
        return int(row["c"] if row else 0)

    async def count_active(self, room_id: str) -> int:
        """Number of members that haven't left (ignores last_seen_at grace)."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS c FROM participants WHERE room_id=$1 AND left_at IS NULL",
                room_id,
            )
        return int(row["c"] if row else 0)


def _row_to_participant(row) -> Participant | None:
    if row is None:
        return None
    return Participant(
        room_id=row["room_id"],
        pid=row["pid"],
        pubkey=row["pubkey"],
        affiliation=row["affiliation"],
        deputy_rank=row["deputy_rank"],
        joined_at=row["joined_at"],
        left_at=row["left_at"],
        penned_until=row["penned_until"],
        last_seen_at=row["last_seen_at"],
        runs_as=row.get("runs_as") or "agent",
        revoked_at=row.get("revoked_at"),
    )
