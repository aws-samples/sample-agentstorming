# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Claim endpoints: participant, moderator, owner invite redemption."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..domain.event import TYPE_PARTICIPANT_JOINED
from ..domain.ids import make_pid
from ..domain.participant import Participant

router = APIRouter(tags=["claim"])


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _check_kind(kind: str, expected_kind: str | None) -> None:
    if expected_kind is not None and expected_kind != kind:
        raise HTTPException(403, detail={"code": "org.agentstorming.err.invite_kind_mismatch",
                                         "message": f"invite is for {kind}, not {expected_kind}"})


async def _check_seat_free(state, room_id: str, pid: str, *, conn=None) -> None:
    """Fail-if-occupied for the moderator seat (§11): one original-moderator per
    room, and the owner must vacate it first."""
    existing = await state.participants.get_by_affiliation(room_id, "original-moderator", conn=conn)
    if existing and existing.pid != pid:
        raise HTTPException(
            409,
            detail={
                "code": "org.agentstorming.err.moderator_seat_occupied",
                "message": (
                    "a moderator already holds the seat; the room-owner "
                    "must vacate via DELETE /v1/owner/rooms/{id}/moderator "
                    "before a new moderator invite can be redeemed"
                ),
            },
        )


async def _claim(
    room_id: str,
    body: dict[str, Any],
    request: Request,
    expected_kind: str | None = None,
) -> dict[str, Any]:
    state = request.app.state
    invite_token = body.get("invite_token")
    pubkey_b64 = body.get("pubkey")
    if not invite_token or not pubkey_b64:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_body",
                                         "message": "invite_token and pubkey required"})
    if not isinstance(invite_token, str) or not isinstance(pubkey_b64, str):
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_body",
                                         "message": "invite_token and pubkey must be strings"})
    try:
        pubkey = _b64url_decode(pubkey_b64)
    except Exception:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_pubkey",
                                         "message": "pubkey must be base64url of 32 raw Ed25519 bytes"})
    if len(pubkey) != 32:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_pubkey",
                                         "message": "pubkey must be 32 bytes"})

    # The token is checked first, without consuming it, so a caller holding no
    # valid invite learns nothing about the room. Capacity used to be checked
    # first, and a junk token sent to a full private room answered room_full
    # with its capacity where a missing room answered invite_invalid. The
    # remaining checks still run before the one-shot token is consumed.
    pending = await state.invites.peek(invite_token)
    if pending is None:
        raise HTTPException(409, detail={"code": "org.agentstorming.err.invite_invalid",
                                         "message": "invite token invalid or already consumed"})
    if pending["room_id"] != room_id:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.room_mismatch",
                                         "message": "invite does not match room"})
    # The kind and the moderator seat are known from the unconsumed row too,
    # and were checked only after redeem(), so a participant invite sent to
    # claim_moderator, or a moderator invite while the seat was held, burned
    # an invite the owner then had to re-mint.
    _check_kind(pending["kind"], expected_kind)

    # §5.2 — enforce max_participants before consuming the invite.
    room_obj = await state.rooms.get(room_id)
    if room_obj is not None and room_obj.config.max_participants:
        active = await state.participants.count_active(room_id)
        if active >= room_obj.config.max_participants:
            raise HTTPException(
                409, detail={
                    "code": "org.agentstorming.err.room_full",
                    "message": f"room is at capacity ({room_obj.config.max_participants})",
                },
            )

    # §12.3 — a penned pubkey cannot redeem invites until its pen expires,
    # regardless of which room minted the invite or which room this claim
    # targets. Check before we consume the one-shot invite token.
    if await state.participants.is_pubkey_penned(pubkey):
        raise HTTPException(
            403,
            detail={
                "code": "org.agentstorming.err.pen_active",
                "message": "this pubkey is currently penned",
            },
        )

    pid = make_pid(pubkey, room_id)
    # A key that already has a row in this room cannot claim again: the insert
    # below is ON CONFLICT DO NOTHING, so the claim used to spend the invite,
    # answer 200 with the invite's affiliation and broadcast a signed
    # participant_joined saying so, while the stored row stayed what it was.
    #
    # A claim proves nothing about who holds the key, so re-issuing tokens for a
    # seated key would hand that seat, the moderator's included, to anyone with
    # an invite and its public key. This read turns most of them away early;
    # the insert below is what decides, so two claims racing for one key cannot
    # both be seated.
    if await state.participants.get(room_id, pid) is not None:
        raise _already_joined()
    if pending["kind"] == "moderator":
        p, kind = await _claim_moderator_serialised(state, room_id, pid, invite_token, body,
                                                    expected_kind, pubkey)
    else:
        async with state.db.tx() as conn:
            p, kind = await _redeem_and_seat(state, room_id, pid, invite_token, body,
                                             expected_kind, pubkey, conn=conn)
    return await _welcome(state, room_id, p, kind, pubkey_b64)


