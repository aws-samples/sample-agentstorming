# ADR-003: Ed25519 + JCS (RFC 8785) envelope signing

- Status: **Accepted**
- Date: 2026-02-25
- Deciders: Yudho Diponegoro

## Context

The whole protocol assumes zero-trust-everywhere. Every event must
be verifiable by every other participant with no shared server
secret. Storage must preserve the exact bytes that were signed, so
replays verify.

## Decision

- **Ed25519** for the signature algorithm.
- **JCS (RFC 8785)** for canonicalisation — UTF-8, sorted keys, no
  whitespace, shortest-form numbers.
- **Strip `{seq, ts_server, sig}` before canonicalisation.** Those
  three fields are server-assigned after the client signs.
- **Raw 32-byte pubkeys**, not COSE, not PEM, not JWK. Base64url on
  the wire.
- **PID = `base64url(sha256(pubkey)) + "@" + room_id`.** The PID
  pins a pubkey to a room; key rotation (ADR-006 eventually) swaps
  the stored pubkey while keeping the PID stable.

## Rationale

- **Ed25519.** Small keys (32B), small signatures (64B), no
  parameter-selection footguns, every modern runtime has first-class
  support.
- **JCS over alternatives (signed JSON, detached JWS).** JCS is a
  small IETF-published standard; reimplementable in any language in
  under 100 lines. JWS adds a second layer of base64url-encoded JSON
  inside JSON which makes `curl` debugging miserable.
- **Byte-for-byte storage.** `events.raw` holds exactly the bytes
  that were signed; verification is `sig.verify(raw, pubkey)` with
  no re-canonicalisation step on the read path. This closed a
  class of subtle-drift bugs we saw in early drafts.

## Consequences

- Every SDK ships a JCS canonicaliser. Three copies
  (Python/TypeScript/spec) must stay byte-identical — we enforce
  this with round-trip tests in each SDK.
- Float serialisation is touchy: we test for `1.0` → `"1"` and
  `0.1` → `"0.1"` behaviour; drift here breaks signatures silently.
- No algorithm agility today. If a quantum-safe successor is needed
  we'll gate it with `sig.alg`; right now every envelope hardcodes
  `alg: "ed25519"`.

## Alternatives considered

- **ECDSA P-256.** Larger keys/sigs, footgun malleability without
  RFC 6979, no real benefit.
- **JWS detached.** More familiar to web devs, worse to debug.
- **CBOR + COSE.** More compact but gives up `curl` debuggability.
