# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Attachments upload/serve."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .deps import public_base_url

router = APIRouter(tags=["attachments"])


@router.post("/rooms/{room_id}/attachments")
async def upload_attachment(room_id: str, request: Request,
                            file: UploadFile = File(...),
                            authorization: str | None = Header(default=None)) -> dict:
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    room = await state.rooms.get(room_id)
    if room is None:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.unknown_room", "message": "room not found"})

    data = await file.read()
    if len(data) > room.config.attachments_max_bytes:
        raise HTTPException(413, detail={"code": "org.agentstorming.err.too_large",
                                         "message": "attachment exceeds room limit"})

    info = await state.attachments_store.put(
        room_id=room_id,
        sender_pid=principal.pid,
        filename=file.filename or "attachment",
        data=data,
        content_type=file.content_type or "application/octet-stream",
    )
    sha_bytes = bytes.fromhex(info.sha256_hex)
    await state.attachments.create(
        {
            "id": info.att_id,
            "room_id": room_id,
            "sender_pid": principal.pid,
            "content_type": info.content_type,
            "size_bytes": info.size_bytes,
            "filename": info.filename,
            "sha256": sha_bytes,
            "s3_key": info.s3_key,
            "local_path": info.local_path,
        }
    )
    url = state.attachments_store.presign_get(info)
    return {
        "id": str(info.att_id),
        "url": url,
        "s3_key": info.s3_key,
        "content_type": info.content_type,
        "size_bytes": info.size_bytes,
        "filename": info.filename,
        "sha256": info.sha256_hex,
    }


@router.post("/attachments/refresh")
async def refresh_attachment(
    body: dict, request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    """§15.3 — re-presign an S3 GET URL whose lifetime has expired.

    Unbound to a specific room in the URL; the caller supplies the
    ``s3_key`` (or ``att_id`` for convenience). Requires a valid
    bearer for the room that originally created the attachment. Local
    backend returns the direct ``/v1/attachments/{att_id}`` URL —
    local attachments don't expire.
    """
    state = request.app.state
    att_id_raw = body.get("att_id")
    s3_key = body.get("s3_key")
    if not att_id_raw and not s3_key:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_body",
                                         "message": "att_id or s3_key required"})
    row = None
    if att_id_raw:
        try:
            row = await state.attachments.get(UUID(str(att_id_raw)))
        except Exception:
            raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_att_id"})
    elif s3_key:
        row = await state.attachments.get_by_s3_key(s3_key) if hasattr(state.attachments, "get_by_s3_key") else None
    if row is None:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.not_found"})
    # Authorise: caller must be a member of the room that owns the attachment.
    await state.auth.principal(row["room_id"], authorization)
    if row.get("s3_key"):
        url = state.attachments_store.presign_get(
            type("i", (), {"s3_key": row["s3_key"], "att_id": row["id"]})()
        )
    else:
        # Local backend — no expiring URL; return the canonical path.
        base = public_base_url(request)
        url = f"{base}/v1/attachments/{row['id']}"
    return {
        "id": str(row["id"]),
        "url": url,
        "s3_key": row.get("s3_key"),
        "content_type": row.get("content_type"),
        "size_bytes": int(row.get("size_bytes") or 0),
        "filename": row.get("filename"),
    }


@router.get("/attachments/{att_id}")
async def serve_attachment(att_id: UUID, request: Request,
                           authorization: str | None = Header(default=None)) -> FileResponse:
    state = request.app.state
    row = await state.attachments.get(att_id)
    if not row:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.not_found", "message": "attachment not found"})
    if row.get("local_path"):
        return FileResponse(row["local_path"], media_type=row["content_type"], filename=row["filename"])
    if row.get("s3_key"):
        # Redirect to presigned URL
        from fastapi.responses import RedirectResponse
        url = state.attachments_store.presign_get(type("i", (), {"s3_key": row["s3_key"], "att_id": att_id})())  # minimal shim
        return RedirectResponse(url)
    raise HTTPException(500, detail={"code": "org.agentstorming.err.internal", "message": "attachment has no backing store"})
