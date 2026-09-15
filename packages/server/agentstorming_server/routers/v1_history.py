# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""GET /v1/rooms/{id}/messages — paginated history.

Supports both seq-based and timestamp-based paging:

- ``?from=<seq>&to=<seq>`` — legacy seq range.
- ``?from_ts=<ISO8601>&to_ts=<ISO8601>&cursor=<seq>`` — time-window
  paging. Results have ``seq > cursor``; the response ``continue_from``
  is the next cursor to pass on the subsequent call. Both ``from_ts``
  and ``to_ts`` are optional — omitting ``from_ts`` means "from the
  beginning," omitting ``to_ts`` means "up to now."
- ``?types=foo,bar`` — optional comma-separated type filter.

The response envelope is:

```json
{
  "events": [...],
  "returned": N,
  "has_more": true|false,
  "continue_from": <seq or null>
}
```
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Query, Request

from ..services.visibility import event_visible_to

router = APIRouter(tags=["history"])


def _parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"code": "org.agentstorming.err.ts_invalid", "field": value},
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/{room_id}/messages")
async def messages(
    room_id: str,
    request: Request,
    from_: int = Query(0, alias="from"),
    to: int | None = Query(None),
    from_ts: str | None = Query(None),
    to_ts: str | None = Query(None),
    cursor: int | None = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
    types: str | None = Query(None),
    authorization: str | None = Header(default=None),
) -> dict:
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    ignore_map: dict[str, str] | None = None
    if getattr(principal.participant, "affiliation", None) in ("original-moderator", "room-owner"):
        ignore_map = await state.ignore_entries.fetch_map(room_id, principal.pid) or None
    ttypes = [t.strip() for t in types.split(",")] if types else None
    room = await state.rooms.get(room_id)
    cap = room.config.history_max_return if room else 1000
    limit = min(limit, cap)

    if from_ts is not None or to_ts is not None:
        f_ts = _parse_ts(from_ts)
        t_ts = _parse_ts(to_ts)
        events = await state.events.get_range_by_time(
            room_id, f_ts, t_ts, limit, ttypes, cursor=cursor,
        )
    else:
        events = await state.events.get_range(room_id, from_, to, limit, ttypes)

    # Same whisper filter as /stream and /sync.
    events = [e for e in events if event_visible_to(e, principal.pid, ignore_map=ignore_map)]

    next_from = None
    has_more = len(events) >= limit
    if events and has_more:
        next_from = int(events[-1]["seq"]) + 1
    return {
        "events": events,
        "returned": len(events),
        "has_more": has_more,
        "continue_from": next_from,
    }


@router.get("/{room_id}/snapshot")
async def snapshot(room_id: str, request: Request,
                   authorization: str | None = Header(default=None)) -> dict:
    state = request.app.state
    _ = await state.auth.principal(room_id, authorization)
    env = await state.snapshot.build_envelope(
        room_id, sign_func=lambda d: state.sys_sign(room_id, d),
    )
    return env.model_dump(mode="json")


@router.get("/{room_id}/hands")
async def list_hands(room_id: str, request: Request,
                     authorization: str | None = Header(default=None)) -> dict:
    state = request.app.state
    _ = await state.auth.principal(room_id, authorization)
    return {"hands": await state.hands.list_active(room_id)}
