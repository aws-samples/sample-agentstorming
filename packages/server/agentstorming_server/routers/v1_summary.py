# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Rolling summary PUT."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from ..domain.event import TYPE_SUMMARY_UPDATED

router = APIRouter(tags=["summary"])


@router.put("/{room_id}/summary")
async def set_summary(room_id: str, body: dict, request: Request,
                      authorization: str | None = Header(default=None)) -> dict:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    text = body.get("text", "")
    await state.summaries.set(room_id, text, principal.pid)
    await state.sys_publish_event(
        room_id, TYPE_SUMMARY_UPDATED,
        payload={"updated_by": principal.pid, "text_length": len(text)},
    )
    return {"ok": True}
