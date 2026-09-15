# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Documents + summary repos."""

from __future__ import annotations

from uuid import UUID, uuid4

from .base import Database


class DocumentRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, room_id: str, doc: dict) -> UUID:
        doc_id = doc.get("id") or uuid4()
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO documents(room_id, id, type, title, body, attachment_ref, content_type, size_bytes)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                room_id, doc_id, doc["type"], doc["title"], doc.get("body"),
                doc.get("attachment_ref"), doc["content_type"], int(doc.get("size_bytes", 0)),
            )
        return doc_id

    async def get(self, room_id: str, doc_id: UUID) -> dict | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM documents WHERE room_id=$1 AND id=$2", room_id, doc_id,
            )
        return dict(row) if row else None

    async def list(self, room_id: str) -> list[dict]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM documents WHERE room_id=$1 ORDER BY created_ts ASC", room_id,
            )
        return [dict(r) for r in rows]

    #: Columns a moderator may mutate via PUT /documents/{id} (§14.2).
    UPDATABLE = ("title", "body", "attachment_ref", "content_type", "size_bytes")

    async def update(self, room_id: str, doc_id: UUID, fields: dict) -> None:
        """Replace the updatable columns of a document (§14.2).

        These are **replace** semantics, not merge: the endpoint behind this is
        a PUT, and its request model supplies all five columns on every call,
        so a PUT that omits ``body`` is asking for the body to be cleared. Do
        not "improve" this into COALESCE — that quietly turns the PUT into a
        PATCH and makes clearing a field impossible.

        The SQL is one fixed string. The previous version assembled the SET
        list from whichever keys the caller passed, which was safe (column
        names came from a literal allowlist, values were always asyncpg
        parameters) but tripped every SQL-injection linter, and each flag then
        needed a human to re-derive why it was fine. Nothing is interpolated
        now, so there is no argument to have.
        """
        if not any(k in fields for k in self.UPDATABLE):
            return
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                UPDATE documents SET
                    title          = $3,
                    body           = $4,
                    attachment_ref = $5,
                    content_type   = $6,
                    size_bytes     = $7,
                    updated_ts     = now()
                 WHERE room_id = $1 AND id = $2
                """,
                room_id, doc_id,
                fields.get("title"),
                fields.get("body"),
                fields.get("attachment_ref"),
                fields.get("content_type"),
                fields.get("size_bytes"),
            )


class SummaryRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, room_id: str) -> str | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow("SELECT text FROM summaries WHERE room_id=$1", room_id)
        return row["text"] if row else None

    async def set(self, room_id: str, text: str, updated_by: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO summaries(room_id, text, updated_by, updated_ts)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (room_id) DO UPDATE
                   SET text = EXCLUDED.text, updated_by = EXCLUDED.updated_by, updated_ts = EXCLUDED.updated_ts
                """,
                room_id, text, updated_by,
            )
