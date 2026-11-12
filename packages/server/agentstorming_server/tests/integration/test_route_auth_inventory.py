# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Every route either authenticates its caller or is public on purpose.

The reported defect was three owner reads that never checked a signature. Nothing in
the route table set them apart from the signed routes beside them, and the
attachment download had the same defect. This test reads the whole table, so a
route added later is covered without anyone remembering to add it here. Each
route must be listed in ``PUBLIC`` with the reason it is public, or meet both
of these conditions:

- it refuses a request that carries no credentials with 401 or 403 (400 or 401
  from the owner-signature dependency), and nothing from the room in the
  response, and
- its handler visibly authenticates: it sits behind an owner-signature
  dependency, or its own body calls ``state.auth``.

A new route has to either authenticate or be argued onto the list. The walk
(``tests/route_walk.py``) reads nested routers and mounts on every FastAPI
release the server allows, and fails on a route type it does not know.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import APIRouter, Depends, FastAPI
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from agentstorming_server.routers import v1_owner

from ..route_walk import calls_auth_gate, routes_of, walk

SIGNATURE_GUARDS = {v1_owner.require_owner_signature, v1_owner.require_owner_signature_without_nonce}

#: Routes reachable without a bearer token or an owner signature, and why.
PUBLIC = {
    ("GET", "/healthz"): "liveness probe",
    ("GET", "/readyz"): "readiness probe; answers ok or 503 and nothing else",
    ("GET", "/openapi.json"): "API schema; the API is published with the source",
    ("GET", "/docs"): "API schema UI",
    ("GET", "/docs/oauth2-redirect"): "API schema UI",
    ("GET", "/redoc"): "API schema UI",
    ("GET", "/v1/capabilities"): "static feature list",
    ("GET", "/v1/rooms"): "public room discovery, visibility=public rooms only",
    ("GET", "/v1/rooms/{room_id}/register/nonce"): "nonce for a candidate with no token yet",
    ("POST", "/v1/rooms/{room_id}/register"): "public registration, visibility=public rooms only",
    ("POST", "/v1/rooms/{room_id}/register/credentials"): "authenticated by the candidate's signature",
    ("POST", "/v1/rooms/{room_id}/claim"): "authenticated by the one-shot invite",
    ("POST", "/v1/rooms/{room_id}/claim_moderator"): "authenticated by the one-shot invite",
    ("POST", "/v1/rooms/{room_id}/claim_owner"): "authenticated by the one-shot invite",
    ("POST", "/v1/{room_id}/claim"): "legacy mount of the claim router",
    ("POST", "/v1/{room_id}/claim_moderator"): "legacy mount of the claim router",
    ("POST", "/v1/{room_id}/claim_owner"): "legacy mount of the claim router",
    ("POST", "/v1/rooms/tokens/refresh"): "authenticated by the refresh token",
    ("POST", "/v1/tokens/refresh"): "authenticated by the refresh token",
}

#: Bodies that pass validation, for routes that validate before authenticating.
#: Without one, a 422 would hide whether the route authenticates at all.
VALID_BODIES = {
    ("POST", "/v1/rooms/{room_id}/documents"): {"title": "probe"},
    ("PUT", "/v1/rooms/{room_id}/documents/{doc_id}"): {"title": "probe"},
    ("POST", "/v1/rooms/{room_id}/moderation/registration-door/close"): {"duration_seconds": 60},
    ("POST", "/v1/rooms/{room_id}/moderation/registrations/{iid}/accept"): {"interview_id": "probe"},
    ("POST", "/v1/rooms/{room_id}/moderation/registrations/{iid}/reject"): {"interview_id": "probe"},
    ("POST", "/v1/attachments/refresh"): {"att_id": str(uuid.uuid4())},
}

#: Long-lived streams: checked statically only, since an anonymous request to a
#: working stream would hang rather than return.
STREAMS = {("GET", "/v1/rooms/{room_id}/stream")}


