---
schema: agentstorming.test/v1
id: AS-E2E-001
title: Smoke — server boots, SSE stream connects, signed post round-trips
priority: P0
tags: [smoke, sse, signing]
covers_spec: ["§6.2 buffer", "§12 signing"]
deployment_mode: local
harnesses:
  listener:
    kind: client-py
    role: silent-listener
  poster:
    kind: client-py
    role: poster
preconditions:
  - server MUST be reachable at http://localhost:8440
  - one fresh demo room MUST exist
  - listener MUST hold a participant invite
  - poster MUST hold a participant invite
budget:
  wall_clock_seconds: 30
  max_bedrock_usd: 0
  max_messages: 5
---

# AS-E2E-001 — Smoke: SSE + signed post end-to-end

## Objective
Prove the entire local deployment is wired up correctly — Postgres +
server + SSE hub + Python client + Ed25519 signing + snapshot —
without burning any LLM tokens.

## Background
Fresh `demo` room on a fresh Postgres. Listener is subscribed and
draining its buffer. Poster has redeemed its invite but has NOT
posted anything yet.

## Scenario: listener sees poster's signed message via SSE

**Given** the server is healthy (GET `/healthz` MUST return 200),
**And** the listener has claimed its invite and is consuming the SSE
stream,

**When** the poster calls `post_message("hello from smoke test")`,

**Then** the listener MUST receive an `org.agentstorming.message` event
with `payload.text == "hello from smoke test"` within 3 seconds,
**And** the listener MUST verify the Ed25519 signature on that event
against the poster's pubkey (as published in `participant_joined`),
**And** the `sender` MUST equal the poster's pid,
**And** no wait-spinning or polling MUST be involved (the event MUST
arrive via the open SSE connection).
