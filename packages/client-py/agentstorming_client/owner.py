# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Owner-key primitives + CLI helpers.

The human owner of a Storm server holds an Ed25519 keypair. The
public half is registered in the server's ``owner_keys`` table (seeded
from ``AGENTSTORMING_OWNER_PUBKEY`` at first boot; additions thereafter
via signed requests). The private half stays on the owner's device.

This module:

- Generates / loads the owner keypair from a file vault.
- Exposes ``print_pubkey`` (emit the base64url public key for the
  operator to paste into terraform vars / server env).
- Implements signed owner requests: ``GET /v1/owner/nonce``, then the call
  itself with a fresh Ed25519 signature. ``request_invite`` posts a signed
  body; ``list_rooms``, ``get_room`` and ``list_keys`` send signed GETs.

Every signature covers ``op``, the method and API path being called
(``"GET /v1/owner/rooms"``), so the server only accepts it on that endpoint.
A body request carries ``nonce``/``ts``/``pubkey``/``sig`` in the body; a GET
carries them in the ``X-AgentStorming-Owner-*`` headers. The nonce request is
signed too, over ``op`` and ``ts`` alone, since it is how the nonce is got.

Backends beyond plain file (keychain, HSM, TPM) can be added by
swapping the vault implementation. The default file backend is
sufficient for laptops with disk encryption.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .signing import (
    b64url,
    b64url_decode,
    canonicalise_any,
    generate_keypair,
    sign_blob,
)


DEFAULT_KEY_PATH = Path("~/.config/agentstorming/owner.key").expanduser()


def _default_key_path() -> Path:
    return DEFAULT_KEY_PATH


def init_key(path: Path | None = None, overwrite: bool = False) -> tuple[bytes, bytes]:
    """Generate a new Ed25519 owner keypair at ``path`` and return (priv, pub).

    Raises ``FileExistsError`` if ``path`` already holds a key and
    ``overwrite`` is False (default).
    """
    path = Path(path or _default_key_path()).expanduser()
    if path.exists() and not overwrite:
        raise FileExistsError(f"owner key already exists at {path}; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    priv, pub = generate_keypair()
    payload = {
        "private_key_b64": b64url(priv),
        "public_key_b64": b64url(pub),
    }
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return priv, pub


def load_key(path: Path | None = None) -> tuple[bytes, bytes]:
    path = Path(path or _default_key_path()).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"no owner key at {path} — run `agentstorming owner init-key` first"
        )
    data = json.loads(path.read_text())
    priv = b64url_decode(data["private_key_b64"])
    pub = b64url_decode(data["public_key_b64"])
    return priv, pub


def print_pubkey(path: Path | None = None) -> str:
    _priv, pub = load_key(path)
    return b64url(pub)


#: The headers that carry the owner signature on a GET, which has no body.
OWNER_SIG_HEADERS = {
    "nonce": "X-AgentStorming-Owner-Nonce",
    "ts": "X-AgentStorming-Owner-Ts",
    "pubkey": "X-AgentStorming-Owner-Pubkey",
    "sig": "X-AgentStorming-Owner-Sig",
}


def op_path(path: str) -> str:
    """The path as it appears in ``op``: ``%`` → ``%25``, then ``?`` → ``%3F``.

    Same rule as the server's ``op_path``. Without it a room id containing
    ``?`` signed the same ``op`` as a shorter id followed by a query string.
    """
    return path.replace("%", "%25").replace("?", "%3F")


_last_ts: datetime | None = None
_ts_lock = threading.Lock()


def unique_timestamp() -> str:
    """A timestamp later than the last one this process signed with.

    Ed25519 is deterministic, so two nonce requests with the same timestamp
    are byte-identical, and the server accepts each signed nonce request once.
    Only a tie or a step back of under a second is bumped: after a larger step
    back the clock wins again, or every request would stay ahead of the
    server's ±5 minute window until real time caught up.
    """
    global _last_ts
    with _ts_lock:
        now = datetime.now(timezone.utc)
        if _last_ts is None or now > _last_ts or _last_ts - now > timedelta(seconds=1):
            _last_ts = now
        else:
            _last_ts += timedelta(microseconds=1)
        return _last_ts.isoformat()


