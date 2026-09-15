# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Direct-DB helpers for integration tests that need to simulate departures.

These reach past the HTTP surface on purpose: some governance states (a
moderator who has left, a stale ``last_seen_at``) are produced by the
governance ticker or by a clean leave, and asserting on the *consequences*
of that state is clearer than driving the whole lifecycle each time.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import asyncpg

DSN = os.environ.get(
    "AGENTSTORMING_TEST_DSN",
    "postgresql://agentstorming@127.0.0.1:5432/agentstorming_test",
)


async def set_left(room_id: str, pid: str, when: datetime | None = None) -> None:
    """Mark a participant as having left the room."""
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "UPDATE participants SET left_at=$3 WHERE room_id=$1 AND pid=$2",
            room_id, pid, when or datetime.now(timezone.utc),
        )
    finally:
        await conn.close()
