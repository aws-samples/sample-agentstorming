# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Storm broker client.

A thin JSON-RPC-over-Unix-socket client that lets the agent ask the
broker to:
  - prepare_headers(provider, target, method, url, body) — for proxy mode
  - prepare_credentials(provider, target) — for SDK container-creds mode

The agent never reads credential files; it asks the broker.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import uuid
from dataclasses import dataclass
from typing import Any


_FRAME_HDR = struct.Struct(">I")


@dataclass
class BrokerError(Exception):
    code: int
    message: str
    data: dict[str, Any]

    def __str__(self) -> str:
        return f"BrokerError({self.code} {self.message}: {self.data})"


class BrokerClient:
    """Synchronous client. Asyncio version is straightforward but not needed for v0."""

    def __init__(self, socket_path: str = "/run/agentstorming/broker.sock", timeout: float = 30.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout
        self._lock = threading.Lock()

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        rid = str(uuid.uuid4())
        req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        body = json.dumps(req, separators=(",", ":")).encode("utf-8")
        with self._lock:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(self.socket_path)
                s.sendall(_FRAME_HDR.pack(len(body)) + body)
                hdr = self._recvall(s, 4)
                (n,) = _FRAME_HDR.unpack(hdr)
                resp = json.loads(self._recvall(s, n).decode("utf-8"))
        if "error" in resp:
            err = resp["error"]
            raise BrokerError(
                code=err.get("code", -1),
                message=err.get("message", "unknown"),
                data=err.get("data", {}),
            )
        return resp.get("result")

    @staticmethod
    def _recvall(s: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("broker closed connection mid-read")
            buf.extend(chunk)
        return bytes(buf)

    def ping(self) -> dict:
        return self._call("ping", {})

    def prepare_headers(
        self, provider: str, target: str = "", method: str = "GET", url: str = "",
        body: bytes | None = None, *, trust: str = "user", room_id: str = "",
        persona_pid: str = "", task_id: str = "", event_id: str = "",
    ) -> dict:
        import base64
        params: dict[str, Any] = {
            "provider": provider, "target": target,
            "method": method, "url": url, "trust": trust,
            "room_id": room_id, "persona_pid": persona_pid,
            "task_id": task_id, "event_id": event_id,
        }
        if body is not None:
            params["body_b64"] = base64.b64encode(body).decode("ascii")
        return self._call("prepare_headers", params)

    def prepare_credentials(
        self, provider: str, target: str = "", *, trust: str = "user",
        session_vars: dict[str, str] | None = None, room_id: str = "",
        persona_pid: str = "", task_id: str = "", event_id: str = "",
    ) -> dict:
        return self._call("prepare_credentials", {
            "provider": provider, "target": target, "trust": trust,
            "session_vars": session_vars or {},
            "room_id": room_id, "persona_pid": persona_pid,
            "task_id": task_id, "event_id": event_id,
        })

    def open_db_connection(self, provider: str, *, database: str = "",
                           trust: str = "user") -> tuple[dict, socket.socket]:
        """Open a DB connection via the broker and return (info, fd_socket).

        The broker authenticates the underlying TCP connection (with a
        password the agent never sees) then sends the live fd back over
        SCM_RIGHTS. The returned socket.socket is wrapped from the fd
        and ready for the DB driver to use directly.

        POSIX-only (Linux + macOS). Windows raises NotImplementedError.
        """
        import sys
        if sys.platform.startswith("win"):
            raise NotImplementedError(
                "fd-passing not supported on Windows; use a different transport"
            )
        rid = str(uuid.uuid4())
        req = {"jsonrpc": "2.0", "id": rid, "method": "open_db_connection",
               "params": {"provider": provider, "database": database,
                          "trust": trust}}
        body = json.dumps(req, separators=(",", ":")).encode("utf-8")
        with self._lock:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.settimeout(self.timeout)
                s.connect(self.socket_path)
                s.sendall(_FRAME_HDR.pack(len(body)) + body)
                hdr = self._recvall(s, 4)
                (n,) = _FRAME_HDR.unpack(hdr)
                resp = json.loads(self._recvall(s, n).decode("utf-8"))
                if "error" in resp:
                    err = resp["error"]
                    s.close()
                    raise BrokerError(
                        code=err.get("code", -1),
                        message=err.get("message", "unknown"),
                        data=err.get("data", {}),
                    )
                # Now expect an SCM_RIGHTS message with one fd.
                msg, fds, _flags, _addr = socket.recv_fds(s, 1, 1)
                if not fds:
                    s.close()
                    raise BrokerError(
                        code=-32030, message="no_fd_received", data={},
                    )
                fd = fds[0]
                s.close()
            except Exception:
                s.close()
                raise
        return resp["result"], socket.socket(fileno=fd)

    def get_room_mode(self) -> dict:
        """Current deliberation mode as the broker sees it (planning|active)."""
        return self._call("get_room_mode", {})

    #: Server-signed event types that carry the room's deliberation mode.
    MODE_BEARING_TYPES = (
        "org.agentstorming.mode_promoted",
        "org.agentstorming.metadata_snapshot",
    )

    def relay_room_mode(self, envelope: dict[str, Any]) -> dict:
        """Relay a server-signed mode-bearing event to the broker.

        The broker will not take our word for the room's mode — it verifies
        the storm server's signature itself (Stage-13 §Planning mode,
        ADR-008). So we hand over the exact bytes the server signed,
        reconstructed the same way ``verify_envelope`` does, plus the
        signature. We are only the courier.

        Accepts either a ``mode_promoted`` event (a transition) or a
        ``metadata_snapshot`` (the current truth, which is how an agent that
        joins *after* a promotion learns about it).

        A broker that never hears from us keeps whatever mode it was
        configured with, which is the safe direction.
        """
        import base64

        from .signing import canonicalise

        sig = (envelope.get("sig") or {}).get("val")
        if not sig:
            raise ValueError("envelope carries no sig.val to relay")
        canonical = canonicalise(envelope)
        return self._call("set_room_mode", {
            "canonical_b64": base64.b64encode(canonical).decode("ascii"),
            "sig": sig,
        })

    def maybe_relay_room_mode(self, envelope: dict[str, Any]) -> dict | None:
        """Relay ``envelope`` if it carries a mode; otherwise do nothing.

        Convenience for event-loop callers that see every event and should
        not have to classify them. Returns None when the envelope is not
        mode-bearing, and swallows the broker's refusal of a stale or
        replayed envelope — that is the broker working as intended, not an
        error the agent should crash on.
        """
        if envelope.get("type") not in self.MODE_BEARING_TYPES:
            return None
        if not (envelope.get("sig") or {}).get("val"):
            return None
        try:
            return self.relay_room_mode(envelope)
        except BrokerError as e:
            if e.message in ("mode_verification_failed", "room_mode_unconfigured"):
                return None
            raise

    def sign(self, provider: str, payload: bytes, *, trust: str = "user",
             room_id: str = "", persona_pid: str = "", task_id: str = "",
             event_id: str = "") -> dict:
        """Request a signature over ``payload`` from a KeyHandleProvider.

        Returns ``{alg, kid, sig_b64}``. The agent never sees the
        private key.
        """
        import base64
        return self._call("sign", {
            "provider": provider,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "trust": trust,
            "room_id": room_id, "persona_pid": persona_pid,
            "task_id": task_id, "event_id": event_id,
        })