def sign_owner_request(
    method: str, path: str, fields: dict[str, Any], nonce: str | None, priv: bytes,
    *, ts: str | None = None,
) -> tuple[str, str]:
    """Sign an owner request and return ``(ts, sig)``.

    ``path`` is the decoded API path, e.g. ``/v1/owner/rooms/my room``. The
    signed payload is ``fields`` plus ``nonce``, ``ts`` and ``op``; ``op`` is
    not sent, because the server rebuilds it from the request it receives.
    ``nonce`` is None only for ``GET /v1/owner/nonce`` itself.
    """
    ts = ts or unique_timestamp()
    payload: dict[str, Any] = {**fields, "ts": ts, "op": f"{method.upper()} {op_path(path)}"}
    if nonce is not None:
        payload["nonce"] = nonce
    return ts, sign_blob(canonicalise_any(payload), priv)


def _client(verify_tls: bool, timeout: float, transport: httpx.AsyncBaseTransport | None):
    return httpx.AsyncClient(verify=verify_tls, timeout=timeout, transport=transport)


async def _nonce(client: httpx.AsyncClient, base_url: str, priv: bytes, pub: bytes) -> str:
    path = "/v1/owner/nonce"
    ts, sig = sign_owner_request("GET", path, {}, None, priv)
    r = await client.get(
        f"{base_url}{path}",
        headers={
            OWNER_SIG_HEADERS["ts"]: ts,
            OWNER_SIG_HEADERS["pubkey"]: b64url(pub),
            OWNER_SIG_HEADERS["sig"]: sig,
        },
    )
    r.raise_for_status()
    return r.json()["nonce"]


async def _signed_get(
    base_url: str, path: str, key_path: Path | None,
    *, verify_tls: bool, timeout: float, transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    priv, pub = load_key(key_path)
    base_url = base_url.rstrip("/")
    async with _client(verify_tls, timeout, transport) as client:
        nonce = await _nonce(client, base_url, priv, pub)
        ts, sig = sign_owner_request("GET", path, {}, nonce, priv)
        headers = {
            OWNER_SIG_HEADERS["nonce"]: nonce,
            OWNER_SIG_HEADERS["ts"]: ts,
            OWNER_SIG_HEADERS["pubkey"]: b64url(pub),
            OWNER_SIG_HEADERS["sig"]: sig,
        }
        r = await client.get(f"{base_url}{quote(path, safe='/')}", headers=headers)
        r.raise_for_status()
        return r.json()


async def request_invite(
    base_url: str,
    room_id: str,
    kind: str = "owner",
    key_path: Path | None = None,
    *,
    verify_tls: bool = True,
    timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Hit the server to mint a fresh invite using the owner key.

    Returns ``{invite_token, kind, room_id, expires_at}``. Raises an
    httpx HTTPStatusError on server-side rejection so callers can
    inspect status + body.
    """
    priv, pub = load_key(key_path)
    base_url = base_url.rstrip("/")
    path = "/v1/owner/request-invite"

    async with _client(verify_tls, timeout, transport) as client:
        nonce = await _nonce(client, base_url, priv, pub)
        fields = {"room_id": room_id, "kind": kind}
        ts, sig = sign_owner_request("POST", path, fields, nonce, priv)
        r = await client.post(
            f"{base_url}{path}",
            json={**fields, "nonce": nonce, "ts": ts, "pubkey": b64url(pub), "sig": sig},
        )
        r.raise_for_status()
        return r.json()


async def list_rooms(
    base_url: str, key_path: Path | None = None,
    *, verify_tls: bool = True, timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Every active room on the server, private ones included. Owner only."""
    return await _signed_get(base_url, "/v1/owner/rooms", key_path,
                             verify_tls=verify_tls, timeout=timeout, transport=transport)


async def get_room(
    base_url: str, room_id: str, key_path: Path | None = None,
    *, verify_tls: bool = True, timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """One room's state, full config and moderator. Owner only."""
    return await _signed_get(base_url, f"/v1/owner/rooms/{room_id}", key_path,
                             verify_tls=verify_tls, timeout=timeout, transport=transport)


async def list_keys(
    base_url: str, key_path: Path | None = None,
    *, verify_tls: bool = True, timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """The server's active owner keys. Owner only."""
    return await _signed_get(base_url, "/v1/owner/keys", key_path,
                             verify_tls=verify_tls, timeout=timeout, transport=transport)
