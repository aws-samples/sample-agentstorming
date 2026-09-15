# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Replay protection: timestamp-skew + per-sender nonce window."""

from __future__ import annotations

from datetime import datetime, timezone

from ..repo.nonces import NonceRepo


class ReplayError(Exception):
    pass


class ReplayGuard:
    def __init__(self, nonces: NonceRepo, clock_skew_seconds: int, window: int) -> None:
        self._nonces = nonces
        self._clock_skew_seconds = clock_skew_seconds
        self._window = window

    async def check_and_register(self, pid: str, nonce: str, iat) -> None:
        now = datetime.now(timezone.utc)
        if isinstance(iat, str):
            s = iat[:-1] + "+00:00" if iat.endswith("Z") else iat
            try:
                iat_dt = datetime.fromisoformat(s)
            except ValueError:
                iat_dt = now
        else:
            iat_dt = iat
        if iat_dt.tzinfo is None:
            iat_dt = iat_dt.replace(tzinfo=timezone.utc)
        delta = abs((now - iat_dt).total_seconds())
        if delta > self._clock_skew_seconds:
            raise ReplayError(f"timestamp outside skew window: delta={delta:.1f}s")

        # Duplicate nonce within the window?
        if await self._nonces.has(pid, nonce):
            raise ReplayError("nonce already seen")
        await self._nonces.insert(pid, nonce, now)
        await self._nonces.trim(pid, self._window)
