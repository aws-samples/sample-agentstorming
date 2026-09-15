# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Event buffer."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any


class BufferManager:
    def __init__(self, capacity: int = 10_000) -> None:
        self._dq: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = asyncio.Lock()

    async def append(self, event: dict[str, Any]) -> None:
        async with self._lock:
            self._dq.append(event)

    async def snapshot(self) -> list[dict[str, Any]]:
        async with self._lock:
            return list(self._dq)

    async def drain(self) -> list[dict[str, Any]]:
        async with self._lock:
            out = list(self._dq)
            self._dq.clear()
            return out

    async def size(self) -> int:
        async with self._lock:
            return len(self._dq)
