# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""SSE event stream.

``GET /v1/rooms/{room_id}/stream`` returns a text/event-stream that:

1. Immediately sends the initial metadata snapshot (id=``snapshot``).
2. Replays any events with seq > since (Last-Event-ID header or ``?since`` query param).
3. Streams new events as Postgres NOTIFY fires.
4. Emits ``:keepalive`` comments every 15 seconds so intermediaries
   (ALB, CloudFront, corporate proxies) don't kill idle connections.

This replaces the old ``/sync`` long-poll endpoint. Clients reconnect
automatically via EventSource's ``Last-Event-ID`` header — our cursor
is the ``seq`` integer, same as the long-poll ``?since`` query.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.sse import EventSourceResponse

from ..services.visibility import event_visible_to as _event_visible_to

log = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])

KEEPALIVE_SECONDS = 15


@router.get("/{room_id}/stream")
async def stream(
    room_id: str,
    request: Request,
    since: int = Query(-1),
    limit: int = Query(200, ge=1, le=1000),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    authorization: str | None = Header(default=None),
) -> EventSourceResponse:
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    await state.rate_limiter.check(principal.pid, "stream")

    # Moderators MAY have a personal ignore list (§12.1a). Loaded once
    # per stream connection; updates mid-stream become visible only on
    # reconnect. Non-moderators always see the full (whisper-filtered)
    # stream.
    ignore_map: dict[str, str] | None = None
    if getattr(principal.participant, "affiliation", None) in ("original-moderator", "room-owner"):
        ignore_map = await state.ignore_entries.fetch_map(room_id, principal.pid) or None

    # Effective cursor: explicit ?since query beats header; header is used
    # by auto-reconnecting browser EventSource.
    cursor = since
    if since < 0 and last_event_id is not None:
        try:
            cursor = int(last_event_id)
        except ValueError:
            cursor = -1

    async def event_generator() -> AsyncIterator[dict[str, Any]]:
        # 1. Snapshot on first connection.
        if cursor < 0:
            snap = await state.snapshot.build_raw_dict(
                room_id,
                sign_func=lambda env_dict: state.sys_sign(room_id, env_dict),
            )
            yield {
                "id": "snapshot",
                "event": "snapshot",
                "data": json.dumps(snap),
            }

        # 2. Subscribe first, then replay, so an event committed between the
        #    replay query and the subscription still wakes the loop below.
        #    Subscribing afterwards, as this used to, held such an event until
        #    some later one arrived.
        q = state.pubsub.subscribe(room_id)
        last_seq = cursor

        async def catch_up():
            """Every event after last_seq, page by page, not just one page.

            One page left a client more than `limit` events behind with the
            rest until something else woke the stream."""
            nonlocal last_seq
            while True:
                page = await state.events.get_after(room_id, last_seq, limit)
                out = []
                for ev in page:
                    seq = int(ev.get("seq", -1))
                    if seq <= last_seq:
                        continue
                    # The replay and live paths share one ignore-map filter;
                    # otherwise live whispers from a moderator-ignored
                    # participant slip through until reconnect.
                    if _event_visible_to(ev, principal.pid, ignore_map=ignore_map):
                        out.append({"id": str(seq), "event": "event", "data": json.dumps(ev)})
                    last_seq = seq
                yield out
                if len(page) < limit:
                    return

        try:
            async for batch in catch_up():
                for item in batch:
                    yield item

            # 3. Stream live events, with periodic keep-alive.
            while True:
                if await request.is_disconnected():
                    return
                try:
                    await asyncio.wait_for(q.get(), timeout=KEEPALIVE_SECONDS)
                    while not q.empty():
                        q.get_nowait()
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
                    # A quiet interval is also when a lost NOTIFY would go
                    # unnoticed, so look for anything missed.
                async for batch in catch_up():
                    for item in batch:
                        yield item
        finally:
            state.pubsub.unsubscribe(room_id, q)

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "X-Accel-Buffering": "no",
            "Content-Encoding": "identity",
        },
    )


@router.get("/{room_id}/sync")
async def sync_compat(
    room_id: str,
    request: Request,
    since: int = Query(-1),
    wait: int = Query(0, ge=0, le=60),
    limit: int = Query(200, ge=1, le=1000),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """One-shot history fetch (no streaming).

    Retained as a small, lightweight compatibility path for callers that
    prefer a single JSON response over SSE (e.g. synchronous scripts,
    tests, curl debugging). Unlike the old long-poll ``/sync``, this
    endpoint returns immediately — no holding-open semantics.
    """
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    await state.rate_limiter.check(principal.pid, "sync")

    ignore_map: dict[str, str] | None = None
    if getattr(principal.participant, "affiliation", None) in ("original-moderator", "room-owner"):
        ignore_map = await state.ignore_entries.fetch_map(room_id, principal.pid) or None

    events = await state.events.get_after(room_id, since, limit)
    # The cursor advances over the page as read, not as shown: computed after
    # the visibility filter, a page made only of other people's whispers left
    # it where it was, and the client asked for the same page forever.
    raw_last = max((e.get("seq") for e in events if isinstance(e.get("seq"), int)), default=since)
    if since < 0:
        snap = await state.snapshot.build_raw_dict(
            room_id,
            sign_func=lambda env_dict: state.sys_sign(room_id, env_dict),
        )
        events = [snap] + events

    # Whisper-class events MUST be dropped for viewers who aren't the
    # sender or the addressed target. Same filter as /stream.
    events = [e for e in events if _event_visible_to(e, principal.pid, ignore_map=ignore_map)]

    next_since = max(since, raw_last)

    return {
        "events": events,
        "next_since": next_since,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }
