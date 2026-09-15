# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""asyncio Unix-domain-socket JSON-RPC broker server.

Wire format: 4-byte big-endian length prefix + UTF-8 JSON-RPC 2.0
payload. Bidirectional, single-request-per-connection (we don't need
streaming for v0).

Methods:
  - prepare_headers(provider, target, method, url, body_b64?) → {headers}
  - prepare_credentials(provider, target, session_vars?) → {credentials}
  - audit_tail(n) → list[entry]   (admin only)

Authorization:
  - SO_PEERCRED gates which uid can connect
  - capability policy gates which provider+target each persona can call
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import struct
import time
import uuid
from pathlib import Path
from typing import Any

from datetime import datetime

from .audit import AuditChain, AuditEntry, args_hash
from .dlp import DLPScanner
from .peercred import PeerCred, get_peer_cred
from .policy import PolicyEngine
from .providers import Provider, ProviderError
from .roommode import ModeVerificationError, verify_mode_envelope

log = logging.getLogger("storm_broker")


_FRAME_HDR = struct.Struct(">I")
_MAX_FRAME = 4 * 1024 * 1024  # 4 MiB hard cap


class BrokerServer:
    def __init__(
        self,
        socket_path: str,
        providers: dict[str, Provider],
        policy_for_uid: dict[int, PolicyEngine],
        audit_chain: AuditChain,
        allowed_uids: set[int],
        dlp: DLPScanner | None = None,
        room_id: str | None = None,
        server_pubkey: bytes | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.providers = providers
        self.policy_for_uid = policy_for_uid
        self.audit = audit_chain
        self.allowed_uids = allowed_uids
        self.dlp = dlp or DLPScanner()
        self._server: asyncio.Server | None = None
        # Stage-13 §Planning mode. The room this broker serves, and the
        # room's server pubkey, so a relayed mode_promoted envelope can be
        # verified rather than trusted. Without both, set_room_mode refuses
        # every request and the configured mode stands for the session.
        self.room_id = room_id
        self.server_pubkey = server_pubkey
        self._last_mode_iat: datetime | None = None
        # HITL pending-approvals registry. Keyed by approval_id.
        # Owner-side process (room-owner CLI / storm-server admin)
        # records grants/denies via the `record_approval` method; the
        # next call by the agent for the same (provider, target,
        # estimated_cost) sees the decision.
        #
        # NB: this registry is in-memory only. Restarting the broker
        # drops all pending approvals — the agent will simply re-emit
        # an approval_request whisper on its next attempt and the owner
        # will see a fresh approval_id. This is acceptable because
        # approvals are short-lived (the call costs the broker is
        # gating are cents-to-dollars, not session-duration commitments)
        # and the agent's whisper is durable on the storm-server side.
        # If durability becomes desirable, persist to the same SQLite
        # the audit log uses.
        self._approvals: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        # Clean up stale socket.
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=self.socket_path,
        )
        # 0o660, not 0o600: the agent runs as a DIFFERENT uid in the same
        # group (Stage-13 broker invariant), so it must be able to connect
        # while everyone outside the group cannot. Peer identity is then
        # checked per connection via SO_PEERCRED against allowed_uids.
        os.chmod(self.socket_path, 0o660)  # nosec B103 - group access is required by design
        log.info("storm-broker listening on %s", self.socket_path)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        sock = writer.get_extra_info("socket")
        try:
            cred = get_peer_cred(sock)
        except OSError as e:
            log.warning("peer-cred lookup failed: %s", e)
            writer.close()
            return
        if cred.uid not in self.allowed_uids:
            log.warning("rejecting peer uid=%s pid=%s", cred.uid, cred.pid)
            await self._reply_error(writer, None, -32000, "unauthorised_peer",
                                    {"uid": cred.uid, "pid": cred.pid})
            writer.close()
            return
        try:
            while True:
                req = await self._read_frame(reader)
                if req is None:
                    break
                resp = await self._dispatch(cred, req, writer)
                await self._write_frame(writer, resp)
                # Side-channel fd-passing: dispatch may have stashed a
                # pending fd on the writer's transport extra dict for
                # us to send AFTER the JSON reply has flushed.
                pending_fd = getattr(writer, "_pending_fd", None)
                if pending_fd is not None:
                    await writer.drain()
                    # asyncio's TransportSocket wrapper doesn't expose
                    # sendmsg; reach the underlying real socket via _sock.
                    real_sock = getattr(sock, "_sock", sock)
                    try:
                        socket.send_fds(real_sock, [b"F"], [pending_fd])
                    finally:
                        try:
                            os.close(pending_fd)
                        except OSError:
                            pass
                        writer._pending_fd = None
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as e:  # pragma: no cover - defensive
            log.exception("session error: %s", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_frame(self, reader: asyncio.StreamReader) -> dict | None:
        try:
            hdr = await reader.readexactly(4)
        except asyncio.IncompleteReadError:
            return None
        (n,) = _FRAME_HDR.unpack(hdr)
        if n > _MAX_FRAME:
            raise ValueError(f"frame too large: {n}")
        body = await reader.readexactly(n)
        return json.loads(body.decode("utf-8"))

    async def _write_frame(self, writer: asyncio.StreamWriter, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        writer.write(_FRAME_HDR.pack(len(body)) + body)
        await writer.drain()

    async def _reply_error(
        self, writer: asyncio.StreamWriter, rid: Any, code: int, msg: str,
        data: dict | None = None,
    ) -> None:
        await self._write_frame(writer, {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": code, "message": msg, "data": data or {}},
        })

    async def _dispatch(
        self, peer: PeerCred, req: dict,
        writer: asyncio.StreamWriter | None = None,
    ) -> dict:
        method = req.get("method", "")
        params = req.get("params", {}) or {}
        rid = req.get("id")
        try:
            if method == "prepare_headers":
                result = await self._prepare_headers(peer, params)
            elif method == "prepare_credentials":
                result = await self._prepare_credentials(peer, params)
            elif method == "record_approval":
                result = self._record_approval(peer, params)
            elif method == "sign":
                result = await self._sign(peer, params)
            elif method == "open_db_connection":
                result = self._open_db_connection(peer, params, writer)
            elif method == "set_room_mode":
                result = self._set_room_mode(peer, params)
            elif method == "get_room_mode":
                result = self._get_room_mode(peer)
            elif method == "ping":
                result = {"ok": True, "uid": peer.uid, "pid": peer.pid, "ts": time.time()}
            else:
                return {
                    "jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": "method_not_found",
                              "data": {"method": method}},
                }
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except PolicyDenied as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32030, "message": e.reason, "data": e.data}}
        except ProviderError as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32035, "message": "provider_error",
                              "data": {"detail": str(e)}}}
        except Exception as e:
            log.exception("dispatch error")
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": "internal", "data": {"detail": str(e)}}}

    async def _prepare_headers(self, peer: PeerCred, params: dict) -> dict:
        provider_name = params["provider"]
        target = params.get("target", "")
        http_method = params.get("method", "GET")
        url = params.get("url", "")
        body_b64 = params.get("body_b64")
        body = base64.b64decode(body_b64) if body_b64 else None
        provider = self.providers.get(provider_name)
        if provider is None:
            raise PolicyDenied("provider_unknown", {"provider": provider_name})
        # Capability gate
        engine = self.policy_for_uid.get(peer.uid)
        if engine is None:
            raise PolicyDenied("no_policy_for_uid", {"uid": peer.uid})
        decision = engine.evaluate(
            tool=f"{provider_name}.request",
            args={"target": target, "url": url, "method": http_method},
            trust=params.get("trust", "user"),
            estimated_cost_usd=float(params.get("estimated_cost_usd", 0.0)),
        )
        if not decision.allow:
            # HITL path: surface a structured approval_required error so
            # the agent can emit org.agentstorming.approval_request and
            # wait. The broker holds a pending entry keyed by approval_id
            # so an out-of-band record_approval call can flip it.
            if decision.approval_required:
                approval_id = params.get("approval_id") or str(uuid.uuid4())
                granted = self._approvals.get(approval_id)
                if granted and granted.get("decision") == "granted":
                    self._record(peer, params, "allow", "approval_granted")
                else:
                    self._approvals[approval_id] = {
                        "decision": "pending",
                        "uid": peer.uid,
                        "provider": provider_name,
                        "target": target,
                        "estimated_cost_usd": decision.estimated_cost,
                        "ts": time.time(),
                    }
                    self._record(peer, params, "deny", "approval_required")
                    raise PolicyDenied("approval_required", {
                        "approval_id": approval_id,
                        "estimated_cost_usd": decision.estimated_cost,
                    })
            else:
                self._record(peer, params, "deny", decision.reason)
                raise PolicyDenied(decision.reason, {})
        # Provider call
        result = provider.prepare_headers(target, http_method, url, body)
        self._record(peer, params, "allow", "ok")
        return {"headers": result.headers, "upstream_url": result.upstream_url}

    def _get_room_mode(self, peer: PeerCred) -> dict:
        engine = self.policy_for_uid.get(peer.uid)
        if engine is None:
            raise PolicyDenied("no_policy_for_uid", {"uid": peer.uid})
        return {"room_id": self.room_id, "mode": engine.room_mode}

    def _set_room_mode(self, peer: PeerCred, params: dict) -> dict:
        """Apply a room-mode transition the storm server signed.

        The agent relays the canonical bytes of an
        ``org.agentstorming.mode_promoted`` envelope plus its signature; we
        verify against the room's server pubkey. The agent's own assertion
        carries no weight, which is the point — this is the one control that
        stands between a jailbroken persona and a live side effect.

        Applies to every policy engine this broker hosts: room mode is a
        property of the room, not of one persona's uid.
        """
        if self.room_id is None or self.server_pubkey is None:
            self._record(peer, params, "deny", "room_mode_unconfigured")
            raise PolicyDenied(
                "room_mode_unconfigured",
                {"detail": "broker has no room_id/server_pubkey to verify against"},
            )
        canonical_b64 = params.get("canonical_b64")
        signature = params.get("sig")
        if not canonical_b64 or not signature:
            raise PolicyDenied("bad_params", {"required": ["canonical_b64", "sig"]})
        try:
            canonical = base64.b64decode(canonical_b64)
        except Exception as e:
            raise PolicyDenied("bad_params", {"detail": f"canonical_b64: {e}"}) from e

        try:
            verified = verify_mode_envelope(
                canonical,
                signature,
                self.server_pubkey,
                expected_room_id=self.room_id,
                last_accepted_iat=self._last_mode_iat,
            )
        except ModeVerificationError as e:
            self._record(peer, params, "deny", "mode_verification_failed")
            raise PolicyDenied("mode_verification_failed", {"detail": str(e)}) from e

        for engine in self.policy_for_uid.values():
            engine.set_room_mode(verified.mode)
        self._last_mode_iat = verified.iat
        self._record(peer, params, "allow", f"room_mode={verified.mode}")
        log.info(
            "room %s mode set to %s (promoted_by=%s)",
            self.room_id, verified.mode, verified.promoted_by,
        )
        return {
            "room_id": self.room_id,
            "mode": verified.mode,
            "iat": verified.iat.isoformat(),
        }

    async def _prepare_credentials(self, peer: PeerCred, params: dict) -> dict:
        provider_name = params["provider"]
        target = params.get("target", "")
        provider = self.providers.get(provider_name)
        if provider is None:
            raise PolicyDenied("provider_unknown", {"provider": provider_name})
        engine = self.policy_for_uid.get(peer.uid)
        if engine is None:
            raise PolicyDenied("no_policy_for_uid", {"uid": peer.uid})
        decision = engine.evaluate(
            tool=f"{provider_name}.credentials",
            args={"target": target},
            trust=params.get("trust", "user"),
        )
        if not decision.allow:
            self._record(peer, params, "deny", decision.reason)
            raise PolicyDenied(decision.reason, {})
        session_vars = params.get("session_vars", {}) or {}
        result = provider.prepare_credentials(target, **session_vars) if session_vars \
                 else provider.prepare_credentials(target)
        self._record(peer, params, "allow", "ok")
        return {"credentials": result.credentials_json}

    def _open_db_connection(
        self, peer: PeerCred, params: dict,
        writer: asyncio.StreamWriter | None,
    ) -> dict:
        """Open a DB connection and stash the fd for SCM_RIGHTS handoff.

        The actual fd transmission happens AFTER this function returns
        and the JSON reply has been flushed (see _handle_client). The
        agent reads the JSON ACK first, then receives the fd via
        recv_fds on the same Unix socket.
        """
        provider_name = params["provider"]
        provider = self.providers.get(provider_name)
        if provider is None:
            raise PolicyDenied("provider_unknown", {"provider": provider_name})
        if not hasattr(provider, "open_connection"):
            raise PolicyDenied("provider_not_db_capable",
                               {"provider": provider_name})
        engine = self.policy_for_uid.get(peer.uid)
        if engine is None:
            raise PolicyDenied("no_policy_for_uid", {"uid": peer.uid})
        decision = engine.evaluate(
            tool=f"{provider_name}.open_db_connection",
            args={"database": params.get("database", "")},
            trust=params.get("trust", "user"),
        )
        if not decision.allow:
            self._record(peer, params, "deny", decision.reason)
            raise PolicyDenied(decision.reason, {})

        try:
            sock_obj = provider.open_connection()
        except ProviderError:
            raise
        except Exception as e:
            raise PolicyDenied("connection_failed", {"detail": str(e)}) from e

        # Detach the fd: we hand ownership to the agent. The Python
        # socket object should no longer close it on GC. We dup so the
        # provider's view stays open if needed (it doesn't here, but
        # the dup also gives us a clean fd to forward).
        fd = os.dup(sock_obj.fileno())
        sock_obj.close()

        if writer is not None:
            writer._pending_fd = fd  # type: ignore[attr-defined]

        self._record(peer, params, "allow", "ok")
        # Diagnostic info — never the password.
        return {
            "ok": True,
            "fd_pending": True,
            "diagnostics": provider.credentials_redacted()
            if hasattr(provider, "credentials_redacted") else {},
        }

    async def _sign(self, peer: PeerCred, params: dict) -> dict:
        """Sign-only key-handle access (KeyHandleProvider).

        Capability gate is the same shape as prepare_headers: the
        persona policy must contain a ``<provider>.sign`` rule that
        matches the kid being requested.
        """
        provider_name = params["provider"]
        provider = self.providers.get(provider_name)
        if provider is None:
            raise PolicyDenied("provider_unknown", {"provider": provider_name})
        # Late check: make sure the provider exposes sign().
        if not hasattr(provider, "sign"):
            raise PolicyDenied("provider_not_signing_capable",
                               {"provider": provider_name})
        engine = self.policy_for_uid.get(peer.uid)
        if engine is None:
            raise PolicyDenied("no_policy_for_uid", {"uid": peer.uid})
        decision = engine.evaluate(
            tool=f"{provider_name}.sign",
            args={"kid": getattr(provider, "kid", "")},
            trust=params.get("trust", "user"),
        )
        if not decision.allow:
            self._record(peer, params, "deny", decision.reason)
            raise PolicyDenied(decision.reason, {})
        payload_b64 = params.get("payload_b64")
        if not payload_b64:
            raise PolicyDenied("missing_payload", {})
        payload = base64.b64decode(payload_b64)
        try:
            sig = provider.sign(payload)
        except ProviderError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            raise ProviderError(f"sign failed: {e}") from e
        self._record(peer, params, "allow", "ok")
        return sig  # {alg, kid, sig_b64}

    def _record_approval(self, peer: PeerCred, params: dict) -> dict:
        """Owner-side method to flip a pending approval to granted/denied.

        ⚠ v0 limitation: any allowed-uid peer can call this. Production
        deployments should restrict via a separate admin uid in
        allowed_admin_uids (TODO).
        """
        approval_id = params.get("approval_id")
        decision = params.get("decision", "denied")
        if not approval_id or approval_id not in self._approvals:
            raise PolicyDenied("unknown_approval_id", {"approval_id": approval_id})
        if decision not in ("granted", "denied", "revoked"):
            raise PolicyDenied("invalid_decision", {"decision": decision})
        entry = self._approvals[approval_id]
        entry["decision"] = decision
        entry["resolved_by_uid"] = peer.uid
        entry["resolved_ts"] = time.time()
        return {"ok": True, "approval_id": approval_id, "decision": decision}

    def _record(self, peer: PeerCred, params: dict, decision: str, reason: str) -> None:
        try:
            entry = AuditEntry(
                event_id=params.get("event_id", str(uuid.uuid4())),
                persona_pid=params.get("persona_pid", f"uid:{peer.uid}"),
                room_id=params.get("room_id", ""),
                task_id=params.get("task_id", ""),
                tool=f"{params.get('provider', '?')}.{params.get('target', '?')}",
                args_hash=args_hash(params),
                decision=decision,
                reason=reason,
            )
            self.audit.append(entry)
        except Exception:  # pragma: no cover - audit never blocks request
            log.exception("audit append failed")


class PolicyDenied(Exception):
    def __init__(self, reason: str, data: dict) -> None:
        super().__init__(reason)
        self.reason = reason
        self.data = data
