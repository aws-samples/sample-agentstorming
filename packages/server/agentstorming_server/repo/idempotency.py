# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Idempotency repo."""

from __future__ import annotations

import json

from .base import Database


class IdempotencyRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def lookup(self, key: str, pid: str) -> dict | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, body FROM idempotency WHERE key=$1 AND pid=$2",
                key, pid,
            )
        if not row:
            return None
        body = row["body"] if isinstance(row["body"], dict) else json.loads(row["body"])
        return {"status": int(row["status"]), "body": body}

    async def save(self, key: str, pid: str, status: int, body: dict) -> None:
        # Upsert: if reserve() inserted a placeholder row (status=0)
        # earlier, this finalises it; otherwise it inserts a fresh
        # row. The unconditional UPDATE on conflict is safe because
        # status > 0 means the operation has completed and the cached
        # body is the canonical reply.
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO idempotency(key, pid, status, body)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (key, pid) DO UPDATE
                  SET status = EXCLUDED.status, body = EXCLUDED.body
                  WHERE idempotency.status = 0
                """,
                key, pid, status, json.dumps(body),
            )

    async def reserve(self, key: str, pid: str) -> bool:
        """Atomically claim an idempotency key for this pid.

        Returns True if the caller is the FIRST to claim the (key,pid)
        and should proceed to do the work; False if another request
        already claimed it (the caller should wait and re-lookup, or
        return a 409). The reservation row is replaced by the real row
        when ``save()`` is called with the final status+body.
        """
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO idempotency(key, pid, status, body)
                VALUES ($1, $2, 0, '{}'::jsonb)
                ON CONFLICT (key, pid) DO NOTHING
                RETURNING key
                """,
                key, pid,
            )
        return row is not None
