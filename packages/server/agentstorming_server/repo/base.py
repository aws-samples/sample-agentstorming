# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Shared DB plumbing: a lightly typed asyncpg pool wrapper."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg


def hash_secret(token: str) -> bytes:
    """SHA-256 of a caller-supplied token, as stored in place of the token.

    UTF-8 with surrogatepass, never ASCII: the server mints ASCII tokens, but a
    caller can send anything, and encode("ascii") made each such token a 500.
    The mapping is one-to-one, so a non-ASCII input cannot collide with the
    hash of a real token.
    """
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).digest()


class Database:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self.dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=30,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database not connected")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    @asynccontextmanager
    async def use(self, conn: asyncpg.Connection | None) -> AsyncIterator[asyncpg.Connection]:
        """The caller's connection when it passes one, else one from the pool.

        Lets a repository method join a transaction the caller already holds,
        so work done under a lock needs no second connection.
        """
        if conn is not None:
            yield conn
            return
        async with self.pool.acquire() as pooled:
            yield pooled