def _already_joined() -> HTTPException:
    return HTTPException(409, detail={
        "code": "org.agentstorming.err.already_joined",
        "message": "this key already holds a seat in the room; claim with a new keypair",
    })


async def _claim_moderator_serialised(state, room_id, pid, invite_token, body,
                                      expected_kind, pubkey) -> tuple[Participant, str]:
    """Seat a moderator: one claim per room at a time.

    The seat check and the insert are separate statements, so two moderator
    invites redeemed at once both saw an empty seat and both were seated. They
    now run, with the redeem, in one transaction on one connection under a
    per-room transaction lock, and a claim that loses keeps its invite.

    The first version held the lock on one pooled connection and did the work
    on others, so ten claims queued on the lock held the whole pool while the
    holder waited for a second connection: every request on the server stalled
    until ``command_timeout``. The holder now needs nothing beyond its own
    connection, and the unlocked look first sends every claim on a taken seat
    away without queueing.
    """
    await _check_seat_free(state, room_id, pid)
    async with state.db.tx() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 7770))", room_id)
        await _check_seat_free(state, room_id, pid, conn=conn)
        return await _redeem_and_seat(state, room_id, pid, invite_token, body, expected_kind,
                                      pubkey, conn=conn)


async def _redeem_and_seat(state, room_id, pid, invite_token, body, expected_kind, pubkey, *,
                           conn=None) -> tuple[Participant, str]:
    """Spend the invite and insert the participant, inside the caller's
    transaction, so a refusal here leaves the invite unspent."""
    invite = await state.invites.redeem(invite_token, pid, conn=conn)
    if invite is None:
        raise HTTPException(409, detail={"code": "org.agentstorming.err.invite_invalid",
                                         "message": "invite token invalid or already consumed"})
    if invite["room_id"] != room_id:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.room_mismatch",
                                         "message": "invite does not match room"})
    kind = invite["kind"]
    # Kind is immutable, so this only restates the pre-redeem check.
    _check_kind(kind, expected_kind)

    # Affiliation from kind. Server decides based on the invite row.
    aff = {"participant": "member", "moderator": "original-moderator", "owner": "room-owner"}[kind]

    # runs_as is self-declared, optional; default 'agent' (§7.2).
    runs_as = body.get("runs_as") if isinstance(body, dict) else None
    if runs_as not in (None, "agent", "human"):
        runs_as = None

    now = datetime.now(timezone.utc)
    p = Participant(room_id=room_id, pid=pid, pubkey=pubkey, affiliation=aff,
                    deputy_rank=None, joined_at=now, last_seen_at=now,
                    runs_as=runs_as or "agent")
    if not await state.participants.insert(p, conn=conn):
        raise _already_joined()
    return p, kind


async def _welcome(state, room_id: str, p: Participant, kind: str,
                   pubkey_b64: str) -> dict[str, Any]:
    """Tokens, the join events and a snapshot for a participant just seated."""
    pid, aff = p.pid, p.affiliation
    access_ttl = state.settings.access_token_ttl_seconds
    refresh_ttl = state.settings.refresh_token_ttl_seconds
    pair = await state.tokens.issue_pair(room_id, pid, access_ttl, refresh_ttl)

    # Emit participant_joined. runs_as is kept server-side, NOT broadcast (§7.2).
    await state.sys_publish_event(
        room_id, TYPE_PARTICIPANT_JOINED,
        payload={"pid": pid, "pubkey": pubkey_b64, "affiliation": aff},
    )
    # Distinct owner_joined event when a room-owner lands in the room (§15 resolution: broadcast).
    if aff == "room-owner":
        await state.sys_publish_event(
            room_id, "org.agentstorming.owner_joined",
            payload={"pid": pid, "pubkey": pubkey_b64},
        )

    # Build a snapshot for the newcomer
    snap_env = await state.snapshot.build_envelope(
        room_id, sign_func=lambda d: state.sys_sign(room_id, d),
    )

    return {
        "pid": pid,
        "kind": kind,
        "affiliation": aff,
        "access_token": pair["access_token"],
        "refresh_token": pair["refresh_token"],
        "access_expires_at": pair["access_expires_at"].isoformat(),
        "refresh_expires_at": pair["refresh_expires_at"].isoformat(),
        "snapshot": snap_env.model_dump(mode="json"),
    }


