# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""In-memory token-bucket rate limiter keyed by (pid, op)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict

from fastapi import HTTPException


@dataclass
class Bucket:
    tokens: float
    last_ts: float


class RateLimiter:
    # Default per-minute caps for ops not explicitly configured. Tuned
    # conservatively — the owner/register/grants-extend/dynamic-invite
    # surfaces are infrequent by design.
    _DEFAULTS = {
        "owner": 30,
        # Nonce issuance, per verified owner key. One per signed request, so
        # it is set above "owner" to leave room for a retry after a 409.
        "owner_nonce": 60,
        "register": 5,
        "grants_extend": 20,
        "dynamic_invite": 10,
        # §20.4 — 1/second per refresh token (60 rpm).
        # nosec B105 — a requests-per-minute integer; B105 matches the key name.
        "token_refresh": 60,  # nosec B105
    }

    def __init__(self, *, post_per_minute: int, raise_hand_per_minute: int, sync_per_minute: int) -> None:
        self._rates: Dict[str, float] = {
            "post": post_per_minute / 60.0,
            "raise_hand": raise_hand_per_minute / 60.0,
            "sync": sync_per_minute / 60.0,
        }
        self._capacity: Dict[str, int] = {
            "post": post_per_minute,
            "raise_hand": raise_hand_per_minute,
            "sync": sync_per_minute,
        }
        for op, cap in self._DEFAULTS.items():
            self._rates.setdefault(op, cap / 60.0)
            self._capacity.setdefault(op, cap)
        self._buckets: Dict[tuple[str, str], Bucket] = {}
        self._lock = asyncio.Lock()

    #: Past this many buckets, refilled ones are dropped. A full bucket is
    #: indistinguishable from a missing one, so dropping it changes no answer;
    #: keeping them all let callers that vary their key (a fresh pubkey per
    #: request, say) grow the dict for the life of the process.
    PRUNE_AT = 10_000

    def _prune(self, now: float) -> None:
        for key, b in list(self._buckets.items()):
            rate = self._rates.get(key[1], 1.0)
            cap = self._capacity.get(key[1], 60)
            if b.tokens + (now - b.last_ts) * rate >= cap:
                del self._buckets[key]

    async def check(self, pid: str, op: str) -> None:
        rate = self._rates.get(op, 1.0)
        cap = self._capacity.get(op, 60)
        async with self._lock:
            key = (pid, op)
            b = self._buckets.get(key)
            now = time.monotonic()
            if b is None and len(self._buckets) >= self.PRUNE_AT:
                self._prune(now)
            if b is None:
                b = Bucket(tokens=cap, last_ts=now)
                self._buckets[key] = b
            elapsed = now - b.last_ts
            b.tokens = min(cap, b.tokens + elapsed * rate)
            b.last_ts = now
            if b.tokens < 1.0:
                retry_after = (1.0 - b.tokens) / rate
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "org.agentstorming.err.rate_limited",
                        "message": f"rate limit for {op} exceeded",
                        "details": {"retry_after_seconds": retry_after},
                    },
                )
            b.tokens -= 1.0
