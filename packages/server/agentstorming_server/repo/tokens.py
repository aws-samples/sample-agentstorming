# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Access/refresh tokens repo."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .base import Database


def _h(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


class TokenRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def issue_pair(self, room_id: str, pid: str, access_ttl_s: int, refresh_ttl_s: int) -> dict:
        now = datetime.now(timezone.utc)
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tokens(id, room_id, pid, kind, token_hash, expires_at)
                VALUES ($1, $2, $3, 'access', $4, $5),
                       ($6, $2, $3, 'refresh', $7, $8)
                """,
                uuid4(), room_id, pid, _h(access_token), now + timedelta(seconds=access_ttl_s),
                uuid4(), _h(refresh_token), now + timedelta(seconds=refresh_ttl_s),
            )
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "access_expires_at": now + timedelta(seconds=access_ttl_s),
            "refresh_expires_at": now + timedelta(seconds=refresh_ttl_s),
        }

    async def lookup_access(self, token: str) -> dict | None:
        now = datetime.now(timezone.utc)
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT room_id, pid, expires_at, revoked_at
                  FROM tokens
                 WHERE token_hash = $1 AND kind = 'access'
                """,
                _h(token),
            )
        if row is None:
            return None
        if row["revoked_at"] is not None or row["expires_at"] < now:
            return None
        return dict(row)

    async def lookup_refresh(self, token: str) -> dict | None:
        now = datetime.now(timezone.utc)
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT room_id, pid, expires_at, revoked_at
                  FROM tokens
                 WHERE token_hash = $1 AND kind = 'refresh'
                """,
                _h(token),
            )
        if row is None:
            return None
        if row["revoked_at"] is not None or row["expires_at"] < now:
            return None
        return dict(row)

    async def revoke_all_for_pid(self, room_id: str, pid: str) -> None:
        now = datetime.now(timezone.utc)
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE tokens SET revoked_at=$3 WHERE room_id=$1 AND pid=$2 AND revoked_at IS NULL",
                room_id, pid, now,
            )

    async def revoke_refresh(self, token: str) -> None:
        now = datetime.now(timezone.utc)
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE tokens SET revoked_at=$2 WHERE token_hash=$1 AND revoked_at IS NULL",
                _h(token), now,
            )
