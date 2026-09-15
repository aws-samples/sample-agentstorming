# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Owner endpoints — signed-request room + key management.

The human owner of the server holds an Ed25519 keypair. Their public
key is registered in ``owner_keys`` (seeded at boot from the
``AGENTSTORMING_OWNER_PUBKEY`` env var; additional keys added later via
signed requests from an existing owner key).

Every mutating endpoint under ``/v1/owner/*`` follows the same
signed-request pattern:

1. Client calls ``GET /v1/owner/nonce`` → ``{nonce, expires_at}``. 60s
   TTL, one-shot.
2. Client canonicalises the request body (minus ``pubkey`` + ``sig``),
   Ed25519-signs it with their owner private key.
3. Client POSTs the endpoint with ``pubkey`` + ``sig`` + the body.
4. Server verifies: owner pubkey active, signature verifies, nonce
   valid + unconsumed + unexpired, timestamp within ±5 minutes.

Endpoints covered here:

- ``GET /v1/owner/nonce``
- ``POST /v1/owner/request-invite`` (compat name)
- ``POST /v1/owner/rooms`` — create a room
- ``DELETE /v1/owner/rooms/{id}`` — terminate
- ``PATCH /v1/owner/rooms/{id}`` — update title/description/visibility
- ``POST /v1/owner/rooms/{id}/invites`` — mint invite of any kind
- ``GET /v1/owner/rooms/{id}`` — stats
- ``GET /v1/owner/rooms`` — list all (including private)
- ``DELETE /v1/owner/rooms/{id}/moderator`` — vacate moderator seat
- ``POST /v1/owner/rooms/{id}/moderator`` — make_moderator_permanent (§7.5.6)
- ``POST /v1/owner/keys`` — register an additional owner key
- ``POST /v1/owner/keys/{pubkey}/revoke`` — revoke an owner key
- ``GET /v1/owner/keys`` — list active owner keys
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..domain.event import (
    TYPE_MODERATOR_CHANGED,
    TYPE_ORIGINAL_MODERATOR_CHANGED,
)
from ..domain.room import Room, RoomConfig
from ..services import sig as sig_mod
from ..services.jcs import canonicalise
from .deps import public_base_url

router = APIRouter(tags=["owner"])

MAX_CLOCK_SKEW_SECONDS = 300


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _verify_ts(ts_str: str) -> None:
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.ts_invalid"})
    now = datetime.now(timezone.utc)
    if abs((now - ts).total_seconds()) > MAX_CLOCK_SKEW_SECONDS:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.ts_skew"})


async def _verify_owner_signed(state, body: dict[str, Any]) -> bytes:
    """Verify the canonical owner-signed request. Returns the caller's pubkey bytes.

    Body MUST carry ``nonce``, ``ts``, ``pubkey``, ``sig``. All other
    keys are passed through to the signature payload so each endpoint
    can bind room_id / kind / target as part of the signed blob.
    """
    required = {"nonce", "ts", "pubkey", "sig"}
    missing = required - set(body.keys())
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"code": "org.agentstorming.err.bad_body", "missing": sorted(missing)},
        )
    _verify_ts(body["ts"])
    try:
        pubkey = _b64url_decode(body["pubkey"])
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.pubkey_decode"})
    if len(pubkey) != 32:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.pubkey_size"})
    if not await state.owner_keys.exists_active(pubkey):
        raise HTTPException(status_code=403, detail={"code": "org.agentstorming.err.owner_unknown"})
    sig = body["sig"]
    signed = {k: v for k, v in body.items() if k not in ("pubkey", "sig")}
    canonical = canonicalise(signed)
    if not sig_mod.verify_blob(canonical, sig, pubkey):
        raise HTTPException(status_code=403, detail={"code": "org.agentstorming.err.sig_invalid"})
    if not await state.owner_nonces.consume(body["nonce"]):
        raise HTTPException(
            status_code=409, detail={"code": "org.agentstorming.err.nonce_invalid"}
        )
    # Rate-limit the signed surface per owner key to slow brute-force
    # nonce harvesting and accidental loops.
    await state.rate_limiter.check(body["pubkey"], "owner")
    return pubkey


# ---- nonce ---------------------------------------------------------------


class OwnerNonceResponse(BaseModel):
    nonce: str
    expires_at: str


@router.get("/owner/nonce", response_model=OwnerNonceResponse)
async def issue_nonce(request: Request) -> OwnerNonceResponse:
    state = request.app.state
    client_ip = request.client.host if request.client else "unknown"
    await state.rate_limiter.check(f"ip:{client_ip}", "owner")
    nonce, expires = await state.owner_nonces.issue()
    return OwnerNonceResponse(nonce=nonce, expires_at=expires.isoformat())


# ---- request-invite (kept for back-compat + convenience) ----------------


class OwnerInviteResponse(BaseModel):
    invite_token: str
    invite_link: str
    kind: str
    room_id: str
    expires_at: str


def _invite_link(request: Request, room_id: str, token: str) -> str:
    base = public_base_url(request)
    return f"{base}/r/{room_id}/join?t={token}"


@router.post("/owner/request-invite", response_model=OwnerInviteResponse)
async def request_invite_legacy(body: dict[str, Any], request: Request) -> OwnerInviteResponse:
    """Legacy alias of POST /v1/owner/rooms/{id}/invites."""
    state = request.app.state
    await _verify_owner_signed(state, body)
    room_id = body.get("room_id")
    kind = body.get("kind", "owner")
    if not room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.bad_body"})
    room = await state.rooms.get(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail={"code": "org.agentstorming.err.room_unknown"})
    ttl = state.settings.invite_ttl_seconds_default
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    _row, token = await state.invites.create(room_id, kind, expires)
    return OwnerInviteResponse(
        invite_token=token,
        invite_link=_invite_link(request, room_id, token),
        kind=kind,
        room_id=room_id,
        expires_at=expires.isoformat(),
    )


# ---- rooms CRUD ----------------------------------------------------------


class RoomCreateBody(BaseModel):
    """Signed body for POST /v1/owner/rooms."""
    room_id: str = Field(..., min_length=1, max_length=128)
    title: str | None = None
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    # §14.1 — startup documents attached at creation time. Each entry
    # may carry {type, title, body?, attachment_ref?, content_type?,
    # size_bytes?}. See RoomConfig.startup_documents for the shape.
    startup_documents: list[dict[str, Any]] = Field(default_factory=list)
    nonce: str
    ts: str
    pubkey: str
    sig: str


class RoomInfoResponse(BaseModel):
    room_id: str
    state: str
    title: str | None = None
    description: str | None = None
    visibility: str
    created_at: str | None = None
    config: dict[str, Any]
    participant_count: int | None = None
    moderator_pid: str | None = None


@router.post("/owner/rooms", response_model=dict)
async def create_room_owner(body: RoomCreateBody, request: Request) -> dict:
    """Create a room via a signed owner request. Returns initial invite set."""
    state = request.app.state
    await _verify_owner_signed(state, body.model_dump())

    if await state.rooms.get(body.room_id):
        raise HTTPException(status_code=409, detail={"code": "org.agentstorming.err.room_exists"})

    cfg = RoomConfig.from_json(body.config or {})
    priv, pub = sig_mod.generate_keypair()
    room = Room(
        id=body.room_id,
        state="CREATED",
        config=cfg,
        server_pubkey=pub,
        server_privkey=priv,
        created_at=datetime.now(timezone.utc),
        title=body.title,
        description=body.description,
    )
    await state.rooms.create(room)
    await state.rooms.set_state(body.room_id, "ACTIVE")

    # §14.1 — install startup documents. These are visible via the
    # metadata snapshot + the /documents endpoints. Invalid entries
    # are skipped (we log) rather than failing the whole create.
    for doc in body.startup_documents or []:
        if not isinstance(doc, dict):
            continue
        doc_type = doc.get("type", "reference")
        title = doc.get("title")
        if not title:
            continue
        try:
            await state.documents.create(body.room_id, {
                "type": doc_type,
                "title": title,
                "body": doc.get("body"),
                "attachment_ref": doc.get("attachment_ref"),
                "content_type": doc.get("content_type", "text/markdown"),
                "size_bytes": int(doc.get("size_bytes") or (len(doc.get("body", "").encode("utf-8")) if doc.get("body") else 0)),
            })
        except Exception:  # pragma: no cover - logged, not fatal
            pass

    ttl = state.settings.invite_ttl_seconds_default
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    _, mod_token = await state.invites.create(body.room_id, "moderator", expires)
    _, owner_token = await state.invites.create(body.room_id, "owner", expires)
    _, part_token = await state.invites.create(body.room_id, "participant", expires)

    return {
        "room_id": body.room_id,
        "title": body.title,
        "description": body.description,
        "visibility": cfg.visibility,
        "moderator_invite": mod_token,
        "moderator_invite_link": _invite_link(request, body.room_id, mod_token),
        "owner_invite": owner_token,
        "owner_invite_link": _invite_link(request, body.room_id, owner_token),
        "participant_invite": part_token,
        "participant_invite_link": _invite_link(request, body.room_id, part_token),
        "expires_at": expires.isoformat(),
    }


class RoomTerminateBody(BaseModel):
    room_id: str
    nonce: str
    ts: str
    pubkey: str
    sig: str


@router.post("/owner/rooms/{room_id}/terminate", response_model=dict)
async def terminate_room_owner(room_id: str, body: RoomTerminateBody, request: Request) -> dict:
    """Terminate a room via a signed owner request."""
    state = request.app.state
    if body.room_id != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
    await _verify_owner_signed(state, body.model_dump())
    room = await state.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail={"code": "org.agentstorming.err.room_unknown"})
    await state.rooms.terminate(room_id, datetime.now(timezone.utc))
    return {"room_id": room_id, "state": "TERMINATED"}


class RoomPatchBody(BaseModel):
    room_id: str
    title: str | None = None
    description: str | None = None
    visibility: Literal["public", "private"] | None = None
    nonce: str
    ts: str
    pubkey: str
    sig: str


@router.patch("/owner/rooms/{room_id}", response_model=RoomInfoResponse)
async def patch_room_owner(room_id: str, body: RoomPatchBody, request: Request) -> RoomInfoResponse:
    state = request.app.state
    if body.room_id != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
    await _verify_owner_signed(state, body.model_dump())
    room = await state.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail={"code": "org.agentstorming.err.room_unknown"})
    if body.title is not None or body.description is not None:
        await state.rooms.update_metadata(room_id, title=body.title, description=body.description)
    if body.visibility is not None and body.visibility != room.config.visibility:
        new_cfg = RoomConfig.from_json({**room.config.to_json(), "visibility": body.visibility})
        await state.rooms.update_config(room_id, new_cfg)
    room2 = await state.rooms.get(room_id)
    pct = await state.participants.count_active(room_id)
    return RoomInfoResponse(
        room_id=room2.id,
        state=room2.state,
        title=room2.title,
        description=room2.description,
        visibility=room2.config.visibility,
        created_at=room2.created_at.isoformat() if room2.created_at else None,
        config=room2.config.to_json(),
        participant_count=pct,
    )


class RoomMintInviteBody(BaseModel):
    room_id: str
    kind: Literal["participant", "moderator", "owner"] = "participant"
    ttl_seconds: int | None = None
    nonce: str
    ts: str
    pubkey: str
    sig: str


@router.post("/owner/rooms/{room_id}/invites", response_model=OwnerInviteResponse)
async def mint_invite_owner(room_id: str, body: RoomMintInviteBody, request: Request) -> OwnerInviteResponse:
    state = request.app.state
    if body.room_id != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
    await _verify_owner_signed(state, body.model_dump())
    room = await state.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail={"code": "org.agentstorming.err.room_unknown"})
    ttl = body.ttl_seconds or state.settings.invite_ttl_seconds_default
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    _, token = await state.invites.create(room_id, body.kind, expires)
    return OwnerInviteResponse(
        invite_token=token,
        invite_link=_invite_link(request, room_id, token),
        kind=body.kind,
        room_id=room_id,
        expires_at=expires.isoformat(),
    )


@router.get("/owner/rooms", response_model=dict)
async def list_rooms_owner(request: Request) -> dict:
    state = request.app.state
    rooms = await state.rooms.list_all_active()
    items = []
    for r in rooms:
        pct = await state.participants.count_active(r.id)
        items.append(
            {
                "room_id": r.id,
                "state": r.state,
                "title": r.title,
                "description": r.description,
                "visibility": r.config.visibility,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "participant_count": pct,
            }
        )
    return {"rooms": items}


@router.get("/owner/rooms/{room_id}", response_model=RoomInfoResponse)
async def get_room_owner(room_id: str, request: Request) -> RoomInfoResponse:
    state = request.app.state
    room = await state.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail={"code": "org.agentstorming.err.room_unknown"})
    pct = await state.participants.count_active(room_id)
    mod = await state.participants.get_by_affiliation(room_id, "original-moderator")
    return RoomInfoResponse(
        room_id=room.id,
        state=room.state,
        title=room.title,
        description=room.description,
        visibility=room.config.visibility,
        created_at=room.created_at.isoformat() if room.created_at else None,
        config=room.config.to_json(),
        participant_count=pct,
        moderator_pid=mod.pid if mod else None,
    )


# ---- moderator-seat lifecycle -------------------------------------------


class ModeratorVacateBody(BaseModel):
    room_id: str
    nonce: str
    ts: str
    pubkey: str
    sig: str


@router.delete("/owner/rooms/{room_id}/moderator", response_model=dict)
async def vacate_moderator_seat(room_id: str, request: Request) -> dict:
    """Vacate the moderator seat so a fresh moderator invite can be redeemed.

    Signed-owner call. Body delivered as JSON body even though DELETE —
    FastAPI allows. Demotes the current original-moderator to plain
    `member`. Does NOT destroy their participant entry; they remain
    in the room as a regular member.
    """
    state = request.app.state
    body = await request.json()
    if body.get("room_id") != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
    await _verify_owner_signed(state, body)
    mod = await state.participants.get_by_affiliation(room_id, "original-moderator")
    if mod is None:
        return {"room_id": room_id, "moderator_seat": "empty"}
    await state.participants.set_affiliation(room_id, mod.pid, "member")
    await state.sys_publish_event(
        room_id,
        TYPE_ORIGINAL_MODERATOR_CHANGED,
        payload={"previous_pid": mod.pid, "new_pid": None, "reason": "owner_vacated"},
    )
    return {"room_id": room_id, "previous_moderator": mod.pid, "moderator_seat": "vacant"}


class PromoteModeratorBody(BaseModel):
    """§7.5.6 — make_moderator_permanent."""
    room_id: str
    target_pid: str
    nonce: str
    ts: str
    pubkey: str
    sig: str


@router.post("/owner/rooms/{room_id}/moderator", response_model=dict)
async def promote_moderator(room_id: str, body: PromoteModeratorBody, request: Request) -> dict:
    """Make a specific participant the original-moderator, replacing the current one."""
    state = request.app.state
    if body.room_id != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
    await _verify_owner_signed(state, body.model_dump())
    target = await state.participants.get(room_id, body.target_pid)
    if target is None:
        raise HTTPException(
            status_code=404, detail={"code": "org.agentstorming.err.participant_unknown"}
        )
    prev = await state.participants.get_by_affiliation(room_id, "original-moderator")
    if prev:
        await state.participants.set_affiliation(room_id, prev.pid, "member")
    await state.participants.set_affiliation(room_id, body.target_pid, "original-moderator")
    await state.sys_publish_event(
        room_id,
        TYPE_ORIGINAL_MODERATOR_CHANGED,
        payload={
            "previous_pid": prev.pid if prev else None,
            "new_pid": body.target_pid,
            "reason": "owner_promotion",
        },
    )
    await state.sys_publish_event(
        room_id,
        TYPE_MODERATOR_CHANGED,
        payload={"new_moderator_pid": body.target_pid, "reason": "owner_promotion"},
    )
    return {"room_id": room_id, "moderator_pid": body.target_pid}


# ---- owner-key management ------------------------------------------------


class RegisterOwnerKeyBody(BaseModel):
    """Add a new owner key. Signed by an EXISTING owner key."""
    new_pubkey: str = Field(..., description="base64url raw Ed25519 32-byte key")
    label: str | None = None
    nonce: str
    ts: str
    pubkey: str
    sig: str


@router.post("/owner/keys", response_model=dict)
async def register_owner_key(body: RegisterOwnerKeyBody, request: Request) -> dict:
    state = request.app.state
    await _verify_owner_signed(state, body.model_dump())
    try:
        new_pub = _b64url_decode(body.new_pubkey)
    except Exception:
        raise HTTPException(
            status_code=400, detail={"code": "org.agentstorming.err.new_pubkey_decode"}
        )
    if len(new_pub) != 32:
        raise HTTPException(
            status_code=400, detail={"code": "org.agentstorming.err.new_pubkey_size"}
        )
    await state.owner_keys.insert(new_pub, label=body.label)
    return {"status": "registered", "pubkey": body.new_pubkey, "label": body.label}


class RevokeOwnerKeyBody(BaseModel):
    target_pubkey: str
    nonce: str
    ts: str
    pubkey: str
    sig: str


@router.post("/owner/keys/revoke", response_model=dict)
async def revoke_owner_key(body: RevokeOwnerKeyBody, request: Request) -> dict:
    state = request.app.state
    await _verify_owner_signed(state, body.model_dump())
    try:
        target = _b64url_decode(body.target_pubkey)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.pubkey_decode"})
    # Don't let an owner accidentally revoke their only key.
    active = await state.owner_keys.list_active()
    if len(active) <= 1:
        raise HTTPException(
            status_code=409, detail={"code": "org.agentstorming.err.last_owner_key"}
        )
    await state.owner_keys.revoke(target)
    return {"status": "revoked", "pubkey": body.target_pubkey}


class OwnerKeyInfo(BaseModel):
    pubkey: str
    label: str | None = None
    registered_at: str


class OwnerKeysResponse(BaseModel):
    keys: list[OwnerKeyInfo]


@router.get("/owner/keys", response_model=OwnerKeysResponse)
async def list_keys(request: Request) -> OwnerKeysResponse:
    state = request.app.state
    rows = await state.owner_keys.list_active()
    return OwnerKeysResponse(
        keys=[
            OwnerKeyInfo(
                pubkey=_b64url_encode(r["pubkey"]),
                label=r.get("label"),
                registered_at=r["registered_at"].isoformat(),
            )
            for r in rows
        ]
    )
