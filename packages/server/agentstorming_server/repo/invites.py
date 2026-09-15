# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Invites repo."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .base import Database


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


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

    async def redeem(self, token: str, pid: str) -> dict | None:
        """Atomically mark token consumed; return invite info or None if invalid."""
        token_h = hash_token(token)
        now = datetime.now(timezone.utc)
        async with self._db.tx() as conn:
            row = await conn.fetchrow(
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
            await conn.execute(
                "UPDATE invites SET consumed_at=$1, consumed_by=$2 WHERE id=$3",
                now, pid, row["id"],
            )
        return dict(row)
