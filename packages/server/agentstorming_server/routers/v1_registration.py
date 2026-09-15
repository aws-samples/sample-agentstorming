# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Public registration + interview endpoints (§8.3-§8.4, §9.3).

Flow:

1. Candidate POSTs to ``/v1/rooms/{id}/register`` with their pubkey,
   declared capabilities, and optional runs_as flag. Room MUST be
   ``visibility=public`` and its registration door MUST NOT be closed.
2. Server creates an ``interviews`` row in state=PENDING, admits the
   candidate with affiliation ``pending-interview``, emits a
   ``registration_request`` event targeted at the current moderator.
3. Moderator and candidate exchange ``org.agentstorming.whisper``
   events scoped to their two pids — not broadcast.
4. Moderator calls ``/v1/rooms/{id}/moderation/registrations/{iid}/accept``
   or ``.../reject``. Affiliation transitions to ``member`` or
   ``ejected`` respectively; ``registration_accepted`` /
   ``registration_rejected`` events broadcast to the room.
5. The moderator may call ``/v1/rooms/{id}/moderation/registration-door``
   to close or re-open the registration door for a duration.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..domain.event import (
    TYPE_INTERVIEW_ENDED,
    TYPE_INTERVIEW_STARTED,
    TYPE_PARTICIPANT_JOINED,
    TYPE_REGISTRATION_ACCEPTED,
    TYPE_REGISTRATION_REJECTED,
    TYPE_REGISTRATION_REQUEST,
)
from ..domain.participant import Participant
from ..domain.ids import make_pid
from ..services import sig as sig_mod
from ..services.jcs import canonicalise

router = APIRouter(tags=["registration"])

_MAX_DOOR_CLOSE_SECONDS = 30 * 86_400
_MAX_CLOCK_SKEW_SECONDS = 300


def _b64url_decode(s: str) -> bytes:
    import base64
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _verify_registration_ts(ts_str: str) -> None:
    """Reject signed candidate requests whose clock is more than ±5min off."""
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.ts_invalid"})
    if abs((datetime.now(timezone.utc) - ts).total_seconds()) > _MAX_CLOCK_SKEW_SECONDS:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.ts_skew"})


class RegisterBody(BaseModel):
    pubkey: str = Field(..., description="candidate Ed25519 pubkey, base64url")
    runs_as: Literal["agent", "human"] = "agent"
    declared: str = Field(default="", max_length=2000,
                          description="freeform: what the candidate claims to contribute")


class RegisterResponse(BaseModel):
    interview_id: str
    candidate_pid: str
    room_id: str
    state: str


