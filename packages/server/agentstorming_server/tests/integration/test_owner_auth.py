# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The owner surface is signed end to end, reads included.

``GET /v1/owner/rooms``, ``GET /v1/owner/rooms/{id}`` and ``GET /v1/owner/keys``
used to answer anyone: the first two listed and described private rooms, the
third listed the owner keys. The fix moved the signature check onto the owner
router, so these tests check it there. Every route on that router refuses an
unsigned request, a signature only verifies on the method and path it was made
for, and the owner can still do everything, reads included.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from agentstorming_server.app import create_app
from agentstorming_server.config import Settings
from agentstorming_server.routers import v1_owner
from agentstorming_server.services.jcs import canonicalise
from agentstorming_server.services.sig import generate_keypair, sign_blob

from ..conftest import DSN
from ..route_walk import routes_of

PRIVATE_ROOM = "owner-auth-private"
PRIVATE_TITLE = "Acquisition planning, do not list"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _now(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _sign(method: str, path: str, fields: dict, nonce: str | None, ts: str, priv: bytes) -> str:
    payload = {**fields, "ts": ts, "op": f"{method} {v1_owner.op_path(path)}"}
    if nonce is not None:
        payload["nonce"] = nonce
    return sign_blob(canonicalise(payload), priv)


def _nonce_headers(priv: bytes, pub: bytes, ts: str | None = None) -> dict:
    ts = ts or _now()
    return {
        "X-AgentStorming-Owner-Ts": ts,
        "X-AgentStorming-Owner-Pubkey": _b64url(pub),
        "X-AgentStorming-Owner-Sig": _sign("GET", "/v1/owner/nonce", {}, None, ts, priv),
    }


async def _nonce(o, *, priv=None, pub=None) -> str:
    priv, pub = priv or o["priv"], pub or o["pub"]
    r = await o["client"].get("/v1/owner/nonce", headers=_nonce_headers(priv, pub))
    assert r.status_code == 200, r.text
    return r.json()["nonce"]


async def _signed_headers(o, method: str, path: str, *, priv=None, pub=None, ts=None,
                          nonce_key=None) -> dict:
    """``nonce_key`` obtains the nonce; by default the fixture's owner key, so a
    request signed with a stranger's or a revoked key still carries a real one."""
    priv, pub = priv or o["priv"], pub or o["pub"]
    npriv, npub = nonce_key or (o["priv"], o["pub"])
    nonce, ts = await _nonce(o, priv=npriv, pub=npub), ts or _now()
    return {
        "X-AgentStorming-Owner-Nonce": nonce,
        "X-AgentStorming-Owner-Ts": ts,
        "X-AgentStorming-Owner-Pubkey": _b64url(pub),
        "X-AgentStorming-Owner-Sig": _sign(method, path, {}, nonce, ts, priv),
    }


async def _signed_body(o, method: str, path: str, fields: dict, *, priv=None, pub=None,
                       nonce_key=None) -> dict:
    priv, pub = priv or o["priv"], pub or o["pub"]
    npriv, npub = nonce_key or (priv, pub)
    nonce, ts = await _nonce(o, priv=npriv, pub=npub), _now()
    sig = _sign(method, path, fields, nonce, ts, priv)
    return {**fields, "nonce": nonce, "ts": ts, "pubkey": _b64url(pub), "sig": sig}


async def _create_private_room(o) -> None:
    path = "/v1/owner/rooms"
    body = await _signed_body(o, "POST", path, {
        "room_id": PRIVATE_ROOM, "title": PRIVATE_TITLE, "config": {"visibility": "private"},
    })
    r = await o["client"].post(path, json=body)
    assert r.status_code == 200, r.text


def _owner_routes(app) -> list[tuple[str, str]]:
    """Every (method, path) the app serves under /v1/owner, read from the app itself."""
    return sorted(k for k in routes_of(app) if k[1].startswith("/v1/owner/"))


# ---- the reported reads ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/v1/owner/rooms",
    f"/v1/owner/rooms/{PRIVATE_ROOM}",
    "/v1/owner/keys",
])
async def test_unsigned_reads_are_refused_and_leak_nothing(owner, path):
    await _create_private_room(owner)
    r = await owner["client"].get(path)
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.owner_auth_required"
    assert PRIVATE_ROOM not in r.text and PRIVATE_TITLE not in r.text
    assert _b64url(owner["pub"]) not in r.text


