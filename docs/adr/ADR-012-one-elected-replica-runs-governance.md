<!--
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
-->

# ADR-012: One elected replica runs governance

- Status: Accepted
- Date: 2026-11-03
- Deciders: Yudho Ahmad Diponegoro
- Related: ADR-002 (Postgres LISTEN/NOTIFY), `services/governance.py`

## Context

The governance ticker expires go-speak grants, mutes and pens, judges whether a
room's moderator is present, emits periodic metadata snapshots and disconnect
notices, and purges expired nonces. Its effects are room-wide and must happen
once: two replicas expiring the same grant, or each emitting its own snapshot
of the same room, give every participant duplicate signed events.

The ticker guarded each tick with `pg_try_advisory_lock`, a session-level lock,
taken on a pooled connection that was returned straight away. asyncpg resets a
connection when it goes back to the pool, and the reset runs
`pg_advisory_unlock_all()`, so the lock was released before the tick began and
excluded nobody. Measured on two processes: mid-tick, a second session could
take the lock.

The first correction held the connection for the length of one tick. That
stopped ticks overlapping but still let every replica tick in turn, and the
snapshot and disconnect bookkeeping is held per process, so every replica still
published its own snapshots.

## Decision

Governance runs on one replica, elected by holding the advisory lock on a
dedicated connection, outside the pool, for the life of the process. Every
replica tries the lock each tick; the holder ticks, the rest wait. The holder
re-checks each tick that its session still holds the lock (`pg_locks` for its
own backend), and stands down if the connection fails. The connection sets
`idle_session_timeout` (Postgres 14 and later) and TCP keepalives, so the
server ends the session, and the lock with it, soon after a leader vanishes
without closing its socket.

## Rationale

Postgres is already the coordination point every replica shares, and a
session-level advisory lock is released by the server when the session ends,
however the holder dies. That gives leader election with no new component and
no lease to renew.

## Consequences

- One replica's governance load instead of all of them; the others are idle
  apart from one lock attempt per tick.
- Failover is bounded by how fast the server notices the old leader is gone:
  immediately on a clean exit or a closed socket, within `idle_session_timeout`
  (30 s, or three ticks if longer) or the keepalive bound otherwise.
- At failover the new leader's snapshot bookkeeping is empty, so it may emit
  one snapshot per room earlier than the interval would.
- Every server sharing a database must use the same lock id. Tests that share a
  database with a running server pass their own (`GovernanceTicker(lock_id=)`).
- A process now holds one connection beyond its pool.

## Alternatives considered

- **Lock per tick on a pooled connection.** What shipped: released before the
  tick ran. Rejected, as described above.
- **Lock held for one tick.** Serialises ticks but not their effects. Rejected.
- **Move the dedup state into the database.** Would make concurrent ticks
  idempotent, but every governance query would need it, and two replicas would
  still do the same work twice. Rejected in favour of electing one.
- **An external lock service or a dedicated governance task.** More moving parts
  for a reference implementation whose only shared dependency is Postgres.
