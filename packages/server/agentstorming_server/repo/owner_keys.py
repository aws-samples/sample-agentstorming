# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Owner keys + owner nonces repository."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from .base import Database


class OwnerKeyRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list_active(self) -> list[dict[str, Any]]:
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT pubkey, label, registered_at FROM owner_keys "
                "WHERE revoked_at IS NULL ORDER BY registered_at ASC"
            )
        return [dict(r) for r in rows]

    async def has_any(self) -> bool:
        async with self.db.acquire() as conn:
            row = await conn.fetchval("SELECT 1 FROM owner_keys LIMIT 1")
        return bool(row)

    async def exists_active(self, pubkey: bytes) -> bool:
        async with self.db.acquire() as conn:
            row = await conn.fetchval(
                "SELECT 1 FROM owner_keys WHERE pubkey = $1 AND revoked_at IS NULL",
                pubkey,
            )
        return bool(row)

    async def insert(self, pubkey: bytes, label: str | None = None) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                "INSERT INTO owner_keys(pubkey, label) VALUES ($1, $2) "
                "ON CONFLICT (pubkey) DO UPDATE SET revoked_at = NULL, label = COALESCE(EXCLUDED.label, owner_keys.label)",
                pubkey,
                label,
            )

    async def revoke(self, pubkey: bytes) -> None:
        async with self.db.acquire() as conn:
            await conn.execute(
                "UPDATE owner_keys SET revoked_at = now() WHERE pubkey = $1",
                pubkey,
            )


class OwnerNonceRepo:
    def __init__(self, db: Database, ttl_seconds: int = 60) -> None:
        self.db = db
        self.ttl_seconds = ttl_seconds

    async def issue(self, *, scope: str = "owner") -> tuple[str, datetime]:
        """Issue a nonce usable only by requests of ``scope`` ("owner" or "registration")."""
        nonce = secrets.token_urlsafe(24)
        expires = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
        async with self.db.acquire() as conn:
            await conn.execute(
                "INSERT INTO owner_nonces(nonce, expires_at, scope) VALUES ($1, $2, $3)",
                nonce,
                expires,
                scope,
            )
        return nonce, expires

    async def is_live(self, nonce: str, *, scope: str = "owner") -> bool:
        """Whether ``nonce`` could be consumed now. Does not consume it."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM owner_nonces "
                "WHERE nonce = $1 AND scope = $2 AND consumed_at IS NULL AND expires_at > now()",
                nonce,
                scope,
            )
        return row is not None

    async def consume(self, nonce: str, *, scope: str = "owner") -> bool:
        """Mark a nonce consumed. Returns True iff it was valid + unexpired + unused, in ``scope``."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE owner_nonces SET consumed_at = now() "
                "WHERE nonce = $1 AND scope = $2 AND consumed_at IS NULL AND expires_at > now() "
                "RETURNING nonce",
                nonce,
                scope,
            )
        return bool(row)

    async def purge_expired(self) -> int:
        """Delete nonces that expired over an hour ago; return how many.

        Called by the governance ticker. It had no caller before, so every
        nonce ever issued, consumed or not, stayed in the table.
        """
        async with self.db.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM owner_nonces WHERE expires_at < now() - interval '1 hour'"
            )
        try:
            return int(status.rsplit(" ", 1)[-1])
        except (ValueError, AttributeError):
            return 0
