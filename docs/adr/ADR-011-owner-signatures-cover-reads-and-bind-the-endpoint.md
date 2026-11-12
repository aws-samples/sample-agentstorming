<!--
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
-->

# ADR-011 — Owner signatures cover reads and bind the endpoint

- **Status:** Accepted
- **Date:** 2026-11-02
- **Amends:** ADR-004
- **Deciders:** Yudho Ahmad Diponegoro

## Context

An external security researcher reported that `GET /v1/owner/rooms` and
`GET /v1/owner/rooms/{id}` answered without an owner signature. The first listed every active room,
private ones included; the second returned a room's full configuration and its
moderator. `GET /v1/owner/keys` had the same defect and listed the owner keys
with their labels.

ADR-004 said "every *mutating* owner endpoint is a signed request", and the code
did exactly that. Each mutating handler called `_verify_owner_signed()` itself,
and the three reads simply did not. Nothing in the route table set them apart
from the signed routes beside them, and the project's own documentation already
described `GET /v1/owner/rooms` as signed. No test exercised any `/v1/owner/*`
route.

Related defects turned up in the same review:

- The signed payload was the request body minus `pubkey` and `sig`. It named
  neither the method nor the path, so endpoints with the same body shape
  accepted each other's signatures. `POST …/terminate` and `DELETE …/moderator`
  both signed `{room_id, nonce, ts}`: a signed request to vacate the moderator
  seat, intercepted before it was used, terminated the room instead.
- `GET /v1/attachments/{id}` took an `Authorization` header and never checked
  it, so an attachment ID alone downloaded the file from any room. On the S3
  backend the upload response, and so every attachment event, carried a
  presigned URL valid for seven days, which kept working after an ejection.
- `GET /v1/owner/nonce` was unsigned and rate-limited per client address.
  Behind a load balancer every caller has the balancer's address, so about 30
  anonymous requests a minute starved the owner of nonces, and every one of
  them added a row to a table nothing purged.
- The path in `op` was percent-decoded but the query was not, so a room id
  containing `?` produced the same `op` as a shorter id plus a query string.

## Decision

1. **Every owner route requires an owner signature, reads and the nonce route
   included.** The check is a FastAPI dependency on the owner router
   (`require_owner_signature`), not a call each handler has to remember. A
   route added to the owner router is signed by construction.
2. **The nonce route is signed without a nonce.** It lives alone on
   `nonce_router`, whose dependency (`require_owner_signature_without_nonce`)
   checks a signature over `{ts, op}` and rate-limits per verified key. An
   unsigned caller gets 401 and no row, so it can neither starve the owner nor
   grow the table, and the governance ticker now purges expired nonces. A
   replay inside the `ts` window is refused, since each nonce-request
   signature is accepted once. Nonces are scoped:
   the candidate registration route, which must stay anonymous, issues nonces
   that only credential collection accepts. A request's nonce is checked
   before the per-key limit is charged, so replays of a used request cannot
   empty it.
3. **The signature binds `op`**, the method and API path being called, for
   example `"POST /v1/owner/rooms/demo/terminate"`, plus `?query` when there is
   one. The client signs `op` and does not send it. The server rebuilds it from
   the request it received, so a signature verifies on one endpoint only. In
   the path, `%` is written `%25` and then `?` is written `%3F`, so the first
   `?` in `op` always starts the query.
4. **A `GET` carries the signature in headers**: `X-AgentStorming-Owner-Nonce`,
   `-Ts`, `-Pubkey` and `-Sig`, signed over `{nonce, ts, op}`. Requests with a
   body keep the ADR-004 shape, `{…, nonce, ts, pubkey, sig}`, now signed over
   the body minus `pubkey` and `sig`, plus `op`.
5. **The signature covers the raw body**, not the server's parsed model of it.
   Previously a client had to reproduce the server's defaults (`ttl_seconds:
   null`, `config: {}`) to produce a signature that verified. Because the raw
   body is signed, the endpoint models reject fields they do not know instead
   of dropping them: a signed field that changes nothing must not answer 200.
6. **Attachment downloads require a bearer** for a current member of the
   owning room, the same rule `/v1/attachments/refresh` already applied. Every
   URL the server hands out is the download route. Presigned S3 URLs come only
   from its redirect and from refresh, after the membership check, and expire
   after `attachments_presign_ttl_seconds` (300 by default).
7. **A test reads the whole route table** (`test_route_auth_inventory.py`).
   It walks nested routers and mounts, and fails on a route type it does not
   recognise. Every route must be on an explicit public list with its reason,
   or refuse an anonymous request with 401 or 403 and call the auth gate in its
   own code (or sit behind the owner signature dependency).

## Consequences

- **Breaking for owner clients.** Signatures made the ADR-004 way no longer
  verify, and the nonce must now be requested with a signature. The Python and
  TypeScript SDKs and the CLI are updated; anything else that signs owner
  requests must add `op` and sign its nonce request.
- **Breaking for attachment consumers.** Fetching an attachment needs the
  member's bearer. The Python SDK has `download_attachment()` and the MCP
  bridge `agentstorming_download_attachment`; both follow the S3 redirect
  without forwarding the bearer.
- `GET /v1/owner/rooms`, `GET /v1/owner/rooms/{id}` and `GET /v1/owner/keys`
  now have SDK functions and CLI commands (`agentstorming owner list-rooms`,
  `get-room`, `list-keys`). Before this change no client could call them signed.
- A deployment that narrows `AGENTSTORMING_CORS_ALLOW_HEADERS` must include the
  four `X-AgentStorming-Owner-*` headers for browser clients to reach owner reads.
- Behind a load balancer, set `AGENTSTORMING_FORWARDED_ALLOW_IPS` to its
  subnets so the remaining per-address limits (registration) key on the
  client. The serverless stack sets it to the VPC CIDR.
- A route is now reviewed for authentication twice: by the router it is mounted
  on, and by the inventory test, which fails for a new unauthenticated route
  until someone adds it to the public list with a reason.
