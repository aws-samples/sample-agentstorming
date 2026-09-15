# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Nonce ring buffer repo."""

from __future__ import annotations

from datetime import datetime

from .base import Database


class NonceRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def has(self, pid: str, nonce: str) -> bool:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM nonces WHERE pid=$1 AND nonce=$2", pid, nonce
            )
        return row is not None

    async def insert(self, pid: str, nonce: str, seen_at: datetime) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "INSERT INTO nonces(pid, nonce, seen_at) VALUES ($1, $2, $3) "
                "ON CONFLICT DO NOTHING",
                pid, nonce, seen_at,
            )

    async def trim(self, pid: str, keep_n: int) -> None:
        """Keep only the most recent ``keep_n`` nonces for this pid."""
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM nonces
                 WHERE pid = $1
                   AND nonce NOT IN (
                     SELECT nonce FROM nonces
                      WHERE pid = $1
                      ORDER BY seen_at DESC
                      LIMIT $2
                   )
                """,
                pid, keep_n,
            )
