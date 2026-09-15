# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Admin bearer tokens repo."""

from __future__ import annotations

import hashlib
import secrets

from .base import Database


def _h(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


class AdminTokenRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, label: str) -> str:
        token = secrets.token_urlsafe(32)
        async with self._db.acquire() as conn:
            await conn.execute(
                "INSERT INTO admin_tokens(token_hash, label) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                _h(token), label,
            )
        return token

    async def exists(self, token: str) -> bool:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 FROM admin_tokens WHERE token_hash=$1", _h(token))
        return row is not None
