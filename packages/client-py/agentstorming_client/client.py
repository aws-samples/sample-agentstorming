# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""StormClient — public high-level API."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .buffer import BufferManager
from .config import ClientConfig
from .errors import StormError
from .http_transport import Transport
from .sse import SSEWorker
from .metadata import MetadataStore
from .signing import (
    b64url,
    b64url_decode,
    canonicalise,
    canonicalise_any,
    generate_keypair,
    sign_blob,
    sign_envelope,
    verify_envelope,
)
from .vault import Vault

log = logging.getLogger(__name__)


class StormClient:
    """Public SDK entry point."""

    def __init__(self, config: ClientConfig, *, vault: Vault | None = None) -> None:
        self.config = config
        self.vault = vault or Vault(
            path=(config.vault_dir / "vault.json") if config.vault_dir else None
        )
        self.transport = Transport(
            config.base_url,
            verify_tls=config.verify_tls,
            user_agent=config.user_agent,
        )
        self.buffer = BufferManager(capacity=config.buffer_capacity)
        self.metadata = MetadataStore()
        self._stream = SSEWorker(self)
        self._hooks: dict[str, list[Callable[[dict[str, Any]], Awaitable[None]]]] = {}
        self._started = False
        self._priv: bytes | None = None
        self._pub: bytes | None = None
        self._pid: str | None = None

    # ---- lifecycle ----------------------------------------------------

    async def __aenter__(self) -> "StormClient":
        # If tokens + pid already on file, start up; caller must redeem_invite manually if fresh.
        if self.vault.data.access_token and self.vault.data.pid:
            await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    @property
    def pid(self) -> str | None:
        return self.vault.data.pid

    async def start(self) -> None:
        if self._started:
            return
        self._priv, self._pub = self.vault.ensure_keypair()
        self._pid = self.vault.data.pid
        self._started = True
        if self.vault.data.access_token:
            await self._stream.start()

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        await self._stream.stop()
        await self.transport.aclose()

    # ---- claim --------------------------------------------------------

    async def redeem_invite(self, invite_token: str, *, kind: str = "participant") -> dict[str, Any]:
        priv, pub = self.vault.ensure_keypair()
        endpoint_map = {
            "participant": f"/v1/rooms/{self.config.room_id}/claim",
            "moderator": f"/v1/rooms/{self.config.room_id}/claim_moderator",
            "owner": f"/v1/rooms/{self.config.room_id}/claim_owner",
        }
        path = endpoint_map.get(kind)
        if not path:
            raise ValueError(f"unknown invite kind: {kind}")
        body = {"invite_token": invite_token, "pubkey": b64url(pub)}
        resp = await self.transport.post_json(path, body)
        self.vault.store_pid(resp["pid"])
        self.vault.store_tokens(
            resp["access_token"], resp["access_expires_at"],
            resp["refresh_token"], resp["refresh_expires_at"],
        )
        # Apply the snapshot to our local metadata store.
        await self.metadata.apply(resp["snapshot"])
        snap_payload = resp["snapshot"].get("payload", {}) or {}
        if snap_payload.get("server_pubkey"):
            self.vault.store_server_pubkey(snap_payload["server_pubkey"])
        # Start long-poll now that we have creds.
        if not self._started:
            await self.start()
        else:
            # start() is idempotent for long poll only if not running.
            if self._stream._task is None:
                await self._stream.start()
        return resp

    async def refresh_tokens(self) -> None:
        rt = self.vault.data.refresh_token
        if not rt:
            raise StormError("no refresh token")
        resp = await self.transport.post_json("/v1/tokens/refresh", {"refresh_token": rt})
        self.vault.store_tokens(
            resp["access_token"], resp["access_expires_at"],
            resp["refresh_token"], resp["refresh_expires_at"],
        )

    # ---- signing ------------------------------------------------------

    def _kid(self) -> str:
        if self._pub is None:
            raise StormError("start() first")
        return hashlib.sha256(self._pub).hexdigest()[:16]

    def _build_envelope(self, type_: str, payload: dict[str, Any], *,
                        mentions: list[str] | None = None,
                        reply_to: int | None = None) -> dict[str, Any]:
        if self._pid is None:
            self._pid = self.vault.data.pid
        if self._pid is None:
            raise StormError("not authenticated — redeem an invite first")
        ts = datetime.now(timezone.utc).isoformat()
        env = {
            "id": str(uuid.uuid4()),
            "type": type_,
            "room_id": self.config.room_id,
            "sender": self._pid,
            "ts_sender": ts,
            "iat": ts,
            "nonce": b64url(secrets.token_bytes(16)),
            "reply_to": reply_to,
            "mentions": mentions or [],
            "payload": payload,
        }
        val = sign_envelope(env, self._priv)
        env["sig"] = {"alg": "ed25519", "kid": self._kid(), "val": val}
        return env

    async def verify_incoming(self, event: dict[str, Any]) -> bool:
        sender = event.get("sender")
        if sender == "system":
            pk_b64 = await self.metadata.server_pubkey_b64() or self.vault.data.server_pubkey
            if not pk_b64:
                # We don't know the server pubkey yet; accept on trust-on-first-use (the snapshot carries it).
                return True
            return verify_envelope(event, b64url_decode(pk_b64))
        if not sender:
            return False
        pk_b64 = await self.metadata.peer_pubkey_b64(sender)
        if not pk_b64:
            # Haven't seen this peer yet; accept with a warning. A strict deployment may re-request the snapshot.
            return True
        return verify_envelope(event, b64url_decode(pk_b64))

    # ---- posting ------------------------------------------------------

    async def post_event(self, type_: str, payload: dict[str, Any], *,
                         mentions: list[str] | None = None,
                         reply_to: int | None = None,
                         idempotency_key: str | None = None) -> dict[str, Any]:
        env = self._build_envelope(type_, payload, mentions=mentions, reply_to=reply_to)
        ikey = idempotency_key or str(uuid.uuid4())
        resp = await self.transport.post_json(
            f"/v1/rooms/{self.config.room_id}/events",
            env,
            token=self.vault.data.access_token,
            idempotency_key=ikey,
        )
        return resp

    async def post_message(self, text: str, *, mentions: list[str] | None = None,
                            reply_to: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text}
        g = await self.metadata.active_grant_for(self.vault.data.pid)
        if g:
            payload["grant_id"] = g["grant_id"]
        return await self.post_event("org.agentstorming.message", payload,
                                     mentions=mentions, reply_to=reply_to)

    async def raise_hand(self, hint: str = "") -> dict[str, Any]:
        return await self.post_event("org.agentstorming.hand_raised", {"hint": hint})

    async def post_approval_request(
        self, *, approval_id: str, tool: str, target_pid: str,
        estimated_cost_usd: float, reason: str = "",
    ) -> dict[str, Any]:
        """Whisper-class request for owner approval of a tool call.

        The broker raises 'approval_required' with an approval_id when
        a tool call would exceed its ask_owner_at_usd threshold; the
        agent fires this whisper to surface the request to the owner.
        Server-side visibility filter ensures only sender, target_pid,
        and the room owner can see it.
        """
        return await self.post_event(
            "org.agentstorming.approval_request",
            {
                "approval_id": approval_id,
                "tool": tool,
                "target_pid": target_pid,
                "estimated_cost_usd": float(estimated_cost_usd),
                "reason": reason,
            },
        )

    async def lower_hand(self, hand_id: str) -> dict[str, Any]:
        return await self.post_event("org.agentstorming.hand_lowered", {"hand_id": hand_id})

    async def rotate_key(self) -> dict[str, Any]:
        """Generate a fresh keypair and broadcast an ``org.agentstorming.key_rotation``
        event co-signed by the new key. The vault swaps to the new keypair only
        after the server accepts the rotation, so a failed rotation leaves the
        old key in place.
        """
        if self._priv is None or self._pid is None:
            raise StormError("start() first")
        new_priv, new_pub = generate_keypair()
        ts = datetime.now(timezone.utc).isoformat()
        env: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "type": "org.agentstorming.key_rotation",
            "room_id": self.config.room_id,
            "sender": self._pid,
            "ts_sender": ts,
            "iat": ts,
            "nonce": b64url(secrets.token_bytes(16)),
            "reply_to": None,
            "mentions": [],
            "payload": {"new_pubkey": b64url(new_pub)},
        }
        # Co-sign with the new key over the canonical envelope minus
        # {seq, ts_server, sig, payload.new_sig}.
        co_env = {k: v for k, v in env.items() if k not in ("seq", "ts_server", "sig")}
        co_env["payload"] = {k: v for k, v in (co_env.get("payload") or {}).items() if k != "new_sig"}
        env["payload"]["new_sig"] = sign_blob(canonicalise_any(co_env), new_priv)
        # Old key signs the full envelope.
        env["sig"] = {"alg": "ed25519", "kid": self._kid(), "val": sign_envelope(env, self._priv)}
        resp = await self.transport.post_json(
            f"/v1/rooms/{self.config.room_id}/events",
            env,
            token=self.vault.data.access_token,
            idempotency_key=str(uuid.uuid4()),
        )
        self.vault.rotate_keypair(new_priv, new_pub)
        self._priv, self._pub = new_priv, new_pub
        return resp

    async def speak_under_grant(self, text: str, *, grant_id: str | None = None,
                                 mentions: list[str] | None = None) -> dict[str, Any]:
        if grant_id is None:
            g = await self.metadata.active_grant_for(self.vault.data.pid)
            if not g:
                raise StormError("no active grant")
            grant_id = g["grant_id"]
        return await self.post_event("org.agentstorming.message",
                                     {"text": text, "grant_id": grant_id},
                                     mentions=mentions)

    # ---- buffer helpers ----------------------------------------------

    async def get_buffer(self) -> list[dict[str, Any]]:
        return await self.buffer.snapshot()

    async def drain_buffer(self) -> list[dict[str, Any]]:
        return await self.buffer.drain()

    async def get_room_state(self) -> dict[str, Any]:
        return await self.metadata.snapshot()

    async def request_snapshot(self) -> dict[str, Any]:
        resp = await self.transport.get_json(
            f"/v1/rooms/{self.config.room_id}/snapshot",
            token=self.vault.data.access_token,
        )
        await self.metadata.apply(resp)
        return resp

    # ---- history ------------------------------------------------------

    async def history(
        self,
        *,
        from_ts: str | None = None,
        to_ts: str | None = None,
        types: list[str] | None = None,
        limit_per_page: int = 500,
    ):
        """Async generator yielding every message event in a time window.

        Transparently follows the server's ``continue_from`` cursor until
        ``has_more`` is false. Pass ISO-8601 timestamps for the range;
        omit both for "whole history."
        """
        cursor: int | None = None
        while True:
            params: dict[str, Any] = {"limit": limit_per_page}
            if from_ts is not None:
                params["from_ts"] = from_ts
            if to_ts is not None:
                params["to_ts"] = to_ts
            if types:
                params["types"] = ",".join(types)
            if cursor is not None:
                params["cursor"] = cursor
            resp = await self.transport.get_json(
                f"/v1/rooms/{self.config.room_id}/messages",
                token=self.vault.data.access_token,
                params=params,
            )
            for ev in resp.get("events", []):
                yield ev
            if not resp.get("has_more"):
                return
            cursor = resp.get("continue_from")
            if cursor is None:
                return

    # ---- discovery ----------------------------------------------------

    async def list_public_rooms(self) -> list[dict[str, Any]]:
        """List every room with visibility=public on the server (unauthenticated)."""
        resp = await self.transport.get_json("/v1/rooms", token=None)
        return resp.get("rooms", [])

    # ---- moderator ----------------------------------------------------

    async def grant_turn(self, target_pid: str, *, hand_id: str | None = None, ttl_seconds: int | None = None) -> dict[str, Any]:
        body = {"target_pid": target_pid}
        if hand_id:
            body["hand_id"] = hand_id
        if ttl_seconds:
            body["ttl_seconds"] = ttl_seconds
        return await self.transport.post_json(
            f"/v1/rooms/{self.config.room_id}/grants", body,
            token=self.vault.data.access_token,
        )

    async def mute(self, target_pid: str, duration_seconds: int) -> dict[str, Any]:
        return await self.transport.post_json(
            f"/v1/rooms/{self.config.room_id}/moderation/mute",
            {"target_pid": target_pid, "duration_seconds": duration_seconds},
            token=self.vault.data.access_token,
        )

    async def eject(self, target_pid: str) -> dict[str, Any]:
        return await self.transport.post_json(
            f"/v1/rooms/{self.config.room_id}/moderation/eject",
            {"target_pid": target_pid},
            token=self.vault.data.access_token,
        )

    async def pen(self, target_pid: str, duration_seconds: int) -> dict[str, Any]:
        return await self.transport.post_json(
            f"/v1/rooms/{self.config.room_id}/moderation/pen",
            {"target_pid": target_pid, "duration_seconds": duration_seconds},
            token=self.vault.data.access_token,
        )

    async def assign_deputy(self, target_pid: str, rank: int) -> dict[str, Any]:
        return await self.transport.post_json(
            f"/v1/rooms/{self.config.room_id}/deputies",
            {"target_pid": target_pid, "rank": rank},
            token=self.vault.data.access_token,
        )

    async def set_summary(self, text: str) -> dict[str, Any]:
        return await self.transport.put_json(
            f"/v1/rooms/{self.config.room_id}/summary",
            {"text": text},
            token=self.vault.data.access_token,
        )

    # ---- attachments --------------------------------------------------

    async def upload_attachment(self, path: Path) -> dict[str, Any]:
        path = Path(path)
        with path.open("rb") as f:
            files = {"file": (path.name, f.read(), "application/octet-stream")}
        return await self.transport.post_multipart(
            f"/v1/rooms/{self.config.room_id}/attachments",
            files=files, token=self.vault.data.access_token,
        )

    async def post_attachment(self, path: Path, text: str = "") -> dict[str, Any]:
        info = await self.upload_attachment(path)
        payload: dict[str, Any] = {
            "text": text,
            "attachments": [{
                "id": info["id"],
                "url": info["url"],
                "s3_key": info.get("s3_key"),
                "content_type": info["content_type"],
                "size_bytes": info["size_bytes"],
                "filename": info["filename"],
                "sha256": info["sha256"],
            }],
        }
        g = await self.metadata.active_grant_for(self.vault.data.pid)
        if g:
            payload["grant_id"] = g["grant_id"]
        return await self.post_event("org.agentstorming.attachment", payload)

    # ---- history ------------------------------------------------------

    async def get_history(self, from_seq: int = 0, to_seq: int | None = None,
                           limit: int = 1000, types: list[str] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"from": from_seq, "limit": limit}
        if to_seq is not None:
            params["to"] = to_seq
        if types:
            params["types"] = ",".join(types)
        return await self.transport.get_json(
            f"/v1/rooms/{self.config.room_id}/messages",
            token=self.vault.data.access_token,
            params=params,
        )

    # ---- hooks --------------------------------------------------------

    def on(self, event_type: str):
        def decorator(fn):
            self._hooks.setdefault(event_type, []).append(fn)
            return fn
        return decorator

    async def dispatch_hooks(self, event: dict[str, Any]) -> None:
        for handler in self._hooks.get(event.get("type", ""), []):
            try:
                await handler(event)
            except Exception:
                log.exception("hook for %s failed", event.get("type"))

    async def leave(self, reason: str = "done") -> None:
        # v1: we mark as left via a disconnect; server's grace period will notice.
        # If we had a /leave endpoint we'd call it; for now simply stop.
        await self.stop()
