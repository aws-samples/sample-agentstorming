# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Moderator + grant endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from ..domain.event import (
    TYPE_AFFILIATION_CHANGED,
    TYPE_GO_SPEAK_GRANTED,
    TYPE_MODE_PROMOTED,
    TYPE_MODERATOR_CHANGED,
    TYPE_MUTE,
    TYPE_PARTICIPANT_LEFT,
    TYPE_UNMUTE,
)
from ..domain.participant import affiliation_power_level
from .deps import MAX_INVITE_TTL_SECONDS, invite_link, is_db_text, public_base_url

router = APIRouter(tags=["moderation"])

#: The protocol's longest duration (§12.3), and far below where adding it to
#: now overflows a datetime: a mute of 10**12 seconds used to be a 500.
MAX_DURATION_SECONDS = 31_536_000
_MAX_PID_LENGTH = 512
_MAX_REASON_LENGTH = 1_000


# These bodies are read by hand, and int() on "abc", UUID() on 5 or a pid
# holding a NUL each raised inside the handler, so a typo answered 500.


def _bad_body(message: str) -> HTTPException:
    return HTTPException(400, detail={"code": "org.agentstorming.err.bad_body", "message": message})


def _target_pid(body: dict[str, Any]) -> str:
    v = body.get("target_pid")
    if not is_db_text(v) or not v or len(v) > _MAX_PID_LENGTH:
        raise _bad_body("target_pid must be a participant id")
    return v


def _int_field(body: dict[str, Any], name: str, lo: int, hi: int, *, required: bool) -> int | None:
    """An integer in ``lo..hi``; absent or null gives None unless required.

    JSON integers only: not "60", not 60.0, not true.
    """
    v = body.get(name)
    if v is None:
        if required:
            raise _bad_body(f"{name} required")
        return None
    if type(v) is not int or not lo <= v <= hi:
        raise _bad_body(f"{name} must be an integer from {lo} to {hi}")
    return v


def _uuid_field(body: dict[str, Any], name: str) -> UUID | None:
    v = body.get(name)
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return UUID(v)
        except ValueError:
            pass
    raise _bad_body(f"{name} must be a UUID")


async def _target(state, room_id: str, pid: str):
    p = await state.participants.get(room_id, pid)
    if p is None:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.unknown_participant",
                                         "message": "participant not found"})
    return p


async def _outranks(state, room_id: str, principal, target_pid: str, new_power: int | None):
    """§6.3: P may change Q only while P's power level is strictly above Q's,
    and, for an affiliation change, strictly above the one Q is given.

    Eject, pen, mute and deputy assignment checked only that the caller held
    the seat, so the moderator (90) could eject, pen, mute or demote the room
    owner (100), whose seat outranks it.
    """
    target = await _target(state, room_id, target_pid)
    mine = principal.participant.power_level
    if mine <= target.power_level or (new_power is not None and mine <= new_power):
        raise HTTPException(403, detail={
            "code": "org.agentstorming.err.outranked",
            "message": "the target's power level is not below the caller's (§6.3)",
        })
    return target


@router.post("/{room_id}/grants")
async def grant_turn(room_id: str, body: dict[str, Any], request: Request,
                     authorization: str | None = Header(default=None)) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)

    target_pid = _target_pid(body)
    hand_uuid = _uuid_field(body, "hand_id")
    ttl_seconds = _int_field(body, "ttl_seconds", 1, MAX_DURATION_SECONDS, required=False)
    await _target(state, room_id, target_pid)
    room = await state.rooms.get(room_id)
    if ttl_seconds is None:
        ttl_seconds = room.config.go_speak_ttl_seconds

    ttl_expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    try:
        grant_id = await state.grants.create(room_id, target_pid, hand_uuid, ttl_expires_at)
    except ValueError as e:
        raise HTTPException(409, detail={"code": "org.agentstorming.err.grant_conflict", "message": str(e)})

    if hand_uuid:
        await state.hands.mark_granted(room_id, hand_uuid)

    await state.sys_publish_event(
        room_id, TYPE_GO_SPEAK_GRANTED,
        payload={
            "grant_id": str(grant_id),
            "pid": target_pid,
            "hand_id": str(hand_uuid) if hand_uuid else None,
            "ttl_expires_at": ttl_expires_at.isoformat(),
        },
    )
    return {"grant_id": str(grant_id), "ttl_expires_at": ttl_expires_at.isoformat()}


