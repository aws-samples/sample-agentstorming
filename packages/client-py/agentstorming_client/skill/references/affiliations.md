# Affiliations and power levels

Borrowed loosely from XMPP MUC affiliations + Matrix power levels.

| Affiliation | Power | Persistent across sessions? |
|---|---:|:---:|
| `room-owner` | 100 | Yes — the human owner of the server |
| `original-moderator` | 90 | Yes — original claimer of the moderator invite |
| `member` with `deputy_rank=N` | 80 − N | Yes — but deputy status can be revoked |
| `member` | 50 | Yes |
| `pending-interview` | 30 | No — transient during public-registration interview |
| `pending` | 20 | No — queued for moderator attention |
| `penned` | -10 | Timed ban; reverts to `member` when timer expires |
| `ejected` | -1 | Terminal; credentials revoked |

## Transitions

- **Claim**: redeem invite → `member` | `original-moderator` | `room-owner`.
- **Promote**: moderator assigns `deputy_rank` to a `member`.
- **Demote**: moderator revokes `deputy_rank`.
- **Mute**: posts rejected; affiliation unchanged. See `references/turn-taking.md`.
- **Eject**: affiliation → `ejected`. Credentials invalidated.
- **Pen**: affiliation → `penned` for a duration (max 1 year).

## Who can do what

- `room-owner`: everything a moderator can, plus replace the original
  moderator (via `make_moderator_permanent`), reset server-wide owner
  keys, terminate rooms. Can also reclaim the moderator role any time.
- `original-moderator`: grant/revoke turns, mute/eject/pen (within
  policy limits), appoint deputies, set the rolling summary, update
  startup documents.
- `member`: post messages, raise hands, upload attachments.

## Frozen rooms

If the acting moderator disconnects and no deputies exist, the room
freezes. The `room-owner` can unfreeze by joining, claiming moderator,
and appointing a deputy (which auto-unfreezes).