@pytest.mark.asyncio
async def test_the_public_list_matches_the_app(app_client):
    """A stale PUBLIC entry is a route that moved or vanished without review."""
    served = set(routes_of(app_client._transport.app))
    assert set(PUBLIC) - served == set()


@pytest.mark.asyncio
async def test_every_other_route_refuses_an_anonymous_caller(app_client, fresh_room):
    app = app_client._transport.app
    room = fresh_room["room_id"]
    subs = {
        "{room_id}": room, "{att_id}": str(uuid.uuid4()), "{doc_id}": str(uuid.uuid4()),
        "{iid}": str(uuid.uuid4()), "{grant_id}": str(uuid.uuid4()), "{target_pid}": "probe",
    }
    checked = 0
    for (method, path), info in sorted(routes_of(app).items()):
        if (method, path) in PUBLIC:
            continue

        assert info.endpoint is not None, f"{method} {path} is not an API route"
        signed = bool(SIGNATURE_GUARDS & set(info.dependencies))
        assert signed or calls_auth_gate(info.endpoint), (
            f"{method} {path} neither sits behind the owner signature nor calls state.auth; "
            "authenticate it, or add it to PUBLIC with the reason it is public"
        )

        if (method, path) in STREAMS:
            continue
        url = path
        for name, value in subs.items():
            url = url.replace(name, value)
        if method == "GET":
            kwargs = {}
        elif path.endswith("/attachments") and method == "POST":
            kwargs = {"files": {"file": ("probe.txt", b"probe", "text/plain")}}
        else:
            kwargs = {"json": VALID_BODIES.get((method, path), {})}
        r = await app_client.request(method, url, **kwargs)

        allowed = {400, 401} if signed else {401, 403}
        assert r.status_code in allowed, (
            f"{method} {path} answered an anonymous caller with {r.status_code}: {r.text[:200]}"
        )
        assert room not in r.text, f"{method} {path} echoed the room to an anonymous caller"
        checked += 1

    # 48 when this was written. A much lower number means the walk missed routers.
    assert checked >= 46, f"only {checked} routes checked; the route walk is broken"


# ---- the walk itself ------------------------------------------------------


async def _guard() -> None:
    return None


def _toy_app() -> FastAPI:
    inner = APIRouter()

    @inner.get("/deep")
    async def deep() -> dict:
        return {}

    outer = APIRouter(dependencies=[Depends(_guard)])
    outer.include_router(inner, prefix="/inner")

    @outer.get("/shallow")
    async def shallow() -> dict:
        return {}

    app = FastAPI()
    app.include_router(outer, prefix="/v1")
    return app


def test_the_walk_reaches_nested_routers_with_their_dependencies():
    found = {(i.method, i.path): i for i in walk(_toy_app().routes)}
    assert ("GET", "/v1/shallow") in found and ("GET", "/v1/inner/deep") in found
    assert _guard in found[("GET", "/v1/inner/deep")].dependencies


def test_the_walk_looks_inside_mounts_and_refuses_what_it_finds_there():
    app = _toy_app()
    app.router.routes.append(Mount("/mounted", routes=[
        Route("/thing", lambda request: PlainTextResponse("x"), methods=["GET"]),
    ]))
    with pytest.raises(AssertionError, match="/mounted/thing"):
        list(walk(app.routes))


def test_the_walk_refuses_a_route_it_cannot_check():
    app = FastAPI()
    app.add_route("/raw", lambda request: PlainTextResponse("x"))
    with pytest.raises(AssertionError, match="unrecognised route"):
        list(walk(app.routes))


def test_a_docstring_mentioning_the_auth_gate_does_not_count():
    async def pretends(request):
        """Calls state.auth.principal(room_id, authorization), honestly."""
        return {}

    async def does(request, room_id, authorization):
        await request.app.state.auth.principal(room_id, authorization)

    assert not calls_auth_gate(pretends)
    assert calls_auth_gate(does)