@pytest.mark.asyncio
async def test_signed_reads_still_work_for_the_owner(owner):
    await _create_private_room(owner)
    c = owner["client"]

    r = await c.get("/v1/owner/rooms", headers=await _signed_headers(owner, "GET", "/v1/owner/rooms"))
    assert r.status_code == 200, r.text
    assert [room["room_id"] for room in r.json()["rooms"]] == [PRIVATE_ROOM]

    path = f"/v1/owner/rooms/{PRIVATE_ROOM}"
    r = await c.get(path, headers=await _signed_headers(owner, "GET", path))
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "private" and r.json()["title"] == PRIVATE_TITLE

    r = await c.get("/v1/owner/keys", headers=await _signed_headers(owner, "GET", "/v1/owner/keys"))
    assert r.status_code == 200, r.text
    assert [k["pubkey"] for k in r.json()["keys"]] == [_b64url(owner["pub"])]


# ---- the router-level guarantee -------------------------------------------


@pytest.mark.asyncio
async def test_every_owner_route_refuses_an_unsigned_request(owner):
    """Holds for routes added later too: it reads the route table, not a list."""
    await _create_private_room(owner)
    routes = _owner_routes(owner["app"])
    assert ("GET", "/v1/owner/nonce") in routes
    assert len(routes) >= 13, routes

    for method, template in routes:
        path = template.replace("{room_id}", PRIVATE_ROOM)
        kwargs = {} if method == "GET" else {"json": {"room_id": PRIVATE_ROOM}}
        r = await owner["client"].request(method, path, **kwargs)
        assert r.status_code in (400, 401), f"{method} {path} -> {r.status_code} {r.text}"
        assert PRIVATE_TITLE not in r.text, f"{method} {path} leaked the room"

    # Nothing above changed state: the room is still there and still private.
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}"
    r = await owner["client"].get(path, headers=await _signed_headers(owner, "GET", path))
    assert r.json()["state"] == "ACTIVE" and r.json()["visibility"] == "private"


@pytest.mark.asyncio
async def test_owner_routes_all_carry_a_signature_dependency(owner):
    """A route on either owner router cannot opt out, the nonce route included."""
    assert [r.path for r in v1_owner.nonce_router.routes] == ["/owner/nonce"]
    assert [d.dependency for d in v1_owner.nonce_router.dependencies] == [
        v1_owner.require_owner_signature_without_nonce]
    assert [d.dependency for d in v1_owner.router.dependencies] == [v1_owner.require_owner_signature]
    guards = {v1_owner.require_owner_signature, v1_owner.require_owner_signature_without_nonce}
    for (method, path), info in routes_of(owner["app"]).items():
        if path.startswith("/v1/owner/"):
            assert guards & set(info.dependencies), f"{method} {path} has no signature guard"


# ---- what a signature is bound to ---------------------------------------


@pytest.mark.asyncio
async def test_an_unsigned_nonce_request_gets_no_nonce(owner):
    r = await owner["client"].get("/v1/owner/nonce")
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.owner_auth_required"
    assert "nonce" not in r.json()


@pytest.mark.asyncio
async def test_a_stranger_cannot_obtain_a_nonce(owner):
    priv, pub = generate_keypair()
    r = await owner["client"].get("/v1/owner/nonce", headers=_nonce_headers(priv, pub))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "org.agentstorming.err.owner_unknown"