@router.post("/{room_id}/moderation/mute")
async def mute(room_id: str, body: dict[str, Any], request: Request,
               authorization: str | None = Header(default=None)) -> dict:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)

    target_pid = _target_pid(body)
    duration = _int_field(body, "duration_seconds", 1, MAX_DURATION_SECONDS, required=True)
    await _outranks(state, room_id, principal, target_pid, None)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=duration)
    await state.mutes.mute(room_id, target_pid, now, expires)

    # §15 resolution: muted event is targeted to the mutee + moderator only.
    # The whisper filter in v1_stream matches on payload.target_pid.
    await state.sys_publish_event(
        room_id, TYPE_MUTE,
        payload={
            "pid": target_pid,
            "target_pid": target_pid,
            "duration_seconds": duration,
            "expires_at": expires.isoformat(),
        },
    )
    return {"ok": True, "expires_at": expires.isoformat()}


@router.post("/{room_id}/moderation/eject")
async def eject(room_id: str, body: dict[str, Any], request: Request,
                authorization: str | None = Header(default=None)) -> dict:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    target_pid = _target_pid(body)
    await _outranks(state, room_id, principal, target_pid, affiliation_power_level("ejected", None))

    await state.participants.set_affiliation(room_id, target_pid, "ejected", None)
    await state.participants.set_left(room_id, target_pid, datetime.now(timezone.utc))
    await state.tokens.revoke_all_for_pid(room_id, target_pid)

    await state.sys_publish_event(
        room_id, TYPE_PARTICIPANT_LEFT,
        payload={"pid": target_pid, "reason": "ejected"},
    )
    return {"ok": True}


@router.post("/{room_id}/moderation/pen")
async def pen(room_id: str, body: dict[str, Any], request: Request,
              authorization: str | None = Header(default=None)) -> dict:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    target_pid = _target_pid(body)
    max_pen = MAX_DURATION_SECONDS
    room = await state.rooms.get(room_id)
    if room is not None:
        max_pen = min(max_pen, room.config.pen_max_duration_seconds)
    duration = _int_field(body, "duration_seconds", 1, max_pen, required=True)
    await _outranks(state, room_id, principal, target_pid, affiliation_power_level("penned", None))
    until = datetime.now(timezone.utc) + timedelta(seconds=duration)
    await state.participants.set_affiliation(room_id, target_pid, "penned", None)
    await state.participants.set_penned_until(room_id, target_pid, until)
    await state.participants.set_left(room_id, target_pid, datetime.now(timezone.utc))
    await state.tokens.revoke_all_for_pid(room_id, target_pid)

    await state.sys_publish_event(
        room_id, TYPE_AFFILIATION_CHANGED,
        payload={"pid": target_pid, "new_affiliation": "penned", "penned_until": until.isoformat()},
    )
    return {"ok": True, "penned_until": until.isoformat()}


