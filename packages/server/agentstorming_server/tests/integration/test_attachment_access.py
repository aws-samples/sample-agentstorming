# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""``GET /v1/attachments/{id}`` serves current members of the owning room only.

The route accepted an Authorization header and never checked it, so an
attachment ID alone downloaded the file from any room, private rooms included,
and a participant who was ejected kept access to everything they had seen.
Found while sweeping every route after the unsigned owner reads were reported.
"""

from __future__ import annotations

import base64

import pytest

from agentstorming_server.repo.base import Database
from agentstorming_server.repo.participants import ParticipantRepo
from agentstorming_server.services.sig import generate_keypair

from ..conftest import DSN

CONTENT = b"quarterly numbers, private room only\n"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


async def _claim(client, room_id: str, invite: str) -> dict:
    _priv, pub = generate_keypair()
    r = await client.post(f"/v1/rooms/{room_id}/claim", json={"invite_token": invite, "pubkey": _b64url(pub)})
    assert r.status_code == 200, r.text
    return r.json()


async def _upload(client, room_id: str, token: str) -> str:
    r = await client.post(
        f"/v1/rooms/{room_id}/attachments",
        files={"file": ("numbers.txt", CONTENT, "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_an_attachment_id_alone_downloads_nothing(app_client, fresh_room):
    member = await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    att_id = await _upload(app_client, fresh_room["room_id"], member["access_token"])

    r = await app_client.get(f"/v1/attachments/{att_id}")
    assert r.status_code == 401, r.text
    assert CONTENT not in r.content


@pytest.mark.asyncio
async def test_a_member_of_the_room_downloads_it(app_client, fresh_room):
    member = await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    att_id = await _upload(app_client, fresh_room["room_id"], member["access_token"])

    r = await app_client.get(
        f"/v1/attachments/{att_id}", headers={"Authorization": f"Bearer {member['access_token']}"},
    )
    assert r.status_code == 200, r.text
    assert r.content == CONTENT


@pytest.mark.asyncio
async def test_a_member_of_another_room_is_refused(app_client, fresh_room, fresh_public_room):
    member = await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    att_id = await _upload(app_client, fresh_room["room_id"], member["access_token"])
    outsider = await _claim(app_client, fresh_public_room["room_id"], fresh_public_room["moderator_invite"])

    r = await app_client.get(
        f"/v1/attachments/{att_id}", headers={"Authorization": f"Bearer {outsider['access_token']}"},
    )
    # The same answer as for an id that does not exist, so a member of one room
    # cannot test which ids exist in another.
    assert r.status_code == 404, r.text
    assert CONTENT not in r.content


@pytest.mark.asyncio
async def test_an_ejected_member_loses_access(app_client, fresh_room):
    member = await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    att_id = await _upload(app_client, fresh_room["room_id"], member["access_token"])

    db = Database(DSN)
    await db.connect()
    try:
        await ParticipantRepo(db).set_affiliation(fresh_room["room_id"], member["pid"], "ejected")
    finally:
        await db.close()

    r = await app_client.get(
        f"/v1/attachments/{att_id}", headers={"Authorization": f"Bearer {member['access_token']}"},
    )
    assert r.status_code == 403, r.text
    assert CONTENT not in r.content


# ---- what a client is handed, and for how long -----------------------------


class _FakeS3Store:
    """Stands in for S3AttachmentStorage: records presigns, stores nothing remote."""

    presign_ttl_seconds = 300

    def __init__(self, local_dir) -> None:
        from agentstorming_server.services.attachments import AttachmentInfo
        self._info = AttachmentInfo
        self.presigned: list[str] = []

    async def put(self, *, room_id, sender_pid, filename, data, content_type):
        import hashlib
        import uuid
        att_id = uuid.uuid4()
        return self._info(att_id=att_id, s3_key=f"{room_id}/{att_id}-{filename}", local_path=None,
                          content_type=content_type, size_bytes=len(data), filename=filename,
                          sha256_hex=hashlib.sha256(data).hexdigest())

    def presign_get(self, info) -> str:
        url = f"https://bucket.s3.example/{info.s3_key}?X-Amz-Expires={self.presign_ttl_seconds}"
        self.presigned.append(url)
        return url


@pytest.mark.asyncio
async def test_the_upload_hands_out_the_download_route_not_a_presigned_url(app_client, fresh_room, tmp_path):
    """On S3 the upload response, and so every attachment event, once carried a
    seven-day presigned URL that kept working after the holder was ejected."""
    app = app_client._transport.app
    fake = _FakeS3Store(tmp_path)
    app.state.attachments_store = fake
    member = await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/attachments",
        files={"file": ("numbers.txt", CONTENT, "text/plain")},
        headers={"Authorization": f"Bearer {member['access_token']}"},
    )
    assert r.status_code == 200, r.text
    att_id = r.json()["id"]
    assert r.json()["url"] == f"http://testserver/v1/attachments/{att_id}"
    assert fake.presigned == [], "nothing may be presigned before a download is asked for"

    auth = {"Authorization": f"Bearer {member['access_token']}"}
    r = await app_client.get(f"/v1/attachments/{att_id}", headers=auth)
    assert r.status_code == 307, r.text
    assert r.headers["location"] == fake.presigned[-1]
    assert "private" in r.headers["cache-control"] and "no-store" in r.headers["cache-control"]

    r = await app_client.post("/v1/attachments/refresh", json={"att_id": att_id}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["url"] == fake.presigned[-1] and r.json()["expires_in_seconds"] == 300

    # Once ejected, no new presigned URL: the window is the TTL of one already held.
    db = Database(DSN)
    await db.connect()
    try:
        await ParticipantRepo(db).set_affiliation(fresh_room["room_id"], member["pid"], "ejected")
    finally:
        await db.close()
    issued = len(fake.presigned)
    assert (await app_client.get(f"/v1/attachments/{att_id}", headers=auth)).status_code == 403
    r = await app_client.post("/v1/attachments/refresh", json={"att_id": att_id}, headers=auth)
    assert r.status_code == 403
    assert len(fake.presigned) == issued


@pytest.mark.asyncio
async def test_local_downloads_are_not_cacheable_and_refresh_returns_the_route(app_client, fresh_room):
    member = await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    att_id = await _upload(app_client, fresh_room["room_id"], member["access_token"])
    auth = {"Authorization": f"Bearer {member['access_token']}"}
    r = await app_client.get(f"/v1/attachments/{att_id}", headers=auth)
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    r = await app_client.post("/v1/attachments/refresh", json={"att_id": att_id}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["url"].endswith(f"/v1/attachments/{att_id}") and r.json()["expires_in_seconds"] is None


@pytest.mark.asyncio
async def test_refresh_without_a_bearer_learns_nothing_about_the_id(app_client, fresh_room):
    import uuid
    member = await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    real = await _upload(app_client, fresh_room["room_id"], member["access_token"])
    for att_id in (real, str(uuid.uuid4())):
        r = await app_client.post("/v1/attachments/refresh", json={"att_id": att_id})
        assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_uploads_are_refused_when_the_room_disables_attachments(app_client, fresh_room):
    """``attachments.enabled`` was stored and never consulted."""
    from agentstorming_server.domain.room import RoomConfig
    from agentstorming_server.repo.rooms import RoomRepo
    member = await _claim(app_client, fresh_room["room_id"], fresh_room["participant_invite"])
    db = Database(DSN)
    await db.connect()
    try:
        rooms = RoomRepo(db)
        room = await rooms.get(fresh_room["room_id"])
        cfg = room.config.to_json()
        cfg["attachments"]["enabled"] = False
        await rooms.update_config(room.id, RoomConfig.from_json(cfg))
    finally:
        await db.close()
    r = await app_client.post(
        f"/v1/rooms/{fresh_room['room_id']}/attachments",
        files={"file": ("numbers.txt", CONTENT, "text/plain")},
        headers={"Authorization": f"Bearer {member['access_token']}"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "org.agentstorming.err.attachments_disabled"


def test_the_s3_store_presigns_for_the_configured_ttl(monkeypatch):
    """Offline: botocore signs locally, so a dummy credential is enough."""
    from urllib.parse import parse_qs, urlparse

    from agentstorming_server.services.attachments import S3AttachmentStorage
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    store = S3AttachmentStorage("bucket", "us-east-1")
    info = type("I", (), {"s3_key": "room/abc-file.txt"})()
    qs = parse_qs(urlparse(store.presign_get(info)).query)
    assert qs["X-Amz-Expires"] == ["300"]
    store = S3AttachmentStorage("bucket", "us-east-1", presign_ttl_seconds=60)
    assert parse_qs(urlparse(store.presign_get(info)).query)["X-Amz-Expires"] == ["60"]
    for bad in (0, -1, 604_801):
        with pytest.raises(ValueError):
            S3AttachmentStorage("bucket", "us-east-1", presign_ttl_seconds=bad)
