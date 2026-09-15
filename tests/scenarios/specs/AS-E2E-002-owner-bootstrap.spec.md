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
