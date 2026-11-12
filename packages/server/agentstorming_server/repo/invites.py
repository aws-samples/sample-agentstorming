# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Invites repo."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .base import Database, hash_secret

hash_token = hash_secret


def generate_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


class InviteRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, room_id: str, kind: str, expires_at: datetime) -> tuple[UUID, str]:
        """Create an invite; return (invite_id, plaintext token)."""
        token = generate_token()
        token_h = hash_token(token)
        inv_id = uuid4()
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO invites(id, room_id, kind, token_hash, expires_at)
                VALUES ($1, $2, $3, $4, $5)
                """,
                inv_id, room_id, kind, token_h, expires_at,
            )
        return inv_id, token

    async def peek(self, token: str) -> dict | None:
        """The invite's row if the token is valid now (unconsumed, unexpired), else None.

        Does not consume it. :meth:`redeem` re-checks under a row lock, so a
        token that passes here and is spent meanwhile still fails there.
        """
        token_h = hash_token(token)
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, room_id, kind, expires_at
                  FROM invites
                 WHERE token_hash = $1 AND consumed_at IS NULL AND expires_at > now()
                """,
                token_h,
            )
        return dict(row) if row else None

    async def redeem(self, token: str, pid: str, *, conn=None) -> dict | None:
        """Atomically mark token consumed; return invite info or None if invalid."""
        token_h = hash_token(token)
        now = datetime.now(timezone.utc)
        async with self._db.use(conn) as c, c.transaction():
            row = await c.fetchrow(
                """
                SELECT id, room_id, kind, expires_at, consumed_at
                  FROM invites
                 WHERE token_hash = $1 FOR UPDATE
                """,
                token_h,
            )
            if row is None:
                return None
            if row["consumed_at"] is not None:
                return None
            if row["expires_at"] < now:
                return None
            await c.execute(
                "UPDATE invites SET consumed_at=$1, consumed_by=$2 WHERE id=$3",
                now, pid, row["id"],
            )
        return dict(row)
