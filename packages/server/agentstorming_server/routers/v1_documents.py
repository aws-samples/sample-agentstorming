# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Room documents (§14).

- ``GET /v1/rooms/{room_id}/documents`` — any authenticated participant
  may list the room's documents. Sizes and metadata only; bodies are
  fetched via the per-document GET.
- ``GET /v1/rooms/{room_id}/documents/{id}`` — fetch a specific doc.
  Returns the body inline for ``text/*`` and ``application/json``;
  binary documents resolve by ``attachment_ref`` (callers use the
  attachment endpoints to download).
- ``PUT /v1/rooms/{room_id}/documents/{id}`` — moderator or owner
  updates (or creates) a document. Emits ``org.agentstorming.document_updated``.
- ``POST /v1/rooms/{room_id}/documents`` — moderator or owner creates
  a new document (convenience for callers that don't want to mint a
  UUID client-side).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..domain.event import TYPE_DOCUMENT_UPDATED

router = APIRouter(tags=["documents"])


_ALLOWED_TYPES = {"problem_statement", "rules", "reference"}


def _type_ok(t: str | None) -> bool:
    if not t:
        return False
    return t in _ALLOWED_TYPES or t.startswith("custom:")


def _doc_to_json(d: dict) -> dict:
    # Keep the wire shape aligned with §14.1. Never leak raw body bytes
    # for binary docs — the caller resolves attachment_ref separately.
    return {
        "id": str(d["id"]),
        "type": d["type"],
        "title": d["title"],
        "body": d.get("body"),
        "attachment_ref": d.get("attachment_ref"),
        "content_type": d.get("content_type"),
        "size_bytes": int(d.get("size_bytes") or 0),
        "created_ts": d["created_ts"].isoformat() if d.get("created_ts") else None,
        "updated_ts": d["updated_ts"].isoformat() if d.get("updated_ts") else None,
    }


@router.get("/{room_id}/documents")
async def list_documents(
    room_id: str, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    await state.auth.principal(room_id, authorization)
    docs = await state.documents.list(room_id)
    return {"room_id": room_id, "documents": [_doc_to_json(d) for d in docs]}


@router.get("/{room_id}/documents/{doc_id}")
async def get_document(
    room_id: str, doc_id: str, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    await state.auth.principal(room_id, authorization)
    try:
        did = UUID(doc_id)
    except ValueError:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_doc_id"})
    doc = await state.documents.get(room_id, did)
    if doc is None:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.doc_unknown"})
    return _doc_to_json(doc)


class _DocUpsertBody(BaseModel):
    type: Literal["problem_statement", "rules", "reference"] | str = "reference"
    title: str = Field(..., min_length=1, max_length=256)
    body: str | None = None
    attachment_ref: str | None = None
    content_type: str = "text/markdown"
    size_bytes: int | None = None


async def _upsert_and_emit(state, room_id: str, did: UUID, body: _DocUpsertBody, updater_pid: str) -> dict:
    if not _type_ok(body.type):
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_doc_type"})

    size = body.size_bytes if body.size_bytes is not None else (len(body.body.encode("utf-8")) if body.body else 0)

    existing = await state.documents.get(room_id, did)
    if existing is None:
        await state.documents.create(room_id, {
            "id": did,
            "type": body.type,
            "title": body.title,
            "body": body.body,
            "attachment_ref": body.attachment_ref,
            "content_type": body.content_type,
            "size_bytes": size,
        })
    else:
        await state.documents.update(room_id, did, {
            "title": body.title,
            "body": body.body,
            "attachment_ref": body.attachment_ref,
            "content_type": body.content_type,
            "size_bytes": size,
        })

    await state.sys_publish_event(
        room_id, TYPE_DOCUMENT_UPDATED,
        payload={
            "doc_id": str(did),
            "type": body.type,
            "title": body.title,
            "content_type": body.content_type,
            "size": size,
            "updated_by": updater_pid,
        },
    )
    return _doc_to_json(await state.documents.get(room_id, did))


@router.put("/{room_id}/documents/{doc_id}")
async def put_document(
    room_id: str, doc_id: str, body: _DocUpsertBody, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    try:
        did = UUID(doc_id)
    except ValueError:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_doc_id"})
    return await _upsert_and_emit(state, room_id, did, body, principal.pid)


@router.post("/{room_id}/documents")
async def create_document(
    room_id: str, body: _DocUpsertBody, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    return await _upsert_and_emit(state, room_id, uuid4(), body, principal.pid)
