# ADR-004: Owner-key bootstrap for room + server administration

- Status: **Accepted**
- Date: 2026-03-02
- Deciders: Yudho Diponegoro

## Context

Room creation, termination, moderator-seat management, and
multi-key owner rotation all need strong authentication. Requiring
a bearer token minted by the server defeats the point (whoever
holds the token owns the server). We need a primitive that ties
admin authority to an off-server private key.

## Decision

- **Server boot seeds an owner pubkey** from
  `AGENTSTORMING_OWNER_PUBKEY` into the `owner_keys` table (only if
  the table is empty — first-write wins).
- **Every mutating owner endpoint is a signed request.** Body carries
  `{nonce, ts, pubkey, sig, ...endpoint-specific fields}`.
- **Server verifies:**
  1. pubkey exists in `owner_keys` and is active,
  2. ts is within ±5 minutes of server clock,
  3. nonce was minted by `GET /v1/owner/nonce` within the last 60s
     and hasn't been consumed,
  4. sig verifies over JCS-canonicalised `(body minus pubkey+sig)`.
- **Owner keys are plural.** `POST /v1/owner/keys` (signed by an
  existing owner key) adds another pubkey; revocation is also a
  signed request. The last active owner key cannot be revoked.

## Rationale

- Ties administration authority to a hardware-friendly primitive
  (Yubikey, iCloud Keychain, KMS key, HSM). No shared secret.
- Nonce-binding makes replay attacks impossible even if the signed
  request is captured.
- Plural owner keys enable smooth key rotation and multi-operator
  deployments without building a separate user table.

## Consequences

- Every owner SDK method needs to call `/v1/owner/nonce` first. One
  extra round-trip per admin action.
- Client must canonicalise the body before signing — the Python and
  TypeScript SDKs both implement this. Drift would silently break
  all owner endpoints.
- If an operator loses their last owner private key with no backup,
  the server has no recovery path. We document this loudly in
  `SECURITY.md`.

## Alternatives considered

- **Static admin bearer token** (`AGENTSTORMING_ADMIN_TOKEN`). Kept
  as a legacy path for ops scripts but demoted — cannot do
  room-mutating actions. Still gates `/admin/*` diagnostic endpoints.
- **OIDC / Cognito.** Overkill for a single-operator deployment; we
  want a system that runs fine on a Raspberry Pi behind Tailscale.
- **Hardcoded public key baked into the binary.** Rejected — no
  rotation story.
