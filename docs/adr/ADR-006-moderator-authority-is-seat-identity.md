# ADR-006: Moderator authority is seat identity, not a power-level threshold

- Status: **Accepted**
- Date: 2026-09-04
- Deciders: Yudho Diponegoro
- Related: spec §6.1 (affiliations), §6.3.1, §6.4, §12.4, §13.1

## Context

Affiliations carry an integer power level borrowed from Matrix
(§6.1): `room-owner` 100, `original-moderator` 90, `deputy-N` 80−N,
`member` 50, and so on down. The power scalar exists to answer "may P
change Q's affiliation?" (§6.3, "cannot set ≥ self").

`services/auth.py:require_moderator` reused that same scalar to answer a
different question — "may this caller act as the moderator?" — with the
test `power_level < 50 → 403`. A plain `member` is exactly 50. The test
therefore admitted every member in the room, and it guarded ten endpoints:
grants, mute, eject, pen, deputy assignment, document create/update,
rolling summary, moderator-scoped config PATCH, and the ignore list. Any
member could grant themselves a speaking turn in raise-hand mode, eject or
pen any peer, appoint deputies, and rewrite the problem statement.

The bug was found by an external reviewer probing the `/grants` endpoint
directly; the impact turned out to be wider than the report.

## Decision

Split the two questions.

- **Ordering** questions (may P outrank Q?) keep using `power_level`.
- **Moderator authority** is decided by identity of the seat holder.
  `AuthGate.is_moderator` returns true only for (a) the `room-owner`, or
  (b) the participant currently holding the `moderating` role.

"Currently holding" is resolved by a single function,
`ParticipantRepo.get_acting_moderator`: the seated `original-moderator`
if there is one, else the lowest-numbered (highest-ranked) deputy who has
not left. That is deliberately the *same* resolution
`services/snapshot.py` publishes as `moderator_pid`, so authorisation and
the roster clients see cannot drift apart.

A deputy in the succession line while an original-moderator is still
seated is **not** a moderator. §6.4 admits exactly one holder of
`moderating`, and §12.4 reserves deputy assignment for "the current
moderator".

## Rationale

- Power levels are a *total order*, and every order has a bottom rung
  that sits at some number. Any absolute threshold over an order that
  includes ordinary participants will eventually admit them; the only
  question is whether someone notices. Identity has no such failure mode.
- Making authorisation read the same resolver as the published snapshot
  turns "who is the moderator?" into one fact with one implementation.
  Two implementations of that question is how the room's roster and its
  access control end up disagreeing.
- The check is cheap: one indexed query per moderator-gated call, on a
  path that already loads the participant row for the bearer token.

## Consequences

- One extra DB round trip on moderator-gated endpoints. Acceptable —
  these are low-frequency governance calls, not the message hot path.
- The room-owner is explicitly allowed, which keeps story O3/H9 ("Claim
  moderator" button) working without first taking the seat.
- If the seat is vacant (owner vacated it, no deputies) then *only* the
  owner can moderate until someone claims. That is the correct reading of
  §6.4 and it is now covered by a test.
- Any future endpoint that needs moderator authority must call
  `require_moderator`; adding a bespoke inline check would reintroduce the
  same class of bug. §6.3.1 now says so normatively.

## Alternatives considered

- **Raise the threshold to `power_level > 50`.** The one-line fix, and
  what the external report suggested. Rejected: it silently grants
  moderator authority to every deputy (70–79) whether or not they hold
  the seat, which contradicts §6.4, and it leaves the next person to add
  an affiliation guessing where the line is.
- **Move `member` down the scale** (e.g. to 10) and keep the threshold.
  Rejected: power levels are wire-visible in `affiliation_changed`
  payloads and the §6.1 table is normative; renumbering to accommodate an
  internal check is the tail wagging the dog.
- **Check `affiliation in ("room-owner", "original-moderator")`.**
  Simple, but wrong after a freeze promotes a deputy (§12.5) — the acting
  moderator would be locked out of the endpoints they exist to serve.
- **Carry a `moderating` role column on `participants`.** Cleaner to read
  but adds a denormalised field that must be kept in step with departures
  and promotions; the resolver derives the same answer from existing
  state.

## Amendment, 2026-09-04 — the same mistake in the other direction

The original fix covered `require_moderator`, which is what the external
report exercised. Two sites in `PostEventService` were missed, and both were
the mirror image of the same error:

```python
if principal.participant.power_level < 80:   # "moderator + owner bypass"
```

one gating the freeze bypass, one gating raise-hand. `affiliation_power_level`
returns `80 - deputy_rank` for a deputy, so **every** deputy sits below 80.
Where the original bug was too permissive — admitting every member — these
were too restrictive: once the seat passed to a deputy, they denied the
*acting moderator*.

For freeze the failure is self-defeating. Succession to a deputy is precisely
what happens when the freeze TTL elapses (§12.5), so the one participant who
needed to act in a frozen room was the one locked out of it. A member was
correctly refused and the acting moderator was refused alongside them, which
makes the freeze state unrecoverable without the owner.

Both now call a `_has_moderator_authority` helper applying the same rule as
`AuthService.is_moderator`. `packages/server/.../tests/integration/`
`test_deputy_moderator_authority.py` pins all three cases — acting deputy
admitted, ordinary member refused, owner admitted — and was confirmed to fail
against the old threshold before the fix landed.

The generalisation is worth stating plainly, because this decision has now
been got wrong twice in opposite directions at four different call sites:
**a role is not a number.** Any numeric stand-in for "is this the moderator"
is wrong in one direction or the other, and which direction depends on where
the seat happens to sit at that moment. There is no threshold that is correct;
there is only the seat.
