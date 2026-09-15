# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""POST /v1/rooms/{room_id}/events — post any participant event."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from ..domain.event import Envelope
from .deps import bearer, idempotency_key

router = APIRouter(tags=["events"])


@router.post("/{room_id}/events")
async def post_event(
    room_id: str,
    envelope: dict[str, Any],
    request: Request,
    authorization: str | None = Header(default=None),
    idem_key: str = Header(default="", alias="Idempotency-Key"),
) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)

    await state.rate_limiter.check(principal.pid, "post")

    # Idempotency check + atomic reservation. A status=0 placeholder
    # row is inserted on first arrival; concurrent requests with the
    # same (idem_key, pid) lose the race and either return the cached
    # final body if it has landed or get told to retry.
    if idem_key:
        cached = await state.idempotency.lookup(idem_key, principal.pid)
        if cached and cached["status"] != 0:
            return cached["body"]
        if cached and cached["status"] == 0:
            # Another concurrent request is still running.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "org.agentstorming.err.idempotency_in_flight",
                    "message": "duplicate idempotency key in flight",
                },
            )
        won = await state.idempotency.reserve(idem_key, principal.pid)
        if not won:
            # Lost the race — re-lookup; if still pending, signal retry.
            cached = await state.idempotency.lookup(idem_key, principal.pid)
            if cached and cached["status"] != 0:
                return cached["body"]
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "org.agentstorming.err.idempotency_in_flight",
                    "message": "duplicate idempotency key in flight",
                },
            )

    env = Envelope.model_validate(envelope)
    committed = await state.post_event.post(principal, env, raw_dict=envelope)

    # Build the response envelope from the raw dict + server-assigned
    # seq/ts_server so the caller can verify its own signature too.
    response_env = dict(envelope)
    response_env["seq"] = committed.seq
    response_env["ts_server"] = committed.ts_server if isinstance(committed.ts_server, str) else (
        committed.ts_server.isoformat() if committed.ts_server else None
    )
    response = {"envelope": response_env, "seq": committed.seq}
    if idem_key:
        await state.idempotency.save(idem_key, principal.pid, 201, response)
    return response
