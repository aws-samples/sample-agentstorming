# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Owner endpoints — signed-request room + key management.

The human owner of the server holds an Ed25519 keypair. Their public
key is registered in ``owner_keys`` (seeded at boot from the
``AGENTSTORMING_OWNER_PUBKEY`` env var; additional keys added later via
signed requests from an existing owner key).

Every route under ``/v1/owner/*`` requires an owner signature, reads and the
nonce route included. A dependency on each router enforces it, so a route
added to either is signed by construction. Leaving it to each handler to call
the verifier is how ``GET /v1/owner/rooms``, ``GET /v1/owner/rooms/{id}`` and
``GET /v1/owner/keys`` once shipped unsigned, listing private rooms and owner
keys to anyone.

The signed-request pattern:

1. Client calls ``GET /v1/owner/nonce`` → ``{nonce, expires_at}``. 60s
   TTL, one-shot. This request is itself signed, over ``{op, ts}`` only, since
   it is the request that obtains the nonce. An unsigned caller gets 401 and
   no nonce, so it can neither drain the owner's rate limit nor grow the nonce
   table.
2. Client builds the signed payload from the request's fields plus ``op``,
   the method and path it is about to call (``"POST /v1/owner/rooms"``), and
   Ed25519-signs its JCS canonical form with the owner private key.
3. A request with a JSON body carries ``nonce``, ``ts``, ``pubkey`` and ``sig``
   in the body beside its other fields. A GET has no body, so it carries the
   same four in the ``X-AgentStorming-Owner-{Nonce,Ts,Pubkey,Sig}`` headers.
4. Server verifies: timestamp within ±5 minutes, owner pubkey active,
   signature valid over the payload with ``op`` rebuilt from the request it
   actually received, the nonce live (issued, unconsumed, unexpired), the
   per-key rate limit, and only then consumes the nonce. A request that fails
   any of those keeps its nonce.
   One that passes them has spent it, even if the handler then rejects the
   body (a wrong ``room_id``, an unknown field), so the retry needs a new one.

``op`` stops a signature made for one endpoint being spent on another. Without
it, ``terminate`` and ``vacate moderator`` signed the same ``{room_id, nonce,
ts}``, so whoever held a signed vacate request could terminate the room with it.

Endpoints covered here:

- ``GET /v1/owner/nonce`` — issue a nonce (signed over ``{op, ts}``)
- ``POST /v1/owner/request-invite`` (compat name)
- ``POST /v1/owner/rooms`` — create a room
- ``POST /v1/owner/rooms/{id}/terminate`` — terminate
- ``PATCH /v1/owner/rooms/{id}`` — update title, description and config
- ``POST /v1/owner/rooms/{id}/invites`` — mint invite of any kind
- ``GET /v1/owner/rooms/{id}`` — stats
- ``GET /v1/owner/rooms`` — list all (including private)
- ``DELETE /v1/owner/rooms/{id}/moderator`` — vacate moderator seat
- ``POST /v1/owner/rooms/{id}/moderator`` — make_moderator_permanent (§7.5.6)
- ``POST /v1/owner/keys`` — register an additional owner key
- ``POST /v1/owner/keys/revoke`` — revoke an owner key
- ``GET /v1/owner/keys`` — list active owner keys
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from ..domain.event import (
    TYPE_MODE_PROMOTED,
    TYPE_MODERATOR_CHANGED,
    TYPE_ORIGINAL_MODERATOR_CHANGED,
)
from ..domain.room import Room, RoomConfig
from ..services import sig as sig_mod
from ..services.signed_request import canonical_bytes, verify_ts
from .deps import MAX_INVITE_TTL_SECONDS, invite_link, public_base_url

log = logging.getLogger("agentstorming.server.owner")

MAX_CLOCK_SKEW_SECONDS = 300

#: The four signature fields, and the headers that carry them on a GET.
OWNER_SIG_HEADERS = {
    "nonce": "X-AgentStorming-Owner-Nonce",
    "ts": "X-AgentStorming-Owner-Ts",
    "pubkey": "X-AgentStorming-Owner-Pubkey",
    "sig": "X-AgentStorming-Owner-Sig",
}


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _bad_body(**extra: Any) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": "org.agentstorming.err.bad_body", **extra})


def _verify_ts(ts_str: str) -> None:
    # A timestamp without an offset cannot be compared with an aware "now"; it
    # used to raise TypeError there and answer 500.
    verify_ts(ts_str, MAX_CLOCK_SKEW_SECONDS)


def op_path(path: str) -> str:
    """Escape a decoded path for ``op``: ``%`` → ``%25``, then ``?`` → ``%3F``.

    The path is percent-decoded but the query is not, so a room id containing
    ``?`` (sent as ``%3F``) once built the same ``op`` as a shorter id plus a
    query string, and one signature verified on both endpoints. With neither
    character left bare in the path, the first ``?`` in ``op`` is always the
    query separator.
    """
    return path.replace("%", "%25").replace("?", "%3F")


def signed_op(request: Request) -> str:
    """The operation an owner signature is bound to: ``"<METHOD> <path>[?<query>]"``.

    Rebuilt from the request the server received, never read from the client,
    so a signature only verifies on the endpoint it was made for. The path is
    the API path (``/v1/owner/...``) with any mount prefix removed, so a client
    signs the same string whether or not a proxy serves the API under one, and
    it is escaped by :func:`op_path`. The query string is used as received.
    """
    path = request.scope["path"]
    root = request.scope.get("root_path") or ""
    if root and path.startswith(root):
        path = path[len(root):]
    op = f"{request.method} {op_path(path)}"
    query = request.scope.get("query_string", b"").decode("latin-1")
    if query:
        op = f"{op}?{query}"
    return op


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        body = json.loads(raw, parse_constant=_reject_constant) if raw else {}
    except (ValueError, RecursionError):
        # RecursionError: a deeply nested body used to escape as a 500.
        raise _bad_body()
    if not isinstance(body, dict):
        raise _bad_body()
    return body


async def _verify_owner_signed(
    state, signed: dict[str, Any], pubkey_b64: str, sig: str,
    *, consume_nonce: bool = True, bucket: str = "owner", replay_guard=None,
) -> bytes:
    """Verify one owner signature over ``signed``. Returns the caller's pubkey bytes.

    ``signed`` is the payload as the client signed it: the request's fields
    without ``pubkey`` and ``sig``, with ``op`` already set from the request.
    The nonce is consumed last, after the per-key rate limit, so a request
    that fails any check here keeps it.
    """
    _verify_ts(signed["ts"])
    try:
        pubkey = _b64url_decode(pubkey_b64)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.pubkey_decode"})
    if len(pubkey) != 32:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.pubkey_size"})
    if not await state.owner_keys.exists_active(pubkey):
        raise HTTPException(status_code=403, detail={"code": "org.agentstorming.err.owner_unknown"})
    # NaN, Infinity, 1e400 or a lone surrogate anywhere in the body has no
    # canonical form, so nothing can have signed it: 400, not 500.
    message = canonical_bytes(signed)
    if not sig_mod.verify_blob(message, sig, pubkey):
        raise HTTPException(status_code=403, detail={"code": "org.agentstorming.err.sig_invalid"})
    nonce_invalid = HTTPException(
        status_code=409, detail={"code": "org.agentstorming.err.nonce_invalid"}
    )
    # A replayed request carries a spent nonce. It is refused here, before the
    # limiter: charged first, as briefly it was, 30 replays of one captured
    # request emptied the owner's bucket and locked the owner out.
    if consume_nonce and not await state.owner_nonces.is_live(signed["nonce"]):
        raise nonce_invalid
    if replay_guard is not None and not replay_guard.first_use(pubkey, message):
        raise nonce_invalid
    # Only a verified owner reaches the limiter, so an anonymous caller cannot
    # spend the owner's allowance. Keyed on the decoded key, so re-encoding it
    # (padding, say) does not open a second bucket.
    await state.rate_limiter.check(_b64url_encode(pubkey), bucket)
    if consume_nonce and not await state.owner_nonces.consume(signed["nonce"]):
        raise nonce_invalid
    return pubkey


def _sig_headers(request: Request, names: tuple[str, ...]) -> dict[str, str]:
    fields = {name: request.headers.get(OWNER_SIG_HEADERS[name]) for name in names}
    missing = [OWNER_SIG_HEADERS[name] for name, value in fields.items() if not value]
    if missing:
        raise HTTPException(
            status_code=401,
            detail={"code": "org.agentstorming.err.owner_auth_required", "missing": missing},
        )
    return fields  # type: ignore[return-value]


def _bind_op(request: Request, signed: dict[str, Any]) -> None:
    op = signed_op(request)
    if signed.get("op", op) != op:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.op_mismatch"})
    signed["op"] = op


async def require_owner_signature(request: Request) -> bytes:
    """Router-level guard: every route on ``router`` needs a valid owner signature.

    The caller's verified pubkey is left on ``request.state.owner_pubkey``.
    """
    if request.method == "GET":
        fields = _sig_headers(request, ("nonce", "ts", "pubkey", "sig"))
        signed: dict[str, Any] = {"nonce": fields["nonce"], "ts": fields["ts"]}
    else:
        # FastAPI refuses a body route a non-JSON content type with 422, after
        # this dependency has spent the nonce. Refusing it here keeps the nonce.
        media = request.headers.get("content-type", "").split(";")[0].strip().lower()
        # FastAPI parses application/json and application/*+json; text/x+json
        # passed this check and then failed there, after the nonce was spent.
        if media != "application/json" and not (media.startswith("application/") and media.endswith("+json")):
            raise HTTPException(
                status_code=415,
                detail={"code": "org.agentstorming.err.unsupported_media_type",
                        "message": "a signed owner request must be application/json"},
            )
        body = await _json_body(request)
        missing = sorted(set(OWNER_SIG_HEADERS) - set(body))
        if missing:
            raise _bad_body(missing=missing)
        fields = {name: body[name] for name in OWNER_SIG_HEADERS}
        signed = {k: v for k, v in body.items() if k not in ("pubkey", "sig")}
    if not all(isinstance(value, str) and value for value in fields.values()):
        raise _bad_body()
    _bind_op(request, signed)

    pubkey = await _verify_owner_signed(request.app.state, signed, fields["pubkey"], fields["sig"])
    request.state.owner_pubkey = pubkey
    return pubkey


class _SeenRequests:
    """Signed nonce requests already used, for as long as their ts is valid.

    The nonce request has no nonce of its own, so without this a captured one
    could be replayed for its five-minute window, and each replay was charged
    to the owner's issuance limit: about 60 of them locked the owner out. Per
    process, so with N replicas a capture is good for at most N uses.

    Keyed on the key and the signed bytes, never on the ``sig`` string: base64
    decoding skips characters outside the alphabet and surplus padding, so
    ``sig + "=="`` or ``sig`` with ``....`` inside verified as a new request
    and each got a fresh nonce.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def first_use(self, pubkey: bytes, message: bytes) -> bool:
        now = time.monotonic()
        if len(self._seen) > 10_000:
            self._seen = {k: v for k, v in self._seen.items() if v > now}
        key = hashlib.sha256(pubkey + message).hexdigest()
        if self._seen.get(key, 0.0) > now:
            return False
        self._seen[key] = now + self._ttl
        return True


#: Twice the clock-skew window: a ts is accepted for ±MAX_CLOCK_SKEW_SECONDS.
_nonce_requests_seen = _SeenRequests(2 * MAX_CLOCK_SKEW_SECONDS)


async def require_owner_signature_without_nonce(request: Request) -> bytes:
    """Guard for ``GET /v1/owner/nonce``: signed over ``{op, ts}``, no nonce.

    Each signed request is accepted once (see ``_SeenRequests``), after it has
    verified, so a replay is refused before it reaches the owner's limit.
    """
    fields = _sig_headers(request, ("ts", "pubkey", "sig"))
    signed: dict[str, Any] = {"ts": fields["ts"]}
    _bind_op(request, signed)
    pubkey = await _verify_owner_signed(
        request.app.state, signed, fields["pubkey"], fields["sig"],
        consume_nonce=False, bucket="owner_nonce", replay_guard=_nonce_requests_seen,
    )
    request.state.owner_pubkey = pubkey
    return pubkey


#: Holds the nonce route alone: it issues the nonce every other signature
#: includes, so its own signature cannot include one.
nonce_router = APIRouter(
    tags=["owner"], dependencies=[Depends(require_owner_signature_without_nonce)]
)

#: Every other owner route. Signed by construction, through the dependency.
router = APIRouter(tags=["owner"], dependencies=[Depends(require_owner_signature)])


# ---- nonce ---------------------------------------------------------------


class OwnerNonceResponse(BaseModel):
    nonce: str
    expires_at: str


@nonce_router.get("/owner/nonce", response_model=OwnerNonceResponse)
async def issue_nonce(request: Request) -> OwnerNonceResponse:
    state = request.app.state
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
    return invite_link(public_base_url(request), room_id, token)


InviteKind = Literal["participant", "moderator", "owner"]


class SignedBody(BaseModel):
    """The four signature fields, plus ``op`` for a client that sends it.

    Unknown fields are refused on every signed body, as ADR-011 requires: the
    signature covers the raw body, so a signed field the model dropped (a
    ``ttl`` typed for ``ttl_seconds``, minting a week-long invite instead of a
    one-minute one) answered 200 for a request it had not honoured.
    """
    model_config = ConfigDict(extra="forbid")
    nonce: str
    ts: str
    pubkey: str
    sig: str
    op: str | None = None


class LegacyInviteBody(SignedBody):
    room_id: str = Field(..., min_length=1)
    # "owner", as `agentstorming owner request-invite` defaults to. Any other
    # value used to reach the database and fail its CHECK as a 500.
    kind: InviteKind = "owner"


@router.post("/owner/request-invite", response_model=OwnerInviteResponse)
async def request_invite_legacy(body: LegacyInviteBody, request: Request) -> OwnerInviteResponse:
    """Legacy alias of POST /v1/owner/rooms/{id}/invites."""
    state = request.app.state
    room = await state.rooms.get(body.room_id)
    if room is None:
        raise HTTPException(status_code=404, detail={"code": "org.agentstorming.err.room_unknown"})
    ttl = state.settings.invite_ttl_seconds_default
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    _row, token = await state.invites.create(body.room_id, body.kind, expires)
    return OwnerInviteResponse(
        invite_token=token,
        invite_link=_invite_link(request, body.room_id, token),
        kind=body.kind,
        room_id=body.room_id,
        expires_at=expires.isoformat(),
    )


# ---- rooms CRUD ----------------------------------------------------------


_DURATION = Field(default=None, ge=1, le=31_536_000)
_COUNT = Field(default=None, ge=1, le=1_000_000)


class AttachmentsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    max_bytes: StrictInt | None = Field(default=None, ge=1, le=5 * 1024**3)


class RoomConfigInput(BaseModel):
    """The §5.2 fields an owner may set, typed and bounded.

    Used for a room's ``config`` at creation. Unknown fields are refused, as in
    the PATCH: creation used to take any dict, so ``{"mode": "planning"}``
    misspelt or misplaced was dropped and the room came up ``active``, and a
    wrongly typed value answered 500.
    """
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["public", "private"] | None = None
    mode: Literal["planning", "active"] | None = None
    max_participants: StrictInt | None = _COUNT
    deputies_enabled: StrictBool | None = None
    raise_hand_required: StrictBool | None = None
    go_speak_ttl_seconds: StrictInt | None = _DURATION
    snapshot_interval_seconds: StrictInt | None = _DURATION
    disconnect_grace_seconds: StrictInt | None = _DURATION
    history_max_return: StrictInt | None = _COUNT
    pen_max_duration_seconds: StrictInt | None = _DURATION
    freeze_multiplier: StrictInt | None = Field(default=None, ge=1, le=1_000)
    rate_limit_per_minute: StrictInt | None = Field(default=None, ge=1, le=100_000)
    attachments: AttachmentsPatch | None = None

    def to_room_config(self) -> RoomConfig:
        data = self.model_dump(exclude_none=True)
        return RoomConfig.from_json(data)


class RoomCreateBody(SignedBody):
    """Signed body for POST /v1/owner/rooms. Config goes under ``config``."""
    model_config = ConfigDict(extra="forbid")
    room_id: str = Field(..., min_length=1, max_length=128)
    title: str | None = None
    description: str | None = None
    config: RoomConfigInput = Field(default_factory=RoomConfigInput)
    # §14.1 — startup documents attached at creation time. Each entry
    # may carry {type, title, body?, attachment_ref?, content_type?,
    # size_bytes?}. See RoomConfig.startup_documents for the shape.
    startup_documents: list[dict[str, Any]] = Field(default_factory=list)


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

    if await state.rooms.get(body.room_id):
        raise HTTPException(status_code=409, detail={"code": "org.agentstorming.err.room_exists"})

    cfg = body.config.to_room_config()
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
            log.warning("skipped startup document %r for room %s", title, body.room_id, exc_info=True)

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


class RoomTerminateBody(SignedBody):
    room_id: str


@router.post("/owner/rooms/{room_id}/terminate", response_model=dict)
async def terminate_room_owner(room_id: str, body: RoomTerminateBody, request: Request) -> dict:
    """Terminate a room via a signed owner request."""
    state = request.app.state
    if body.room_id != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
    room = await state.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail={"code": "org.agentstorming.err.room_unknown"})
    await state.rooms.terminate(room_id, datetime.now(timezone.utc))
    return {"room_id": room_id, "state": "TERMINATED"}




class RoomPatchBody(SignedBody):
    """Signed body for PATCH /v1/owner/rooms/{id}: metadata plus the §5.2 config
    fields an owner may change.

    Unknown fields are rejected. The model used to accept only title,
    description and visibility and drop everything else, so a correctly signed
    ``{"raise_hand_required": true}``, which the docs say this route sets,
    answered 200 and changed nothing.
    """
    model_config = ConfigDict(extra="forbid")
    room_id: str
    title: str | None = None
    description: str | None = None
    visibility: Literal["public", "private"] | None = None
    mode: Literal["planning", "active"] | None = None
    max_participants: StrictInt | None = _COUNT
    deputies_enabled: StrictBool | None = None
    raise_hand_required: StrictBool | None = None
    go_speak_ttl_seconds: StrictInt | None = _DURATION
    snapshot_interval_seconds: StrictInt | None = _DURATION
    disconnect_grace_seconds: StrictInt | None = _DURATION
    history_max_return: StrictInt | None = _COUNT
    freeze_multiplier: StrictInt | None = Field(default=None, ge=1, le=1_000)
    attachments: AttachmentsPatch | None = None


_PATCHABLE_CONFIG = (
    "visibility", "mode", "max_participants", "deputies_enabled", "raise_hand_required",
    "go_speak_ttl_seconds", "snapshot_interval_seconds", "disconnect_grace_seconds",
    "history_max_return", "freeze_multiplier",
)


@router.patch("/owner/rooms/{room_id}", response_model=RoomInfoResponse)
async def patch_room_owner(room_id: str, body: RoomPatchBody, request: Request) -> RoomInfoResponse:
    state = request.app.state
    if body.room_id != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
    room = await state.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail={"code": "org.agentstorming.err.room_unknown"})
    if body.title is not None or body.description is not None:
        await state.rooms.update_metadata(room_id, title=body.title, description=body.description)

    current = room.config.to_json()
    merged = dict(current)
    for name in _PATCHABLE_CONFIG:
        value = getattr(body, name)
        if value is not None:
            merged[name] = value
    if body.attachments is not None:
        merged["attachments"] = {
            **current["attachments"],
            **body.attachments.model_dump(exclude_none=True),
        }
    if merged != current:
        await state.rooms.update_config(room_id, RoomConfig.from_json(merged))
        if merged["mode"] != current["mode"]:
            # The broker learns the mode only from server-signed events (§Planning
            # mode), so an owner demotion that emitted nothing would leave it
            # allowing writes until the next metadata snapshot.
            await state.sys_publish_event(
                room_id, TYPE_MODE_PROMOTED,
                payload={
                    "from_mode": current["mode"],
                    "to_mode": merged["mode"],
                    "promoted_by": "room-owner",
                },
            )
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


class RoomMintInviteBody(SignedBody):
    room_id: str
    kind: InviteKind = "participant"
    # 0 used to fall back to the default, a negative value minted an invite
    # that had already expired, and a huge one overflowed timedelta into a 500.
    ttl_seconds: StrictInt | None = Field(default=None, ge=1, le=MAX_INVITE_TTL_SECONDS)


@router.post("/owner/rooms/{room_id}/invites", response_model=OwnerInviteResponse)
async def mint_invite_owner(room_id: str, body: RoomMintInviteBody, request: Request) -> OwnerInviteResponse:
    state = request.app.state
    if body.room_id != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
    room = await state.rooms.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail={"code": "org.agentstorming.err.room_unknown"})
    ttl = body.ttl_seconds if body.ttl_seconds is not None else state.settings.invite_ttl_seconds_default
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


class ModeratorVacateBody(SignedBody):
    room_id: str


@router.delete("/owner/rooms/{room_id}/moderator", response_model=dict)
async def vacate_moderator_seat(room_id: str, body: ModeratorVacateBody, request: Request) -> dict:
    """Vacate the moderator seat so a fresh moderator invite can be redeemed.

    Signed-owner call. Body delivered as JSON body even though DELETE —
    FastAPI allows. Demotes the current original-moderator to plain
    `member`. Does NOT destroy their participant entry; they remain
    in the room as a regular member.
    """
    state = request.app.state
    if body.room_id != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
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


class PromoteModeratorBody(SignedBody):
    """§7.5.6 — make_moderator_permanent."""
    room_id: str
    target_pid: str


@router.post("/owner/rooms/{room_id}/moderator", response_model=dict)
async def promote_moderator(room_id: str, body: PromoteModeratorBody, request: Request) -> dict:
    """Make a specific participant the original-moderator, replacing the current one."""
    state = request.app.state
    if body.room_id != room_id:
        raise HTTPException(status_code=400, detail={"code": "org.agentstorming.err.room_mismatch"})
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


class RegisterOwnerKeyBody(SignedBody):
    """Add a new owner key. Signed by an EXISTING owner key."""
    new_pubkey: str = Field(..., description="base64url raw Ed25519 32-byte key")
    label: str | None = None


@router.post("/owner/keys", response_model=dict)
async def register_owner_key(body: RegisterOwnerKeyBody, request: Request) -> dict:
    state = request.app.state
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


class RevokeOwnerKeyBody(SignedBody):
    target_pubkey: str


@router.post("/owner/keys/revoke", response_model=dict)
async def revoke_owner_key(body: RevokeOwnerKeyBody, request: Request) -> dict:
    state = request.app.state
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
