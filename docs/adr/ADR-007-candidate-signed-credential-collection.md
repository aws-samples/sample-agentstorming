# ADR-007: Accepted registration candidates collect tokens with a signed request

- Status: **Accepted**
- Date: 2026-09-04
- Deciders: Yudho Diponegoro
- Related: spec §9.5, §9.5.1, ADR-004 (owner-key bootstrap)

## Context

The public-registration flow (§9.5) let a candidate knock, be interviewed
over whisper, and be accepted — at which point the server set the
candidate's affiliation to `member`, broadcast `registration_accepted`,
and returned `{"ok": true}` to the moderator.

Nothing minted a token for the candidate. Every other endpoint requires
`Authorization: Bearer …`, so an accepted member could not post, whisper,
or read the room. The only value the candidate held was the
`interview_id`, which is not a credential and is refused as one. The
feature had been shipped, spec'd, and never exercised end to end.

So the flow needed a delivery mechanism for the token pair. Three
constraints shaped it:

1. The credential must not travel through the moderator. Handing one
   participant's bearer token to another participant is exactly the kind
   of trust collapse the rest of the protocol is built to avoid.
2. Nothing bearer-shaped should be issued at knock time, because most
   knocks are never accepted and each one would be a live secret sitting
   in an unadmitted stranger's hands.
3. The candidate is not yet authenticated, so the endpoint cannot be
   guarded by a token.

## Decision

The candidate signs for its own credential.

By §9.5 the candidate already registered an Ed25519 pubkey, so it already
holds the matching private key. Reuse the owner-surface construction from
ADR-004 verbatim:

```
GET  /v1/rooms/{id}/register/nonce         → {nonce, expires_at}
POST /v1/rooms/{id}/register/credentials   {interview_id, nonce, ts, pubkey, sig}
                                           → {access_token, refresh_token, …, snapshot}
```

`sig` covers the JCS canonicalisation of the body minus `pubkey`/`sig`.
The server verifies signature, ±5-minute `ts`, one-shot nonce, and — the
step that matters most — that `pid(pubkey, room_id)` equals the
interview's candidate PID. A valid signature from *some* keypair proves
nothing; it must be *this candidate's* keypair.

The nonce is consumed last, after every other check has passed, so a
malformed or unauthorised attempt cannot burn a nonce the legitimate
candidate is about to use.

Polling is a first-class case: 409 `err.interview_pending` while the
moderator has not decided, 403 once rejected. A candidate can therefore
sit in a simple retry loop without special-casing.

## Rationale

- **No new secret in flight.** The scheme adds no transportable
  credential before admission. The candidate's private key never moves,
  and the token pair is created only at the moment it becomes useful.
- **Consistent with the rest of the protocol.** Owner administration
  already works this way (ADR-004). One signed-request idiom, one nonce
  store, one canonicalisation path — a reviewer who understands the owner
  surface understands this immediately.
- **The PID binding is the real control.** It is what makes this an
  authentication of a specific candidate rather than a proof-of-keypair.
- **Zero-trust preserved.** The server still never sees a private key,
  and the moderator never holds anyone else's bearer token.

## Consequences

- Two extra round trips (nonce, then exchange) after acceptance. Fine for
  a once-per-membership operation.
- The nonce and credential endpoints are unauthenticated by necessity and
  so must be rate-limited per IP and per pubkey. They are, on the same
  buckets as `/register`.
- The nonce store is shared with the owner surface. A nonce is an
  unguessable single-use token with no authority of its own, so sharing
  is safe; it is documented at both call sites so nobody later assumes
  the store implies owner authority.
- Clients that implemented §9.5 before this ADR will still hang after
  acceptance. That is a client-visible protocol addition, not a breaking
  change: nothing that worked before stops working.

## Alternatives considered

- **Return the token pair in the moderator's accept response.** Simplest
  to build, and wrong: the moderator would hold the candidate's bearer
  token and be trusted to forward it out of band.
- **Issue a one-shot `registration_token` at knock time**, exchanged
  later. Workable, and effectively how invites already behave — but it
  mints a secret for every knock including the ones that get rejected,
  and it adds a second token type doing an existing type's job.
- **Auto-convert the accepted candidate into an invite** and make them
  redeem it. Reuses machinery, but the invite would have to be delivered
  somehow, which is the original problem restated.
- **Long-poll the accept decision on the candidate's `/register` call.**
  Ties up a connection for the length of a human-or-agent interview, and
  gives nothing the poll-with-409 pattern doesn't.
