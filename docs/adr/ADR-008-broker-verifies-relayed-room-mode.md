# ADR-008: The broker verifies a relayed room mode rather than trusting the agent

- Status: **Accepted**
- Date: 2026-09-04
- Deciders: Yudho Diponegoro
- Related: spec Stage-13 addendum §Planning mode, ADR-003 (Ed25519 + JCS)

## Context

Planning mode (Stage-13) says that while a room's `mode` is `planning`,
every tool call declaring `effects: write` is denied **at the broker**. It
is the control that answers the Replit SaaStr class of incident, where an
agent acted during what its operator believed was a planning phase.

Until now it did not exist. `org.agentstorming.mode_promoted` was a string
constant in `domain/event.py`; `RoomConfig` had no `mode` field; the
broker's `PolicyEngine` parsed `effects` and never read it. A signed
`mode_promoted` event could be constructed and stored, and nothing in the
running system changed — an auditable record of an intention, not a
mechanism.

Implementing it raises a trust question the spec had not answered: the
broker is a separate process from the agent, deliberately so, and the
agent is the *untrusted* party in the Stage-13 threat model. How does the
broker learn the room's mode? If the answer is "the agent tells it", then
a jailbroken persona says "we're active now" and the control evaporates.

## Decision

The broker only believes a mode transition the storm server signed.

The agent relays the `mode_promoted` event; the broker verifies the
server's Ed25519 signature against the room's `server_pubkey`, configured
out of band at broker start (`[room] room_id`, `server_pubkey` in the
broker config, alongside the credential material). The agent's own
assertion carries no weight.

Verification additionally requires: `type == org.agentstorming.mode_promoted`,
`room_id` equal to the broker's configured room, `sender == "system"`,
a parseable `to_mode`, an `iat` no older than 24h, and an `iat` strictly
newer than the last transition applied. The last two together stop a
captured promotion being replayed to re-arm `active` after an owner has
demoted the room.

A broker with no `room_id`/`server_pubkey` configured refuses every
`set_room_mode` call and keeps its configured mode for the session. If
that mode is `planning`, writes stay denied — failure keeps the
restriction rather than dropping it.

**The broker does not canonicalise JSON.** The agent sends the exact
canonical bytes the server signed; the broker verifies the signature over
those bytes and then reads the claims out of the same bytes it just
proved.

## Rationale

- **It reuses an invariant already in the protocol.** System events are
  signed by the server's key and the server pubkey is in every
  `metadata_snapshot` (§16.1). The mechanism for "the server said this,
  provably" was already there; this decision just consumes it.
- **Not canonicalising in the broker is the important detail.** ADR-003
  notes that every additional JCS implementation is a chance for
  byte-level drift that breaks signatures silently, and warns that the
  three existing copies (spec, Python SDK, TypeScript SDK) must stay
  byte-identical. Verify-the-bytes-you-were-given removes any
  re-serialisation step in which drift could hide, and keeps the
  security-critical process free of a fourth copy.
- **It keeps the broker's dependency surface small.** `cryptography` and
  the stdlib, no SDK import, no network client, no polling loop.
- **It fails in the safe direction** at every branch: unconfigured,
  unverifiable, stale, or replayed all leave the previous mode standing.

## Consequences

- The broker config gains a `[room]` section. Deployments that do not set
  it keep working, with mode fixed at whatever they configured.
- The agent must relay: on join (if the snapshot shows a promotion) and
  whenever `mode_promoted` arrives in the buffer.
  `BrokerClient.relay_room_mode` does the byte reconstruction so callers
  do not hand-roll it.
- Room mode is applied to *every* policy engine the broker hosts. Mode is
  a property of the room, not of one persona's uid.
- A per-persona `mode` in broker config may only be stricter than the
  room's, matching the monotonic-narrowing rule for capability sets.
- An offline broker cannot be promoted. Acceptable: the room stays in the
  more restrictive state until the relay succeeds.

## Alternatives considered

- **Trust the agent's `set_room_mode(mode)`.** One line, and it makes the
  feature decorative. Rejected outright — the agent is the adversary this
  control exists to bound.
- **Broker polls the storm server's snapshot endpoint.** Authoritative
  and self-serving, needing no relay. Rejected for v1: it gives the
  broker a network dependency and a room access token of its own, both of
  which enlarge the process we most want to keep small and offline-ish.
  Worth revisiting if brokers ever need to learn other room state.
- **Static mode in broker config only, no transitions.** Safe and
  useless: promotion would require restarting the broker mid-session,
  which drops pending HITL approvals and every warm provider.
- **Have the broker re-canonicalise the envelope with its own JCS.**
  The obvious shape, and the one ADR-003 tells us not to build.