@pytest.mark.asyncio
async def test_anonymous_nonce_requests_cannot_lock_the_owner_out(owner):
    """Behind a load balancer every caller once shared one rate-limit bucket, and
    about 30 anonymous nonce requests a minute starved every owner operation."""
    async with owner["app"].state.db.acquire() as conn:
        before = await conn.fetchval("SELECT count(*) FROM owner_nonces")
    for _ in range(100):
        assert (await owner["client"].get("/v1/owner/nonce")).status_code == 401
    async with owner["app"].state.db.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM owner_nonces") == before
    r = await owner["client"].get(
        "/v1/owner/rooms", headers=await _signed_headers(owner, "GET", "/v1/owner/rooms"))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_nonce_signature_only_obtains_a_nonce(owner):
    """The nonce request is signed over {ts, op}; that cannot pass as a read."""
    headers = _nonce_headers(owner["priv"], owner["pub"])
    headers["X-AgentStorming-Owner-Nonce"] = await _nonce(owner)
    r = await owner["client"].get("/v1/owner/keys", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "org.agentstorming.err.sig_invalid"


@pytest.mark.asyncio
async def test_a_rate_limited_request_keeps_its_nonce(owner, monkeypatch):
    from fastapi import HTTPException
    limiter = owner["app"].state.rate_limiter
    real = limiter.check
    calls = {"n": 0}

    async def once_limited(key, op):
        calls["n"] += 1
        if op == "owner" and calls["n"] == 1:
            raise HTTPException(429, detail={"code": "org.agentstorming.err.rate_limited"})
        return await real(key, op)

    headers = await _signed_headers(owner, "GET", "/v1/owner/keys")
    monkeypatch.setattr(limiter, "check", once_limited)
    assert (await owner["client"].get("/v1/owner/keys", headers=headers)).status_code == 429
    r = await owner["client"].get("/v1/owner/keys", headers=headers)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_question_mark_in_a_room_id_does_not_alias_another_endpoint(owner):
    """``op`` joined the decoded path to the raw query, so the signature for room
    ``a?b`` (sent as ``a%3Fb``) also verified for room ``a`` with query ``b``."""
    c = owner["client"]
    for room_id in ("alias", "alias?b"):
        path = "/v1/owner/rooms"
        r = await c.post(path, json=await _signed_body(owner, "POST", path, {"room_id": room_id}))
        assert r.status_code == 200, r.text

    headers = await _signed_headers(owner, "GET", "/v1/owner/rooms/alias?b")
    r = await c.get("/v1/owner/rooms/alias?b", headers=headers)
    assert r.status_code == 403, r.text
    r = await c.get("/v1/owner/rooms/alias%3Fb", headers=headers)
    assert r.status_code == 200 and r.json()["room_id"] == "alias?b", r.text
    assert v1_owner.op_path("/v1/owner/rooms/50%?x") == "/v1/owner/rooms/50%25%3Fx"


@pytest.mark.asyncio
@pytest.mark.parametrize("ts", ["2026-11-02T10:00:00", "not-a-time", "2026-11-02"])
async def test_a_timestamp_without_an_offset_is_a_400_not_a_500(owner, ts):
    headers = await _signed_headers(owner, "GET", "/v1/owner/keys")
    headers["X-AgentStorming-Owner-Ts"] = ts
    r = await owner["client"].get("/v1/owner/keys", headers=headers)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.ts_invalid"

    body = await _signed_body(owner, "POST", "/v1/owner/request-invite", {"room_id": PRIVATE_ROOM})
    body["ts"] = ts
    r = await owner["client"].post("/v1/owner/request-invite", json=body)
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    '{"x": NaN}', '{"x": Infinity}', '{"x": 1e400}', '{"x": "\\ud800"}',
    "[" * 5000 + "]" * 5000, '{"a":' * 3000 + "1" + "}" * 3000,
])
async def test_a_body_without_a_canonical_form_is_a_400_not_a_500(owner, raw):
    """It needs no secret: the public owner key and a junk signature are enough."""
    pub = _b64url(owner["pub"])
    if raw.startswith('{"x"'):
        raw = raw[:-1] + f', "room_id": "r", "nonce": "n", "ts": "{_now()}", "pubkey": "{pub}", "sig": "AAAA"}}'
    for method, path in (("POST", "/v1/owner/request-invite"),
                         ("DELETE", f"/v1/owner/rooms/{PRIVATE_ROOM}/moderator")):
        r = await owner["client"].request(
            method, path, content=raw, headers={"content-type": "application/json"})
        assert r.status_code == 400, f"{method} {path} {raw[:20]!r} -> {r.status_code} {r.text[:200]}"


