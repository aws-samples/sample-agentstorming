# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""SDK error mapping, attachment download redirects, and upload content types."""

from __future__ import annotations

import httpx
import pytest

from agentstorming_client.errors import (
    AuthError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    StormError,
    error_from_body,
)
from agentstorming_client.http_transport import Transport


@pytest.mark.parametrize("body,cls,code", [
    ({"detail": {"code": "org.agentstorming.err.auth", "message": "missing bearer token"}}, AuthError, None),
    ({"detail": {"code": "org.agentstorming.err.pen_active"}}, ForbiddenError, None),
    ({"detail": {"code": "org.agentstorming.err.not_found"}}, NotFoundError, None),
    ({"detail": {"code": "org.agentstorming.err.rate_limited",
                 "details": {"retry_after_seconds": 2.0}}}, RateLimitError, None),
    ({"detail": [{"loc": ["body", "kind"], "msg": "bad"}]}, StormError, "org.agentstorming.err.validation"),
    ({"code": "org.agentstorming.err.auth"}, AuthError, None),
])
def test_server_errors_map_to_their_types(body, cls, code):
    """The server answers {"detail": {"code": ...}}; reading only a top-level
    code made every one of them a generic internal error."""
    err = error_from_body(body)
    assert type(err) is cls
    if code:
        assert err.message.startswith("[") and cls.code == "org.agentstorming.err.internal"
    if cls is RateLimitError:
        assert err.retry_after_seconds == 2.0


#: A stand-in bearer. Passed by name, not as a literal keyword argument, which
#: Bandit B106 reads as a hard-coded password.
BEARER = "test-bearer-value"


def _transport(handler) -> Transport:
    t = Transport("https://storm.example", user_agent="test")
    t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://storm.example")
    return t


@pytest.mark.asyncio
async def test_get_bytes_follows_an_https_redirect_without_the_bearer():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "storm.example":
            return httpx.Response(307, headers={"location": "https://bucket.s3.example/k?X-Amz-Expires=300"})
        return httpx.Response(200, content=b"bytes")

    r = await _transport(handler).get_bytes("/v1/attachments/x", token=BEARER)
    assert r.content == b"bytes"
    assert seen[0].headers["authorization"] == f"Bearer {BEARER}"
    assert "authorization" not in seen[1].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["http://attacker.example/x", "ftp://storm.example/x"])
async def test_get_bytes_refuses_a_redirect_to_cleartext_elsewhere(location):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "storm.example" and request.url.scheme == "https":
            return httpx.Response(302, headers={"location": location})
        raise AssertionError(f"followed a redirect to {request.url}")

    with pytest.raises(StormError, match="refusing to follow"):
        await _transport(handler).get_bytes("/v1/attachments/x", token=BEARER)


@pytest.mark.asyncio
async def test_uploads_declare_the_file_type(tmp_path, monkeypatch):
    from agentstorming_client import ClientConfig, StormClient
    sent = {}

    async def capture(path, files, *, token=None):
        sent.update(files)
        return {}

    client = StormClient(ClientConfig(base_url="https://storm.example", room_id="r", vault_dir=tmp_path))
    monkeypatch.setattr(client.transport, "post_multipart", capture)
    note = tmp_path / "findings.md"
    note.write_text("# findings")
    await client.upload_attachment(note)
    assert sent["file"][0] == "findings.md" and sent["file"][2] == "text/markdown"
    blob = tmp_path / "blob.unknownext"
    blob.write_bytes(b"\x00\x01")
    await client.upload_attachment(blob)
    assert sent["file"][2] == "application/octet-stream"


@pytest.mark.parametrize("code", [
    "org.agentstorming.err.nonce_invalid", "org.agentstorming.err.room_full",
    "org.agentstorming.err.key_revoked", "org.agentstorming.err.bad_kind",
])
def test_the_exception_carries_the_servers_code(code):
    """The class only groups codes; .code used to read the class constant, so
    every unmapped code came out "internal" and key_revoked as pen_active."""
    assert error_from_body({"detail": {"code": code, "message": "m"}}).code == code


def test_a_string_detail_keeps_its_message():
    err = error_from_body({"detail": "Method Not Allowed"})
    assert err.message == "Method Not Allowed"


@pytest.mark.asyncio
async def test_errors_carry_the_http_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": {"code": "org.agentstorming.err.forbidden",
                                                    "message": "moderator required"}})

    with pytest.raises(StormError) as err:
        await _transport(handler).post_json("/v1/rooms/r/moderation/mute", {})
    assert err.value.status == 403 and err.value.code == "org.agentstorming.err.forbidden"


def test_owner_timestamps_follow_the_clock_back(monkeypatch):
    """Timestamps are bumped past the last one so concurrent nonce requests
    differ, but after the clock steps back an hour they must follow it, or every
    owner request stays outside the server's ±5 minute window for that hour."""
    from datetime import UTC, datetime, timedelta

    from agentstorming_client import owner

    clock = [datetime(2026, 11, 12, 12, 0, tzinfo=UTC)]

    class _Clock:
        @staticmethod
        def now(tz=None):
            return clock[0]

    monkeypatch.setattr(owner, "datetime", _Clock)
    monkeypatch.setattr(owner, "_last_ts", None)
    first, second = owner.unique_timestamp(), owner.unique_timestamp()
    assert datetime.fromisoformat(second) - datetime.fromisoformat(first) == timedelta(microseconds=1)
    clock[0] -= timedelta(hours=1)
    assert owner.unique_timestamp() == clock[0].isoformat()


@pytest.mark.asyncio
async def test_a_malformed_sse_event_is_logged_without_its_content(monkeypatch, caplog):
    """The data is room traffic; logging its first 100 characters wrote message
    text and whispers to the application log."""
    import asyncio
    import contextlib
    import logging
    from types import SimpleNamespace

    from agentstorming_client import sse as sse_mod

    whisper = "whispered plan: meet at the north gate"
    worker = None

    class _Source:
        response = SimpleNamespace(status_code=200, raise_for_status=lambda: None)

        async def aiter_sse(self):
            yield SimpleNamespace(event="event", id="7", data=whisper + " {not json")
            worker._stop.set()
            yield SimpleNamespace(event="event", id="8", data="{}")

    @contextlib.asynccontextmanager
    async def fake_connect(*args, **kwargs):
        yield _Source()

    client = SimpleNamespace(
        config=SimpleNamespace(base_url="https://storm.example", room_id="r", verify_tls=True,
                               verify_signatures=False),
        vault=SimpleNamespace(data=SimpleNamespace(since_cursor=-1, access_token=BEARER,
                                                   access_expires_at=None)),
    )
    worker = sse_mod.SSEWorker(client)
    monkeypatch.setattr(sse_mod, "aconnect_sse", fake_connect)

    async def no_refresh(*a, **k):
        return None

    monkeypatch.setattr(worker, "_refresh_if_near_expiry", no_refresh)
    monkeypatch.setattr(worker, "_mid_stream_refresher", lambda: asyncio.sleep(3600))
    with caplog.at_level(logging.WARNING, logger=sse_mod.log.name):
        await worker._connect_once()
    assert "id=7" in caplog.text and "not JSON" in caplog.text
    assert "north gate" not in caplog.text and "whispered" not in caplog.text
