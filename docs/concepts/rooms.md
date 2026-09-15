# Rooms

A **room** is a discussion surface. One room, one shared event log;
every participant sees the same sequence of events (subject to the
whisper filter — §5.2).

## Lifecycle

```
CREATED ── first participant joins ──→ ACTIVE ──── terminate ──→ TERMINATED
             │                             │
             │                             └── moderator gone > grace ──→ FROZEN
             │                                   │
             └── never becomes ACTIVE until moderator joins ←── (if frozen with no moderator)
```

- **CREATED** — room exists, no events yet.
- **ACTIVE** — normal operation; events flow.
- **FROZEN** — the original moderator (or acting deputy) has been
  disconnected for longer than `disconnect_grace_seconds`. Members
  can still subscribe but cannot post (except whispers). If no deputy
  is promoted within `grace * freeze_multiplier`, the room stays
  frozen until a moderator reconnects.
- **TERMINATED** — soft delete; events kept for audit, posting
  forbidden.

## Visibility

- **`public`** — listed in `GET /v1/rooms` and accepts public
  registration (`POST /v1/rooms/{id}/register`).
- **`private`** — omitted from discovery; joining requires an invite
  token.

## Config knobs (§5.2)

Settable at creation and via `PATCH /v1/owner/rooms/{id}`:

| Field | Default | Meaning |
|---|---|---|
| `raise_hand_required` | false | Gate posting behind moderator grants. |
| `go_speak_ttl_seconds` | 120 | How long a grant stays valid. |
| `disconnect_grace_seconds` | 60 | Moderator disconnect grace before freeze. |
| `freeze_multiplier` | 5 | How long the room stays frozen before deputy is considered for promotion. |
| `history_max_return` | 1000 | Max events per `/messages` page. |
| `visibility` | "private" | "public" opens registration + discovery. |
| `deputies_enabled` | false | Allow the moderator to pin ordered deputies. |

Servers MAY define additional fields; clients MUST ignore unknown
fields (§5.2).

## Multi-room

One server hosts many rooms. A single participant may hold separate
PIDs in different rooms (the PID derives from pubkey + room_id).
Their vault stores per-room credentials keyed by room_id.

## Discovery

`GET /v1/rooms` returns all `public` rooms with their title,
description, state, and participant count. Unauthenticated — this is
how public rooms are advertised.

Owner-scope `GET /v1/owner/rooms` (signed) returns all rooms including
private ones.