@pytest.mark.asyncio
async def test_a_signed_patch_sets_config_and_refuses_unknown_fields(owner):
    """The PATCH dropped every field but title, description and visibility, so a
    signed ``raise_hand_required`` answered 200 and changed nothing."""
    await _create_private_room(owner)
    c = owner["client"]
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}"

    r = await c.patch(path, json=await _signed_body(owner, "PATCH", path, {
        "room_id": PRIVATE_ROOM, "raise_hand_required": True, "max_participants": 7,
        "attachments": {"max_bytes": 1024},
    }))
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["raise_hand_required"] is True and cfg["max_participants"] == 7
    assert cfg["attachments"]["max_bytes"] == 1024 and cfg["attachments"]["enabled"] is True

    for bad in ({"no_such_field": True}, {"config": {"raise_hand_required": False}},
                {"max_participants": 0}, {"raise_hand_required": "yes"},
                {"attachments": {"backend": "s3"}}, {"mode": "paused"}):
        r = await c.patch(path, json=await _signed_body(owner, "PATCH", path, {
            "room_id": PRIVATE_ROOM, **bad}))
        assert r.status_code == 422, f"{bad} -> {r.status_code} {r.text[:200]}"

    r = await c.get(path, headers=await _signed_headers(owner, "GET", path))
    assert r.json()["config"]["raise_hand_required"] is True


@pytest.mark.asyncio
async def test_an_owner_mode_change_emits_a_signed_mode_event(owner):
    """The broker learns the mode only from server-signed events, so a demotion
    that emitted nothing left write tools allowed until the next snapshot."""
    await _create_private_room(owner)
    c, state = owner["client"], owner["app"].state
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}"
    for mode in ("planning", "active"):
        r = await c.patch(path, json=await _signed_body(owner, "PATCH", path, {
            "room_id": PRIVATE_ROOM, "mode": mode}))
        assert r.status_code == 200 and r.json()["config"]["mode"] == mode, r.text
    events = await state.events.get_after(PRIVATE_ROOM, -1, 100)
    moves = [(e["payload"]["from_mode"], e["payload"]["to_mode"], e["payload"]["promoted_by"])
             for e in events if e["type"] == "org.agentstorming.mode_promoted"]
    assert moves == [("active", "planning", "room-owner"), ("planning", "active", "room-owner")]
    assert all(e.get("sig") for e in events if e["type"] == "org.agentstorming.mode_promoted")


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [0, -60, 10**12, "3600", 1.5])
async def test_an_invite_ttl_out_of_range_is_refused(owner, ttl):
    await _create_private_room(owner)
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}/invites"
    r = await owner["client"].post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": PRIVATE_ROOM, "kind": "participant", "ttl_seconds": ttl}))
    assert r.status_code == 422, f"{ttl!r} -> {r.status_code} {r.text[:200]}"


@pytest.mark.asyncio
async def test_an_invite_ttl_in_range_is_honoured(owner):
    await _create_private_room(owner)
    path = f"/v1/owner/rooms/{PRIVATE_ROOM}/invites"
    r = await owner["client"].post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": PRIVATE_ROOM, "kind": "participant", "ttl_seconds": 120}))
    assert r.status_code == 200, r.text
    expires = datetime.fromisoformat(r.json()["expires_at"])
    assert abs((expires - datetime.now(timezone.utc)).total_seconds() - 120) < 30


@pytest.mark.asyncio
async def test_the_legacy_invite_route_validates_kind(owner):
    await _create_private_room(owner)
    path = "/v1/owner/request-invite"
    r = await owner["client"].post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": PRIVATE_ROOM, "kind": "superuser"}))
    assert r.status_code == 422, r.text
    r = await owner["client"].post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": PRIVATE_ROOM}))
    assert r.status_code == 200 and r.json()["kind"] == "owner", r.text