@router.post("/{room_id}/deputies")
async def assign_deputy(room_id: str, body: dict[str, Any], request: Request,
                        authorization: str | None = Header(default=None)) -> dict:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    room = await state.rooms.get(room_id)
    if room is not None and not room.config.deputies_enabled:
        raise HTTPException(
            409, detail={"code": "org.agentstorming.err.deputies_disabled",
                         "message": "deputies_enabled=false for this room"},
        )
    target_pid = _target_pid(body)
    rank = _int_field(body, "rank", 1, 10, required=True)
    await _outranks(state, room_id, principal, target_pid, affiliation_power_level("member", rank))

    existing = await state.participants.get_deputy(room_id, rank)
    if existing and existing.pid != target_pid:
        raise HTTPException(409, detail={"code": "org.agentstorming.err.rank_taken",
                                         "message": f"rank {rank} already held by {existing.pid}"})

    # Remove any prior rank held by target, then set affiliation=member with new rank.
    await state.participants.set_affiliation(room_id, target_pid, "member", rank)

    await state.sys_publish_event(
        room_id, TYPE_AFFILIATION_CHANGED,
        payload={"pid": target_pid, "new_affiliation": "member", "deputy_rank": rank},
    )
    return {"ok": True}


@router.post("/{room_id}/moderation/reclaim")
async def reclaim(room_id: str, request: Request,
                  authorization: str | None = Header(default=None)) -> dict:
    """Claim / reclaim the acting moderator seat.

    Allowed for:

    - ``room-owner`` — the human owner always outranks the current moderator.
    - ``original-moderator`` — the original claimer has a permanent reclaim right.

    This endpoint powers the SPA's "Claim moderator" button as well as the
    native-agent's programmatic moderator-reclaim flow.
    """
    state = request.app.state
    principal = await state.auth.principal(room_id, authorization)
    aff = principal.participant.affiliation
    if aff not in ("original-moderator", "room-owner"):
        raise HTTPException(
            403,
            detail={
                "code": "org.agentstorming.err.forbidden",
                "message": "only original-moderator or room-owner can reclaim",
            },
        )
    await state.sys_publish_event(
        room_id, TYPE_MODERATOR_CHANGED,
        payload={"new_moderator_pid": principal.pid, "reason": "reclaim"},
    )
    return {"ok": True, "moderator_pid": principal.pid}


@router.post("/{room_id}/grants/{grant_id}/extend")
async def extend_grant(room_id: str, grant_id: str, body: dict[str, Any], request: Request,
                       authorization: str | None = Header(default=None)) -> dict:
    """§6.7.6 — moderator extends a grant's TTL so the grantee can keep speaking.

    Mints a new grant pointing at the same hand+pid, returns the new
    grant_id. The old grant is superseded (next post accepts either).
    """
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    await state.rate_limiter.check(principal.pid, "grants_extend")
    try:
        old_uuid = UUID(grant_id)
    except ValueError:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_grant"})

    ttl_seconds = _int_field(body, "ttl_seconds", 1, MAX_DURATION_SECONDS, required=False)
    room = await state.rooms.get(room_id)
    if ttl_seconds is None:
        ttl_seconds = room.config.go_speak_ttl_seconds

    ttl_expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    try:
        new_grant = await state.grants.extend(room_id, old_uuid, ttl_expires_at)
    except ValueError as e:
        raise HTTPException(409, detail={"code": "org.agentstorming.err.grant_conflict", "message": str(e)})
    if new_grant is None:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.grant_unknown"})

    await state.sys_publish_event(
        room_id,
        "org.agentstorming.speaking_extension_granted",
        payload={
            "previous_grant_id": grant_id,
            "grant_id": str(new_grant["grant_id"]),
            "pid": new_grant["target_pid"],
            "hand_id": str(new_grant["hand_id"]) if new_grant.get("hand_id") else None,
            "ttl_expires_at": ttl_expires_at.isoformat(),
        },
    )
    return {"grant_id": str(new_grant["grant_id"]), "ttl_expires_at": ttl_expires_at.isoformat()}


