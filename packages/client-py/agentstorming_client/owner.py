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
- Implements the signed invite-request flow: ``GET /v1/owner/nonce``
  followed by ``POST /v1/owner/request-invite`` with a fresh Ed25519
  signature.

Backends beyond plain file (keychain, HSM, TPM) can be added by
swapping the vault implementation. The default file backend is
sufficient for laptops with disk encryption.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


async def request_invite(
    base_url: str,
    room_id: str,
    kind: str = "owner",
    key_path: Path | None = None,
    *,
    verify_tls: bool = True,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Hit the server to mint a fresh invite using the owner key.

    Returns ``{invite_token, kind, room_id, expires_at}``. Raises an
    httpx HTTPStatusError on server-side rejection so callers can
    inspect status + body.
    """
    priv, pub = load_key(key_path)
    base_url = base_url.rstrip("/")

    async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
        # Step 1: grab a nonce.
        r = await client.get(f"{base_url}/v1/owner/nonce")
        r.raise_for_status()
        nonce_resp = r.json()
        nonce = nonce_resp["nonce"]

        ts = datetime.now(timezone.utc).isoformat()
        body = {"room_id": room_id, "kind": kind, "nonce": nonce, "ts": ts}
        signed_bytes = canonicalise_any(body)
        sig = sign_blob(signed_bytes, priv)

        r = await client.post(
            f"{base_url}/v1/owner/request-invite",
            json={**body, "pubkey": b64url(pub), "sig": sig},
        )
        r.raise_for_status()
        return r.json()