@pytest.mark.asyncio
async def test_expired_nonces_are_purged(owner):
    state = owner["app"].state
    async with state.db.acquire() as conn:
        await conn.execute(
            "INSERT INTO owner_nonces(nonce, expires_at) VALUES ($1, now() - interval '2 hours'),"
            " ($2, now() + interval '1 minute')", "old-nonce", "live-nonce")
    assert await state.owner_nonces.purge_expired() >= 1
    async with state.db.acquire() as conn:
        left = {r["nonce"] for r in await conn.fetch("SELECT nonce FROM owner_nonces")}
    assert "old-nonce" not in left and "live-nonce" in left


@pytest.mark.asyncio
async def test_owner_responses_are_not_cacheable(owner):
    r = await owner["client"].get(
        "/v1/owner/keys", headers=await _signed_headers(owner, "GET", "/v1/owner/keys"))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert (await owner["client"].get("/v1/owner/keys")).headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_a_key_that_is_not_an_owner_key_is_refused(owner):
    priv, pub = generate_keypair()
    headers = await _signed_headers(owner, "GET", "/v1/owner/rooms", priv=priv, pub=pub)
    r = await owner["client"].get("/v1/owner/rooms", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "org.agentstorming.err.owner_unknown"


@pytest.mark.asyncio
async def test_a_read_signature_does_not_open_another_read(owner):
    headers = await _signed_headers(owner, "GET", "/v1/owner/rooms")
    r = await owner["client"].get("/v1/owner/keys", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "org.agentstorming.err.sig_invalid"
    # The refused attempt did not spend the nonce: the request it was made for still works.
    r = await owner["client"].get("/v1/owner/rooms", headers=headers)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_vacate_signature_cannot_terminate_the_room(owner):
    """``terminate`` and ``vacate`` once signed the identical {room_id, nonce, ts}."""
    await _create_private_room(owner)
    vacate = f"/v1/owner/rooms/{PRIVATE_ROOM}/moderator"
    body = await _signed_body(owner, "DELETE", vacate, {"room_id": PRIVATE_ROOM})

    r = await owner["client"].post(f"/v1/owner/rooms/{PRIVATE_ROOM}/terminate", json=body)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.sig_invalid"

    path = f"/v1/owner/rooms/{PRIVATE_ROOM}"
    r = await owner["client"].get(path, headers=await _signed_headers(owner, "GET", path))
    assert r.json()["state"] == "ACTIVE"


@pytest.mark.asyncio
async def test_a_body_that_names_another_op_is_refused(owner):
    body = await _signed_body(owner, "POST", "/v1/owner/request-invite", {"room_id": PRIVATE_ROOM})
    body["op"] = "POST /v1/owner/keys"
    r = await owner["client"].post("/v1/owner/request-invite", json=body)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "org.agentstorming.err.op_mismatch"


@pytest.mark.asyncio
async def test_a_tampered_body_is_refused(owner):
    await _create_private_room(owner)
    path = "/v1/owner/request-invite"
    body = await _signed_body(owner, "POST", path, {"room_id": PRIVATE_ROOM, "kind": "participant"})
    body["kind"] = "owner"
    r = await owner["client"].post(path, json=body)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "org.agentstorming.err.sig_invalid"


@pytest.mark.asyncio
async def test_a_signed_read_cannot_be_replayed(owner):
    headers = await _signed_headers(owner, "GET", "/v1/owner/keys")
    assert (await owner["client"].get("/v1/owner/keys", headers=headers)).status_code == 200
    r = await owner["client"].get("/v1/owner/keys", headers=headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org.agentstorming.err.nonce_invalid"


@pytest.mark.asyncio
async def test_a_stale_timestamp_is_refused(owner):
    headers = await _signed_headers(owner, "GET", "/v1/owner/keys", ts=_now(-600))
    r = await owner["client"].get("/v1/owner/keys", headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "org.agentstorming.err.ts_skew"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["nonce", "ts", "pubkey", "sig"])
async def test_a_non_string_signature_field_is_a_400_not_a_500(owner, bad):
    path = "/v1/owner/request-invite"
    body = await _signed_body(owner, "POST", path, {"room_id": PRIVATE_ROOM})
    body[bad] = 12345
    r = await owner["client"].post(path, json=body)
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_a_revoked_owner_key_loses_the_reads(owner):
    c = owner["client"]
    second_priv, second_pub = generate_keypair()
    path = "/v1/owner/keys"
    r = await c.post(path, json=await _signed_body(owner, "POST", path, {
        "new_pubkey": _b64url(second_pub), "label": "laptop-2",
    }))
    assert r.status_code == 200, r.text

    path = "/v1/owner/keys/revoke"
    r = await c.post(path, json=await _signed_body(owner, "POST", path, {
        "target_pubkey": _b64url(owner["pub"]),
    }, priv=second_priv, pub=second_pub))
    assert r.status_code == 200, r.text

    second = (second_priv, second_pub)
    r = await c.get("/v1/owner/keys", headers=await _signed_headers(
        owner, "GET", "/v1/owner/keys", nonce_key=second))
    assert r.status_code == 403
    r = await c.get("/v1/owner/nonce", headers=_nonce_headers(owner["priv"], owner["pub"]))
    assert r.status_code == 403, "a revoked key must not obtain a nonce either"
    r = await c.get("/v1/owner/keys", headers=await _signed_headers(
        owner, "GET", "/v1/owner/keys", priv=second_priv, pub=second_pub, nonce_key=second))
    assert [k["label"] for k in r.json()["keys"]] == ["laptop-2"]


# ---- the signed writes still work through the router guard --------------


@pytest.mark.asyncio
async def test_every_signed_write_still_works(owner):
    """Moving verification to the router removed nine per-handler calls."""
    c = owner["client"]
    await _create_private_room(owner)
    room = PRIVATE_ROOM

    path = f"/v1/owner/rooms/{room}"
    r = await c.patch(path, json=await _signed_body(owner, "PATCH", path, {
        "room_id": room, "visibility": "public",
    }))
    assert r.status_code == 200 and r.json()["visibility"] == "public", r.text

    path = f"/v1/owner/rooms/{room}/invites"
    r = await c.post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": room, "kind": "moderator",
    }))
    assert r.status_code == 200, r.text
    mod_invite = r.json()["invite_token"]

    path = "/v1/owner/request-invite"
    r = await c.post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": room, "kind": "participant",
    }))
    assert r.status_code == 200 and r.json()["kind"] == "participant", r.text
    part_invite = r.json()["invite_token"]

    _mpriv, mpub = generate_keypair()
    r = await c.post(f"/v1/rooms/{room}/claim", json={"invite_token": mod_invite, "pubkey": _b64url(mpub)})
    assert r.status_code == 200, r.text
    _ppriv, ppub = generate_keypair()
    r = await c.post(f"/v1/rooms/{room}/claim", json={"invite_token": part_invite, "pubkey": _b64url(ppub)})
    assert r.status_code == 200, r.text
    member_pid = r.json()["pid"]

    path = f"/v1/owner/rooms/{room}/moderator"
    r = await c.request("DELETE", path, json=await _signed_body(owner, "DELETE", path, {"room_id": room}))
    assert r.status_code == 200 and r.json()["moderator_seat"] == "vacant", r.text

    r = await c.post(path, json=await _signed_body(owner, "POST", path, {
        "room_id": room, "target_pid": member_pid,
    }))
    assert r.status_code == 200 and r.json()["moderator_pid"] == member_pid, r.text

    path = f"/v1/owner/rooms/{room}/terminate"
    r = await c.post(path, json=await _signed_body(owner, "POST", path, {"room_id": room}))
    assert r.status_code == 200 and r.json()["state"] == "TERMINATED", r.text


@pytest.mark.asyncio
async def test_api_responses_stay_uncacheable_under_a_root_path(reset_db, tmp_path):
    """The default used to test the full path, so a mount prefix skipped it."""
    os.environ["AGENTSTORMING_DSN"] = DSN
    app = create_app(Settings(dsn=DSN, attachments_local_dir=tmp_path / "a"))
    transport = httpx.ASGITransport(app=app, root_path="/storm")
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client, \
            app.router.lifespan_context(app):
        r = await client.get("/storm/v1/owner/keys")
        assert r.status_code == 401 and r.headers["cache-control"] == "no-store", r.headers