@router.post("/{room_id}/moderation/dynamic-invite")
async def dynamic_invite(room_id: str, body: dict[str, Any], request: Request,
                          authorization: str | None = Header(default=None)) -> dict:
    """§8.2 — moderator mints an in-room invite for a missing expert.

    Broadcasts an ``invite_minted`` event so the room knows a peer has
    been summoned.
    """
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    await state.rate_limiter.check(principal.pid, "dynamic_invite")
    # Participant invites only. Owner invites come from room creation and the
    # owner's signed routes, and this one used to accept kind "owner", so a
    # moderator could mint themselves a second, room-owner identity that
    # outlived the owner vacating their seat and could reclaim it. A moderator
    # invite is no use here either: the seat is held by the caller.
    kind = body.get("kind", "participant")
    if kind != "participant":
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_kind",
                                         "message": "a moderator may mint participant invites only"})
    ttl_raw = body.get("ttl_seconds")
    if ttl_raw is None:
        ttl_seconds = state.settings.invite_ttl_seconds_default
    elif isinstance(ttl_raw, int) and not isinstance(ttl_raw, bool) and 1 <= ttl_raw <= MAX_INVITE_TTL_SECONDS:
        ttl_seconds = ttl_raw
    else:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_value", "field": "ttl_seconds"})
    reason = body.get("reason")
    if reason is not None and (not is_db_text(reason) or len(reason) > _MAX_REASON_LENGTH):
        raise _bad_body(f"reason must be text of at most {_MAX_REASON_LENGTH} characters")
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    _, token = await state.invites.create(room_id, kind, expires)

    link = invite_link(public_base_url(request), room_id, token)

    await state.sys_publish_event(
        room_id,
        "org.agentstorming.invite_minted",
        payload={
            "kind": kind,
            "by_pid": principal.pid,
            "expires_at": expires.isoformat(),
            "reason": reason,
        },
    )
    return {
        "invite_token": token,
        "invite_link": link,
        "kind": kind,
        "room_id": room_id,
        "expires_at": expires.isoformat(),
    }


@router.patch("/{room_id}/config")
async def patch_room_config_moderator(
    room_id: str, body: dict[str, Any], request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """§5.2 — moderator-mutable config fields.

    Accepts a subset of room config fields:
    - ``raise_hand_required`` (bool)
    - ``go_speak_ttl_seconds`` (int, positive)
    - ``max_participants`` (int, positive)

    Owner-only fields (``visibility``, ``deputies_enabled``,
    ``snapshot_interval_seconds``, ``disconnect_grace_seconds``,
    ``history_max_return``, ``attachments_max_bytes``) are silently
    ignored here — use the signed owner PATCH for those. Returns the
    updated config.
    """
    state = request.app.state
    await state.auth.require_moderator(room_id, authorization)
    room = await state.rooms.get(room_id)
    if room is None:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.room_unknown"})

    # Build a merged dict respecting the moderator's allowed fields.
    current = room.config.to_json()
    mod_allowed = {"raise_hand_required", "go_speak_ttl_seconds", "max_participants"}
    changed: dict[str, Any] = {}
    for k in mod_allowed:
        if k not in body:
            continue
        v = body[k]
        if k == "raise_hand_required":
            if not isinstance(v, bool):
                raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_type",
                                                 "field": k})
        elif k in ("go_speak_ttl_seconds", "max_participants"):
            if not isinstance(v, int) or v <= 0:
                raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_value",
                                                 "field": k})
        current[k] = v
        changed[k] = v

    if not changed:
        return {"room_id": room_id, "config": room.config.to_json(), "changed": {}}

    from ..domain.room import RoomConfig
    new_cfg = RoomConfig.from_json(current)
    await state.rooms.update_config(room_id, new_cfg)
    return {
        "room_id": room_id,
        "config": new_cfg.to_json(),
        "changed": changed,
    }


