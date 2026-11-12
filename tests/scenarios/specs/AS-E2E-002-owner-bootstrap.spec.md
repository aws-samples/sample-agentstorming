---
schema: agentstorming.test/v1
id: AS-E2E-002
title: Owner key bootstrap and signed invite-request flow
priority: P0
tags: [owner, bootstrap, signing]
covers_spec: ["§7.5 Human Owner", "§12 signing"]
deployment_mode: local
harnesses:
  operator:
    kind: client-py
    role: owner-cli
preconditions:
  - a fresh server MUST have been started with AGENTSTORMING_OWNER_PUBKEY set to the operator's pubkey
  - the operator's private key MUST be on disk at ~/.config/agentstorming/owner.key
  - a `demo` room MUST exist
budget:
  wall_clock_seconds: 30
  max_bedrock_usd: 0
  max_messages: 0
---

# AS-E2E-002 — Owner key bootstrap + signed invite request

## Objective
Validate the owner-key bootstrap and the signed-request flow a deployer
uses to mint fresh invites without SSH / console access.

## Scenario: owner mints a fresh participant invite via signed request

**Given** the server is running with the operator's pubkey seeded in
`owner_keys`,

**When** the operator runs `agentstorming owner request-invite
--base-url http://localhost:8440 --room demo --kind participant`,

**Then** the CLI MUST return a JSON `{invite_token, kind, room_id,
expires_at}` with `kind == "participant"` and `room_id == "demo"`,
**And** the invite token MUST be redeemable exactly once by a fresh
client (using it twice MUST fail with HTTP 409).

## Scenario: replay of the same nonce is rejected

**Given** a successfully-consumed nonce from the previous scenario,

**When** the operator replays the same signed request body,

**Then** the server MUST reject with HTTP 409 and
`code: org.agentstorming.err.nonce_invalid`.

## Scenario: unknown pubkey is rejected

**Given** an Ed25519 keypair that has NOT been registered in
`owner_keys`,

**When** a signed request using that key is sent to
`/v1/owner/request-invite`,

**Then** the server MUST reject with HTTP 403 and
`code: org.agentstorming.err.owner_unknown`.

## Scenario: an unsigned owner read is refused

**Given** a `demo` room that is private,

**When** any client sends `GET /v1/owner/rooms`, `GET /v1/owner/rooms/demo`
or `GET /v1/owner/keys` without the `X-AgentStorming-Owner-*` headers,

**Then** the server MUST reject each with HTTP 401 and
`code: org.agentstorming.err.owner_auth_required`,
**And** the responses MUST NOT contain the room id, its title, or an owner key.

## Scenario: the owner's signed reads succeed

**Given** the server is running with the operator's pubkey seeded,

**When** the operator runs `agentstorming owner list-rooms --base-url
http://localhost:8440`,

**Then** the CLI MUST return every active room, private ones included,
**And** `agentstorming owner list-keys` MUST return the operator's key.

## Scenario: a signature only verifies on the endpoint it was made for

**Given** a valid signature made for `DELETE /v1/owner/rooms/demo/moderator`,

**When** the same signed body is sent to `POST /v1/owner/rooms/demo/terminate`,

**Then** the server MUST reject it with HTTP 403 and
`code: org.agentstorming.err.sig_invalid`,
**And** the room MUST still be `ACTIVE`.

## Scenario: an unsigned nonce request is refused

**Given** the server is running with the operator's pubkey seeded,

**When** any client sends `GET /v1/owner/nonce` without the
`X-AgentStorming-Owner-Ts`, `-Pubkey` and `-Sig` headers,

**Then** the server MUST reject it with HTTP 401 and
`code: org.agentstorming.err.owner_auth_required`,
**And** the response MUST NOT contain a nonce,
**And** after 100 such requests the operator's `agentstorming owner
list-rooms` MUST still succeed.

## Scenario: a signed PATCH that names an unknown field is refused

**Given** a `demo` room and a valid owner signature over
`{room_id: "demo", no_such_field: true}` for `PATCH /v1/owner/rooms/demo`,

**When** that body is sent,

**Then** the server MUST reject it with HTTP 422,
**And** a signed PATCH of `{room_id: "demo", raise_hand_required: true}`
MUST return the room with `config.raise_hand_required == true`.