@router.post("/{room_id}/register", response_model=RegisterResponse)
async def register(room_id: str, body: RegisterBody, request: Request) -> RegisterResponse:
    state = request.app.state
    room = await state.rooms.get(room_id)
    if room is None:
        raise HTTPException(
            status_code=404, detail={"code": "org.agentstorming.err.room_unknown"}
        )
    if room.config.visibility != "public":
        raise HTTPException(
            status_code=403,
            detail={"code": "org.agentstorming.err.room_private",
                    "message": "registration only available for public rooms"},
        )

    # Door open?
    door = await state.registration_doors.get_closed(room_id)
    if door is not None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "org.agentstorming.err.registration_closed",
                "reopens_at": door["reopens_at"].isoformat(),
            },
        )

    try:
        pubkey = _b64url_decode(body.pubkey)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.bad_pubkey"})
    if len(pubkey) != 32:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.bad_pubkey"})

    # §5.2 — enforce max_participants.
    if room.config.max_participants:
        active = await state.participants.count_active(room_id)
        if active >= room.config.max_participants:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "org.agentstorming.err.room_full",
                    "message": f"room is at capacity ({room.config.max_participants})",
                },
            )

    # §12.3 — penned pubkeys cannot re-register in ANY room until the
    # pen elapses, not just the room they were penned in.
    if await state.participants.is_pubkey_penned(pubkey):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "org.agentstorming.err.pen_active",
                "message": "this pubkey is currently penned",
            },
        )

    # Two-layer rate limit: per-pubkey (slows repeat candidates) and
    # per-client-IP (slows pubkey-rotation spray). Both share the same
    # `register` bucket config.
    client_ip = request.client.host if request.client else "unknown"
    await state.rate_limiter.check(f"pub:{body.pubkey}", "register")
    await state.rate_limiter.check(f"ip:{client_ip}", "register")

    # Optional operator-provided filter hook (abuse / bot-detection).
    filter_fn = getattr(state, "registration_filter", None)
    if filter_fn is not None:
        meta = {
            "room_id": room_id,
            "client_ip": client_ip,
            "user_agent": request.headers.get("user-agent"),
        }
        try:
            verdict = filter_fn(body.model_dump(), meta)
            if asyncio.iscoroutine(verdict):
                verdict = await verdict
        except Exception:
            # Fail-closed: if the filter errors, reject the registration
            # rather than leaking past a broken operator policy.
            raise HTTPException(
                status_code=503,
                detail={"code": "org.agentstorming.err.registration_filter_error"},
            )
        if verdict and isinstance(verdict, dict) and verdict.get("deny"):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "org.agentstorming.err.registration_denied",
                    "reason": str(verdict["deny"]),
                },
            )

    pid = make_pid(pubkey, room_id)
    if await state.participants.get(room_id, pid) is not None:
        raise HTTPException(
            status_code=409, detail={"code": "org.agentstorming.err.already_member"}
        )

    now = datetime.now(timezone.utc)
    candidate = Participant(
        room_id=room_id, pid=pid, pubkey=pubkey, affiliation="pending-interview",
        deputy_rank=None, joined_at=now, last_seen_at=now, runs_as=body.runs_as,
    )
    await state.participants.insert(candidate)

    interview_id = secrets.token_urlsafe(16)
    # Route the knock at whoever actually holds the moderating seat — the
    # original-moderator, or the deputy who has been promoted into it.
    # Fall back to the room-owner so a knock is never emitted with no
    # recipient at all: registration_request is whisper-class (§12.1a), so
    # a payload with no routing field would be invisible to every viewer
    # including the moderator, and the candidate would wait forever.
    mod = await state.participants.get_acting_moderator(room_id)
    if mod is None:
        mod = await state.participants.get_by_affiliation(room_id, "room-owner")
    await state.interviews.create(
        interview_id=interview_id,
        room_id=room_id,
        candidate_pid=pid,
        moderator_pid=mod.pid if mod else None,
        declared=body.declared,
    )
    await state.sys_publish_event(
        room_id, TYPE_REGISTRATION_REQUEST,
        payload={
            "interview_id": interview_id,
            "candidate_pid": pid,
            "declared": body.declared,
            "runs_as": body.runs_as,
            # Routing fields consumed by services/visibility.py. `pid` lets
            # the candidate see their own knock; `target_pid` delivers it to
            # the moderator.
            "pid": pid,
            "target_pid": mod.pid if mod else None,
        },
    )
    # §7.2 interview_started — targeted to candidate + moderator. Visible
    # only to those two via the whisper filter (target_pid / pid match).
    await state.sys_publish_event(
        room_id, TYPE_INTERVIEW_STARTED,
        payload={
            "interview_id": interview_id,
            "candidate_pid": pid,
            "moderator_pid": mod.pid if mod else None,
            "pid": pid,
            "target_pid": mod.pid if mod else pid,
        },
    )
    return RegisterResponse(
        interview_id=interview_id, candidate_pid=pid, room_id=room_id, state="PENDING",
    )


class RegistrationNonceResponse(BaseModel):
    nonce: str
    expires_at: str


@router.get("/{room_id}/register/nonce", response_model=RegistrationNonceResponse)
async def issue_registration_nonce(room_id: str, request: Request) -> RegistrationNonceResponse:
    """One-shot 60s nonce for the candidate's signed credential request.

    Shares the server's signed-request nonce store with the owner surface
    (ADR-004); a nonce is a bare unguessable token with no authority of its
    own, so the store is deliberately common. Unauthenticated by necessity —
    the caller has no bearer token yet — and rate-limited per client IP.
    """
    state = request.app.state
    client_ip = request.client.host if request.client else "unknown"
    await state.rate_limiter.check(f"ip:{client_ip}", "register")
    nonce, expires = await state.owner_nonces.issue()
    return RegistrationNonceResponse(nonce=nonce, expires_at=expires.isoformat())


