# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Events repo — append-only log."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..domain.event import Envelope
from .base import Database


def _parse_iso(s: str) -> datetime:
    # Accept both '...+00:00' and trailing 'Z'
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)


class EventRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, room_id: str, env: Envelope, raw_dict: dict | None = None) -> Envelope:
        """Append an event with server-assigned seq + ts_server (serialisable).

        If raw_dict is supplied, it is stored in the ``raw`` column as-is
        (with seq + ts_server fields added). This preserves the exact
        bytes that were signed, so downstream signature verification
        succeeds byte-for-byte. Otherwise env.model_dump(mode="json") is
        used, which is acceptable for participant-posted events whose
        envelope came from JSON already.
        """
        async with self._db.tx() as conn:
            # Per-room advisory lock so concurrent inserts can't both
            # observe MAX(seq)=N and try to write seq=N+1. asyncpg
            # serialises on the lock; releasing happens at tx commit.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", room_id,
            )
            row = await conn.fetchrow(
                "SELECT COALESCE(MAX(seq), -1) AS s FROM events WHERE room_id=$1",
                room_id,
            )
            next_seq = int(row["s"]) + 1

            ts_server_dt = datetime.now(timezone.utc)
            ts_server_iso = ts_server_dt.isoformat()

            env_json = dict(raw_dict) if raw_dict is not None else env.model_dump(mode="json")
            env_json["seq"] = next_seq
            env_json["ts_server"] = ts_server_iso

            ts_sender_dt = _parse_iso(env.ts_sender)
            iat_dt = _parse_iso(env.iat)

            await conn.execute(
                """
                INSERT INTO events(
                    room_id, seq, id, type, sender, ts_sender, ts_server, iat, nonce,
                    reply_to, mentions, payload, sig, raw
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14::jsonb)
                """,
                room_id,
                next_seq,
                env.id,
                env.type,
                env.sender,
                ts_sender_dt,
                ts_server_dt,
                iat_dt,
                env.nonce,
                env.reply_to,
                env.mentions,
                json.dumps(env.payload),
                json.dumps(env.sig.model_dump()),
                json.dumps(env_json),
            )

            await conn.execute(
                "SELECT pg_notify('room_events', $1)",
                json.dumps({"room_id": room_id, "seq": next_seq}),
            )
        env.seq = next_seq
        env.ts_server = ts_server_iso
        return env

    async def get_after(self, room_id: str, since_seq: int, limit: int) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT raw FROM events
                 WHERE room_id=$1 AND seq > $2
                 ORDER BY seq ASC
                 LIMIT $3
                """,
                room_id, since_seq, limit,
            )
        out = []
        for r in rows:
            data = r["raw"] if isinstance(r["raw"], dict) else json.loads(r["raw"])
            out.append(data)
        return out

    async def get_range(self, room_id: str, from_seq: int, to_seq: int | None, limit: int,
                        types: list[str] | None = None) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            if to_seq is None:
                if types:
                    rows = await conn.fetch(
                        """
                        SELECT raw FROM events
                         WHERE room_id=$1 AND seq >= $2 AND type = ANY($3)
                         ORDER BY seq ASC LIMIT $4
                        """,
                        room_id, from_seq, types, limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT raw FROM events
                         WHERE room_id=$1 AND seq >= $2
                         ORDER BY seq ASC LIMIT $3
                        """,
                        room_id, from_seq, limit,
                    )
            else:
                if types:
                    rows = await conn.fetch(
                        """
                        SELECT raw FROM events
                         WHERE room_id=$1 AND seq >= $2 AND seq <= $3 AND type = ANY($4)
                         ORDER BY seq ASC LIMIT $5
                        """,
                        room_id, from_seq, to_seq, types, limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT raw FROM events
                         WHERE room_id=$1 AND seq >= $2 AND seq <= $3
                         ORDER BY seq ASC LIMIT $4
                        """,
                        room_id, from_seq, to_seq, limit,
                    )
        return [r["raw"] if isinstance(r["raw"], dict) else json.loads(r["raw"]) for r in rows]

    async def get_range_by_time(
        self,
        room_id: str,
        from_ts,
        to_ts,
        limit: int,
        types: list[str] | None = None,
        cursor: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch events bounded by server timestamps.

        ``from_ts`` / ``to_ts`` may be None. ``cursor`` is a ``seq`` —
        results are strictly > cursor so clients can page forward.
        """
        where = ["room_id = $1"]
        params: list[Any] = [room_id]
        n = 2
        if from_ts is not None:
            where.append(f"ts_server >= ${n}")
            params.append(from_ts)
            n += 1
        if to_ts is not None:
            where.append(f"ts_server <= ${n}")
            params.append(to_ts)
            n += 1
        if cursor is not None:
            where.append(f"seq > ${n}")
            params.append(cursor)
            n += 1
        if types:
            where.append(f"type = ANY(${n})")
            params.append(types)
            n += 1
        # Each element of `where` is a literal predicate whose only variable
        # part is a `$N` placeholder index; no caller-supplied string is ever
        # concatenated in. Values go through asyncpg parameters in `params`.
        predicates = " AND ".join(where)
        sql = f"SELECT raw FROM events WHERE {predicates} ORDER BY seq ASC LIMIT ${n}"  # nosec B608 - literal predicates; $N is a placeholder index, not a value
        params.append(limit)
        async with self._db.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [r["raw"] if isinstance(r["raw"], dict) else json.loads(r["raw"]) for r in rows]

    async def current_seq(self, room_id: str) -> int:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(MAX(seq), -1) AS s FROM events WHERE room_id=$1",
                room_id,
            )
        return int(row["s"] if row else -1)