@router.post("/{room_id}/moderation/mode")
async def promote_room_mode(
    room_id: str, body: dict[str, Any], request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Stage-13 §Planning mode — promote a room from ``planning`` to ``active``.

    In ``planning`` mode every broker-mediated tool call declaring
    ``effects: write`` is denied; read-only calls proceed, and personas may
    still talk, raise hands, and propose plans. Promotion is a moderator
    action and is recorded as a signed ``org.agentstorming.mode_promoted``
    event so the transition is auditable by every participant.

    Promotion is one-way here. Returning a live room to ``planning`` is an
    owner action via the signed ``PATCH /v1/owner/rooms/{id}`` surface: a
    moderator that could re-arm the restriction at will could also use it to
    stall peers mid-task.
    """
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    room = await state.rooms.get(room_id)
    if room is None:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.room_unknown"})

    target = body.get("mode", "active")
    if target != "active":
        raise HTTPException(
            400,
            detail={"code": "org.agentstorming.err.bad_value",
                    "message": "only promotion to 'active' is a moderator action"},
        )
    if room.config.mode == "active":
        return {"room_id": room_id, "mode": "active", "changed": False}

    current = room.config.to_json()
    current["mode"] = "active"
    from ..domain.room import RoomConfig
    new_cfg = RoomConfig.from_json(current)
    await state.rooms.update_config(room_id, new_cfg)

    await state.sys_publish_event(
        room_id, TYPE_MODE_PROMOTED,
        payload={
            "from_mode": "planning",
            "to_mode": "active",
            "promoted_by": principal.pid,
        },
    )
    return {"room_id": room_id, "mode": "active", "changed": True}


@router.post("/{room_id}/moderation/ignore/{target_pid}")
async def add_ignore_entry(
    room_id: str, target_pid: str, body: dict[str, Any], request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """§12.1a — moderator adds a view-local ignore entry on ``target_pid``.

    ``kind`` is one of ``"muted"`` (suppress whispers from target) or
    ``"candidate"`` (additionally suppress that candidate's
    registration_request events).
    """
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    kind = body.get("kind", "muted")
    if kind not in ("muted", "candidate"):
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_kind"})
    if target_pid == principal.pid:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.self_ignore"})
    await state.ignore_entries.add(room_id, principal.pid, target_pid, kind)
    return {"room_id": room_id, "ignored_pid": target_pid, "kind": kind}


@router.delete("/{room_id}/moderation/ignore/{target_pid}")
async def remove_ignore_entry(
    room_id: str, target_pid: str, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    removed = await state.ignore_entries.remove(room_id, principal.pid, target_pid)
    if not removed:
        raise HTTPException(404, detail={"code": "org.agentstorming.err.ignore_not_found"})
    return {"room_id": room_id, "ignored_pid": target_pid, "state": "removed"}


@router.get("/{room_id}/moderation/ignore")
async def list_ignore_entries(
    room_id: str, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    state = request.app.state
    principal = await state.auth.require_moderator(room_id, authorization)
    entries = await state.ignore_entries.list_for(room_id, principal.pid)
    return {
        "room_id": room_id,
        "moderator_pid": principal.pid,
        "entries": [
            {
                "ignored_pid": e["ignored_pid"],
                "kind": e["kind"],
                "created_at": e["created_at"].isoformat() if e.get("created_at") else None,
            }
            for e in entries
        ],
    }


@router.get("/{room_id}/moderation/participants")
async def list_participants_for_moderator(
    room_id: str, request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Moderator-only participant roster that exposes ``runs_as``.

    The ``runs_as`` attribute is declared at join time and kept
    server-side so the general room snapshot does not leak whether a
    peer is an agent or a human. The moderator needs it (for invites,
    abuse triage, turn-taking tuning), so this endpoint returns it —
    but only to original-moderator or room-owner.
    """
    state = request.app.state
    await state.auth.require_moderator(room_id, authorization)
    parts = await state.participants.list_all(room_id)
    return {
        "room_id": room_id,
        "participants": [
            {
                "pid": p.pid,
                "affiliation": p.affiliation,
                "deputy_rank": p.deputy_rank,
                "runs_as": p.runs_as,
                "joined_at": p.joined_at.isoformat() if p.joined_at else None,
                "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
                "penned_until": p.penned_until.isoformat() if p.penned_until else None,
            }
            for p in parts
        ],
    }