class RegistrationCredentialBody(BaseModel):
    """Signed request by which an accepted candidate collects its tokens.

    The candidate proves possession of the private key behind the pubkey it
    registered with, so no bearer secret has to be handed out at knock time
    and none has to travel through the moderator. Same construction as the
    owner-signed surface (ADR-004): canonicalise everything except
    ``pubkey``/``sig``, Ed25519-sign it, and bind a one-shot nonce.
    """

    interview_id: str
    nonce: str
    ts: str
    pubkey: str
    sig: str


@router.post("/{room_id}/register/credentials")
async def collect_registration_credentials(
    room_id: str, body: RegistrationCredentialBody, request: Request,
) -> dict[str, Any]:
    """§9.5 — issue access + refresh tokens to an ACCEPTED candidate.

    Acceptance alone leaves the candidate a member with no way to act: it
    flips the affiliation but mints no credential, and every other endpoint
    demands a bearer token. This is the missing half of the public
    registration flow. The candidate polls here after seeing
    ``registration_accepted`` (or after any interview whisper) and exchanges
    a signature for the token pair.

    Returns 409 while the interview is still PENDING/ACTIVE so a candidate
    can poll without special-casing, and 403 once rejected.
    """
    state = request.app.state
    await state.rate_limiter.check(f"pub:{body.pubkey}", "register")

    _verify_registration_ts(body.ts)
    try:
        pubkey = _b64url_decode(body.pubkey)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.bad_pubkey"})
    if len(pubkey) != 32:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.bad_pubkey"})

    signed = body.model_dump(exclude={"pubkey", "sig"})
    if not sig_mod.verify_blob(canonicalise(signed), body.sig, pubkey):
        raise HTTPException(status_code=403, detail={"code": "org.agentstorming.err.sig_invalid"})

    interview = await state.interviews.get(body.interview_id)
    if not interview or interview["room_id"] != room_id:
        raise HTTPException(
            status_code=404, detail={"code": "org.agentstorming.err.interview_unknown"}
        )

    # The signature must belong to the candidate this interview is about —
    # not merely to some valid keypair.
    if interview["candidate_pid"] != make_pid(pubkey, room_id):
        raise HTTPException(
            status_code=403,
            detail={"code": "org.agentstorming.err.forbidden",
                    "message": "signature does not match the interview candidate"},
        )

    if interview["state"] in ("PENDING", "ACTIVE"):
        raise HTTPException(
            status_code=409,
            detail={"code": "org.agentstorming.err.interview_pending",
                    "message": "the moderator has not decided yet"},
        )
    if interview["state"] != "ACCEPTED":
        raise HTTPException(
            status_code=403,
            detail={"code": "org.agentstorming.err.registration_rejected",
                    "message": f"interview state={interview['state']}"},
        )

    participant = await state.participants.get(room_id, interview["candidate_pid"])
    if participant is None or participant.affiliation not in ("member", "original-moderator"):
        raise HTTPException(
            status_code=409,
            detail={"code": "org.agentstorming.err.not_admitted",
                    "message": "candidate is not an admitted member"},
        )

    # One-shot nonce, consumed last so a replay cannot burn a nonce before
    # the request has otherwise been proven valid.
    if not await state.owner_nonces.consume(body.nonce):
        raise HTTPException(
            status_code=409, detail={"code": "org.agentstorming.err.nonce_invalid"}
        )

    pair = await state.tokens.issue_pair(
        room_id,
        interview["candidate_pid"],
        state.settings.access_token_ttl_seconds,
        state.settings.refresh_token_ttl_seconds,
    )
    snap_env = await state.snapshot.build_envelope(
        room_id, sign_func=lambda d: state.sys_sign(room_id, d),
    )
    return {
        "pid": interview["candidate_pid"],
        "room_id": room_id,
        "affiliation": participant.affiliation,
        "access_token": pair["access_token"],
        "refresh_token": pair["refresh_token"],
        "access_expires_at": pair["access_expires_at"].isoformat(),
        "refresh_expires_at": pair["refresh_expires_at"].isoformat(),
        "snapshot": snap_env,
    }


class RegistrationDecision(BaseModel):
    interview_id: str
    reason: str | None = None


