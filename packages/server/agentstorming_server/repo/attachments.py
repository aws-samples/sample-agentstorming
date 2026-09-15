# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Attachments repo."""

from __future__ import annotations

from uuid import UUID, uuid4

from .base import Database


class AttachmentRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, row: dict) -> UUID:
        att_id = row.get("id") or uuid4()
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO attachments(id, room_id, sender_pid, content_type, size_bytes, filename, sha256, s3_key, local_path)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                att_id, row["room_id"], row["sender_pid"], row["content_type"],
                int(row["size_bytes"]), row["filename"], row["sha256"],
                row.get("s3_key"), row.get("local_path"),
            )
        return att_id

    async def get(self, att_id: UUID) -> dict | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM attachments WHERE id=$1", att_id)
        return dict(row) if row else None

    async def get_by_s3_key(self, s3_key: str) -> dict | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM attachments WHERE s3_key=$1", s3_key)
        return dict(row) if row else None
