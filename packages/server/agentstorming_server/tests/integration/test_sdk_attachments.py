# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""``StormClient.download_attachment`` against the real server.

Downloading an attachment now needs the member's bearer, and on the S3
backend the route redirects to a presigned URL that must be fetched without
it. The agent contract used to tell agents to fetch the URL bare, and no SDK
call sent the bearer, so the fix that closed the route also broke every agent
that read attachments until this call existed.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from agentstorming_client import ClientConfig, StormClient
from agentstorming_client.errors import StormError
from agentstorming_mcp.server import MCPServer

from agentstorming_server.services.sig import generate_keypair

from .test_attachment_access import CONTENT, _FakeS3Store, _upload

BASE = "http://testserver"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


async def _member(app_client, room_id: str, invite: str) -> dict:
    _priv, pub = generate_keypair()
    r = await app_client.post(f"/v1/rooms/{room_id}/claim", json={"invite_token": invite, "pubkey": _b64url(pub)})
    assert r.status_code == 200, r.text
    return r.json()


def _sdk(app, room_id: str, token: str, vault_dir, *, s3_seen: list | None = None) -> StormClient:
    """A StormClient whose HTTP goes to the in-process app; S3 requests are recorded."""
    asgi = httpx.ASGITransport(app=app)

    async def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "testserver":
            return await asgi.handle_async_request(request)
        if s3_seen is not None:
            s3_seen.append(request)
        return httpx.Response(200, content=CONTENT, headers={"content-type": "text/plain"})

    client = StormClient(ClientConfig(base_url=BASE, room_id=room_id, vault_dir=vault_dir))
    client.transport._client = httpx.AsyncClient(transport=httpx.MockTransport(route), base_url=BASE)
    client.vault.data.access_token = token
    return client


@pytest.mark.asyncio
async def test_download_attachment_sends_the_bearer(app_client, fresh_room, tmp_path):
    app = app_client._transport.app
    member = await _member(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    att_id = await _upload(app_client, fresh_room["room_id"], member["access_token"])

    got = await _sdk(app, fresh_room["room_id"], member["access_token"], tmp_path).download_attachment(att_id)
    assert got["data"] == CONTENT and got["size_bytes"] == len(CONTENT)

    with pytest.raises(StormError):
        await _sdk(app, fresh_room["room_id"], "not-a-token", tmp_path).download_attachment(att_id)


@pytest.mark.asyncio
async def test_download_attachment_follows_the_s3_redirect_without_the_bearer(app_client, fresh_room, tmp_path):
    app = app_client._transport.app
    app.state.attachments_store = _FakeS3Store(tmp_path)
    member = await _member(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    att_id = await _upload(app_client, fresh_room["room_id"], member["access_token"])

    seen: list[httpx.Request] = []
    got = await _sdk(app, fresh_room["room_id"], member["access_token"], tmp_path / "v",
                     s3_seen=seen).download_attachment(att_id)
    assert got["data"] == CONTENT
    assert len(seen) == 1 and seen[0].url.host == "bucket.s3.example"
    assert "authorization" not in seen[0].headers, "the bearer must never leave the server's origin"


@pytest.mark.asyncio
async def test_the_mcp_tool_inlines_text_and_describes_binary(app_client, fresh_room, tmp_path):
    app = app_client._transport.app
    member = await _member(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    text_id = await _upload(app_client, fresh_room["room_id"], member["access_token"])
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/attachments",
        files={"file": ("blob.bin", b"\x00\x01\xff" * 10, "application/octet-stream")},
        headers={"Authorization": f"Bearer {member['access_token']}"},
    )
    bin_id = r.json()["id"]

    server = MCPServer.__new__(MCPServer)
    server.client = _sdk(app, fresh_room["room_id"], member["access_token"], tmp_path)
    out = await server._download_attachment({"att_id": text_id})
    assert out["text"] == CONTENT.decode()
    out = await server._download_attachment({"att_id": bin_id})
    assert out["inlined"] is False and "text" not in out and out["size_bytes"] == 30
    json.dumps(out)  # what the bridge sends back must serialise


@pytest.mark.asyncio
async def test_the_mcp_tool_inlines_text_uploaded_as_octet_stream(app_client, fresh_room, tmp_path):
    """Older uploaders sent every file as application/octet-stream."""
    app = app_client._transport.app
    member = await _member(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/attachments",
        files={"file": ("notes", b"plain words", "application/octet-stream")},
        headers={"Authorization": f"Bearer {member['access_token']}"},
    )
    server = MCPServer.__new__(MCPServer)
    server.client = _sdk(app, fresh_room["room_id"], member["access_token"], tmp_path)
    out = await server._download_attachment({"att_id": r.json()["id"]})
    assert out["text"] == "plain words"
