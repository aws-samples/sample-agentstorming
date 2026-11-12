# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Attachments upload/serve.

Every URL this router hands out for an attachment is its download route,
``{base}/v1/attachments/{id}``, which checks room membership on each fetch.
The S3 backend used to hand out a presigned S3 URL valid for seven days
instead, and clients copied it into the attachment event, so the URL reached
every member and kept working after an ejection. Presigned URLs are now
issued only by the download route's redirect and by refresh, after the
membership check, and live for ``attachments_presign_ttl_seconds``.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from .deps import is_db_text, public_base_url

router = APIRouter(tags=["attachments"])

#: Authenticated content: no shared cache may keep a copy for the next caller.
_NO_STORE = {"Cache-Control": "private, no-store"}


def _download_url(request: Request, att_id) -> str:
    return f"{public_base_url(request)}/v1/attachments/{att_id}"




def _in_callers_room(row: dict | None, principal) -> dict:
    """The attachment if it belongs to the caller's room; otherwise 404.

    One answer for "no such id" and "another room's id", so a member of one
    room cannot test ids from another. The bearer was checked first, so a
    caller with no valid token got 401 before any lookup.
    """
    if row is None or row["room_id"] != principal.room_id:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.not_found",
                                         "message": "attachment not found"})
    return row


def _presign(state, row: dict) -> str:
    return state.attachments_store.presign_get(
        SimpleNamespace(s3_key=row["s3_key"], att_id=row["id"])
    )


@router.post("/rooms/{room_id}/attachments")
async def upload_attachment(room_id: str, request: Request,
                            file: UploadFile = File(...),
                            authorization: str | None = Header(default=None)) -> dict:
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    room = await state.rooms.get(room_id)
    if room is None:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.unknown_room", "message": "room not found"})

    # attachments.enabled is owner-settable (§5.2); it used to be stored and
    # never consulted, so turning uploads off changed nothing.
    if not room.config.attachments_enabled:
        raise HTTPException(403, detail={"code": "org.agentstorming.err.attachments_disabled",
                                         "message": "attachments are disabled in this room"})

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
    return {
        "id": str(info.att_id),
        "url": _download_url(request, info.att_id),
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
    """§15.4 — a direct download URL, for a current member of the attachment's room.

    Unbound to a specific room in the URL; the caller supplies the
    ``att_id`` (or ``s3_key``). Requires a valid bearer for the room that
    owns the attachment. The S3 backend returns a presigned URL that expires
    after ``attachments_presign_ttl_seconds``; the local backend returns the
    download route, which needs the bearer itself.
    """
    state = request.app.state
    # Same order as the download route: the bearer is verified before anything
    # is looked up, so a caller without a valid token learns nothing about
    # which ids exist.
    principal = await state.auth.principal_for_bearer(authorization)
    att_id_raw = body.get("att_id")
    s3_key = body.get("s3_key")
    if not att_id_raw and not s3_key:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_body",
                                         "message": "att_id or s3_key required"})
    if s3_key is not None and not is_db_text(s3_key):
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_body",
                                         "message": "s3_key must be a string"})
    row = None
    if att_id_raw:
        try:
            att_id = UUID(str(att_id_raw))
        except ValueError:
            raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_att_id"})
        row = await state.attachments.get(att_id)
    elif s3_key:
        row = await state.attachments.get_by_s3_key(s3_key) if hasattr(state.attachments, "get_by_s3_key") else None
    row = _in_callers_room(row, principal)
    if row.get("s3_key"):
        url = _presign(state, row)
        expires_in = getattr(state.attachments_store, "presign_ttl_seconds", None)
    else:
        url = _download_url(request, row["id"])
        expires_in = None
    return {
        "id": str(row["id"]),
        "url": url,
        "expires_in_seconds": expires_in,
        "s3_key": row.get("s3_key"),
        "content_type": row.get("content_type"),
        "size_bytes": int(row.get("size_bytes") or 0),
        "filename": row.get("filename"),
    }


@router.get("/attachments/{att_id}")
async def serve_attachment(att_id: UUID, request: Request,
                           authorization: str | None = Header(default=None)) -> FileResponse:
    """Serve an attachment to a current member of the room that owns it.

    Same rule as ``/attachments/refresh``. This route took the Authorization
    header and never checked it, so an attachment ID alone downloaded the file
    from any room, private ones included, and an ejected member kept access.
    """
    state = request.app.state
    principal = await state.auth.principal_for_bearer(authorization)
    row = _in_callers_room(await state.attachments.get(att_id), principal)
    if row.get("local_path"):
        return FileResponse(row["local_path"], media_type=row["content_type"],
                            filename=row["filename"], headers=_NO_STORE)
    if row.get("s3_key"):
        # The client must not send its bearer to S3: a presigned URL already
        # carries its credential, and S3 rejects a request that has two.
        return RedirectResponse(_presign(state, row), status_code=307, headers=_NO_STORE)
    raise HTTPException(500, detail={"code": "org.agentstorming.err.internal", "message": "attachment has no backing store"})
