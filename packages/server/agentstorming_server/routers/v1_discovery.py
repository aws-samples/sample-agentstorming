# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Public room discovery.

``GET /v1/rooms`` returns every active room with
``config.visibility == 'public'``. Unauthenticated.

The payload intentionally excludes anything that would leak the
internal state of a room to passers-by — no pids, no pubkeys, no
summaries, no moderator identity. Callers who want richer info must
redeem an invite or go through public registration (``§8.3``) first.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["discovery"])


@router.get("/rooms")
async def list_public_rooms(request: Request) -> dict[str, Any]:
    state = request.app.state
    rooms = await state.rooms.list_public_active()
    parts_repo = state.participants
    items = []
    for r in rooms:
        try:
            pct = await parts_repo.count_active(r.id)
        except Exception:
            pct = None
        items.append(
            {
                "room_id": r.id,
                "title": r.title or r.id,
                "description": r.description or "",
                "visibility": r.config.visibility,
                "state": r.state,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "raise_hand_required": r.config.raise_hand_required,
                "max_participants": r.config.max_participants,
                "participant_count": pct,
            }
        )
    return {"rooms": items, "count": len(items)}