@router.post("/{room_id}/claim")
async def claim(room_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Redeem any invite. The server infers the kind from the invite row.

    The response includes the resolved ``kind`` and ``affiliation`` so the
    client can render the UI (e.g. showing a Claim Moderator button for
    owner-affiliation sessions).
    """
    return await _claim(room_id, body, request)


# Kept as thin compatibility shims for clients pinning a specific kind.
# These do not change the authorization model — the invite record still
# determines the resulting affiliation; the shim only asserts that the
# invite kind matches what the client expected, returning 403 otherwise.
@router.post("/{room_id}/claim_moderator")
async def claim_moderator(room_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    return await _claim(room_id, body, request, expected_kind="moderator")


@router.post("/{room_id}/claim_owner")
async def claim_owner(room_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    return await _claim(room_id, body, request, expected_kind="owner")


@router.post("/{room_id}/owner/register_key")
async def owner_register_key(
    room_id: str, body: dict[str, Any], request: Request,
) -> dict[str, Any]:
    """§9.3 — after claiming the owner invite the room owner SHOULD
    register a dedicated Ed25519 public key for later out-of-band
    operations (e.g. owner token regeneration, claiming moderator on
    a frozen room). Multiple keys MAY be registered; each is scoped
    to this room only."""
    import base64
    from fastapi import Header as _Header  # local import — dep on typing

    state = request.app.state
    auth = request.headers.get("authorization")
    principal = await state.auth.principal(room_id, auth)
    if principal.participant.affiliation != "room-owner":
        raise HTTPException(
            status_code=403,
            detail={"code": "org.agentstorming.err.forbidden",
                    "message": "room-owner required"},
        )
    raw_pubkey = body.get("pubkey")
    if not raw_pubkey:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_body",
                                         "message": "pubkey required"})
    try:
        pub = _b64url_decode(raw_pubkey)
    except Exception:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_pubkey"})
    if len(pub) != 32:
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_pubkey"})
    await state.room_owner_keys.insert(room_id, pub, body.get("label"))
    return {"room_id": room_id, "pubkey": raw_pubkey, "state": "registered"}


@router.get("/{room_id}/owner/keys")
async def owner_list_keys(room_id: str, request: Request) -> dict[str, Any]:
    """List active per-room owner keys. Room-owner only."""
    import base64
    state = request.app.state
    auth = request.headers.get("authorization")
    principal = await state.auth.principal(room_id, auth)
    if principal.participant.affiliation != "room-owner":
        raise HTTPException(
            status_code=403,
            detail={"code": "org.agentstorming.err.forbidden",
                    "message": "room-owner required"},
        )
    rows = await state.room_owner_keys.list_active(room_id)
    def _enc(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")
    return {
        "room_id": room_id,
        "keys": [
            {
                "pubkey": _enc(r["pubkey"]),
                "label": r.get("label"),
                "registered_at": r["registered_at"].isoformat(),
            }
            for r in rows
        ],
    }


@router.post("/tokens/refresh")
async def refresh(body: dict[str, Any], request: Request) -> dict[str, Any]:
    import hashlib
    state = request.app.state
    refresh_token = body.get("refresh_token")
    if not refresh_token or not isinstance(refresh_token, str):
        raise HTTPException(400, detail={"code": "org.agentstorming.err.bad_body", "message": "refresh_token required"})
    # §20.4 — rate-limit refresh-token use to 1/second per token. Key by
    # the hash so we don't retain the plaintext token in bucket memory.
    token_key = hashlib.sha256(refresh_token.encode("utf-8", "surrogatepass")).hexdigest()[:32]
    await state.rate_limiter.check(f"rt:{token_key}", "token_refresh")
    # Rotate: the old refresh token is revoked and read in one statement, so
    # only one of several concurrent refreshes gets a new pair.
    info = await state.tokens.consume_refresh(refresh_token)
    if not info:
        raise HTTPException(401, detail={"code": "org.agentstorming.err.auth", "message": "invalid refresh token"})
    pair = await state.tokens.issue_pair(
        info["room_id"], info["pid"],
        state.settings.access_token_ttl_seconds,
        state.settings.refresh_token_ttl_seconds,
    )
    return {
        "access_token": pair["access_token"],
        "refresh_token": pair["refresh_token"],
        "access_expires_at": pair["access_expires_at"].isoformat(),
        "refresh_expires_at": pair["refresh_expires_at"].isoformat(),
    }
