# ADR-002: Postgres LISTEN/NOTIFY for cross-replica fan-out

- Status: **Accepted**
- Date: 2026-02-20
- Deciders: Yudho Diponegoro

## Context

When `/stream` moved off single-process long-polling the fan-out
problem showed up: two replicas behind an ALB, each holds half the
SSE subscribers, but only one handled the POST that generated the
event. The replica that didn't handle the POST still needs to wake
its subscribers.

## Decision

Use Postgres `LISTEN` / `NOTIFY`. Every insert into `events` fires a
`pg_notify('events_<room_id>', '<seq>')`. Each replica's `PubSubHub`
subscribes via `asyncpg`'s `add_listener` and wakes the in-memory
queues for that room.

## Rationale

- Already have Postgres — the DSN is the one piece of infra we can
  count on in every deploy profile (single-VM embedded PG, RDS in
  aws-native, Aurora Serverless in v2 deployments).
- No new operational dependency (no Redis, no Kafka, no SQS).
- `NOTIFY` payload is the `seq` integer — the replica fetches the
  actual event row with a targeted `SELECT`, so a notify-loss
  recovery is a simple "fetch after last-known-seq" call.
- Aurora Serverless v2 requires min 0.5 ACU to keep LISTEN/NOTIFY
  connections — we sized the deploys accordingly.

## Consequences

- LISTEN/NOTIFY has a per-payload size limit (8000 bytes); since we
  only send the seq integer this never bites.
- Under extreme contention `pg_notify` can backpressure. The
  governance ticker runs under a `pg_try_advisory_lock` so only one
  replica processes the tick, isolating any hot path.
- Aurora Serverless v1 (classic) is NOT compatible because it pauses
  to 0 ACU and drops LISTEN subscriptions. We're explicitly on v2.

## Alternatives considered

- **Redis Pub/Sub.** Cleaner async, but adds a second system to
  operate. Rejected for the "one Postgres, no other moving parts"
  goal.
- **Kafka.** Overkill. The event volume in any realistic room is
  tiny; Kafka's durability guarantees are already provided by the
  `events` table.
- **In-process only.** Would force us to single-instance the server
  or implement sticky sessions.