@router.post("/{room_id}/moderation/registrations/{iid}/accept")
async def accept_registration(
    room_id: str, iid: str, body: RegistrationDecision, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    if principal.participant.affiliation != "original-moderator":
        raise HTTPException(
            status_code=403, detail={"code": "org.agentstorming.err.forbidden"}
        )
    interview = await state.interviews.get(iid)
    if not interview or interview["room_id"] != room_id:
        raise HTTPException(
            status_code=404, detail={"code": "org.agentstorming.err.interview_unknown"}
        )
    if interview["state"] not in ("PENDING", "ACTIVE"):
        raise HTTPException(
            status_code=409, detail={"code": "org.agentstorming.err.interview_decided"}
        )
    await state.interviews.decide(iid, "ACCEPTED")
    await state.participants.set_affiliation(room_id, interview["candidate_pid"], "member")
    await state.sys_publish_event(
        room_id, TYPE_REGISTRATION_ACCEPTED,
        payload={"interview_id": iid, "pid": interview["candidate_pid"]},
    )
    await state.sys_publish_event(
        room_id, TYPE_INTERVIEW_ENDED,
        payload={
            "interview_id": iid,
            "candidate_pid": interview["candidate_pid"],
            "moderator_pid": interview.get("moderator_pid") or principal.pid,
            "pid": interview["candidate_pid"],
            "target_pid": interview.get("moderator_pid") or principal.pid,
            "outcome": "accepted",
        },
    )
    return {"ok": True}


@router.post("/{room_id}/moderation/registrations/{iid}/reject")
async def reject_registration(
    room_id: str, iid: str, body: RegistrationDecision, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    if principal.participant.affiliation != "original-moderator":
        raise HTTPException(
            status_code=403, detail={"code": "org.agentstorming.err.forbidden"}
        )
    interview = await state.interviews.get(iid)
    if not interview or interview["room_id"] != room_id:
        raise HTTPException(
            status_code=404, detail={"code": "org.agentstorming.err.interview_unknown"}
        )
    if interview["state"] not in ("PENDING", "ACTIVE"):
        raise HTTPException(
            status_code=409, detail={"code": "org.agentstorming.err.interview_decided"}
        )
    await state.interviews.decide(iid, "REJECTED")
    await state.participants.set_affiliation(room_id, interview["candidate_pid"], "ejected")
    await state.participants.set_left(
        room_id, interview["candidate_pid"], datetime.now(timezone.utc)
    )
    await state.sys_publish_event(
        room_id, TYPE_REGISTRATION_REJECTED,
        payload={
            "interview_id": iid,
            "pid": interview["candidate_pid"],
            "reason": body.reason,
        },
    )
    await state.sys_publish_event(
        room_id, TYPE_INTERVIEW_ENDED,
        payload={
            "interview_id": iid,
            "candidate_pid": interview["candidate_pid"],
            "moderator_pid": interview.get("moderator_pid") or principal.pid,
            "pid": interview["candidate_pid"],
            "target_pid": interview.get("moderator_pid") or principal.pid,
            "outcome": "rejected",
            "reason": body.reason,
        },
    )
    return {"ok": True}


class DoorCloseBody(BaseModel):
    duration_seconds: int = Field(..., ge=1, le=_MAX_DOOR_CLOSE_SECONDS)
    reason: str | None = None


@router.post("/{room_id}/moderation/registration-door/close")
async def close_registration_door(
    room_id: str, body: DoorCloseBody, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    if principal.participant.affiliation != "original-moderator":
        raise HTTPException(
            status_code=403, detail={"code": "org.agentstorming.err.forbidden"}
        )
    reopens = datetime.now(timezone.utc) + timedelta(seconds=body.duration_seconds)
    await state.registration_doors.close(room_id, reopens, body.reason)
    return {"room_id": room_id, "reopens_at": reopens.isoformat()}


@router.post("/{room_id}/moderation/registration-door/open")
async def open_registration_door(
    room_id: str, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    if principal.participant.affiliation != "original-moderator":
        raise HTTPException(
            status_code=403, detail={"code": "org.agentstorming.err.forbidden"}
        )
    await state.registration_doors.open(room_id)
    return {"room_id": room_id, "state": "open"}
