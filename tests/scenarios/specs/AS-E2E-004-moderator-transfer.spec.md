---
schema: agentstorming.test/v1
id: AS-E2E-004
title: Human owner claims moderator from an agent moderator
priority: P1
tags: [owner, moderator, spa, claim-moderator]
covers_spec: ["§7.5 Human Owner", "§11 moderator"]
deployment_mode: local
harnesses:
  agent-moderator:
    kind: native-agent
    persona: packages/native-agent/samples/neural-experiments/personas/project-lead
  human-owner:
    kind: spa
    role: human-owner
preconditions:
  - a fresh `demo` room MUST exist
  - the agent-moderator MUST have claimed the moderator invite and be live
  - the human-owner MUST have an owner invite token
budget:
  wall_clock_seconds: 60
  max_bedrock_usd: 0.25
  max_messages: 5
---

# AS-E2E-004 — Owner takes over moderation

## Objective
Verify the SPA's Claim Moderator button transfers the role from an
agent moderator to a human owner cleanly.

## Scenario: owner logs in and claims moderator

**Given** the SPA is open at `http://localhost:8440/`,
**And** the agent-moderator is the current acting moderator,

**When** the human-owner pastes the owner invite token and clicks
"Join room",

**Then** the login form MUST disappear,
**And** the sidebar MUST display `Affiliation: room-owner`,
**And** a "Claim moderator" button MUST be visible,
**And** the current moderator pid in the participants list MUST be
the agent-moderator's pid.

**When** the human-owner clicks "Claim moderator",

**Then** within 3 seconds an `org.agentstorming.moderator_changed`
event MUST appear in the transcript,
**And** the sidebar's moderator marker MUST move to the human-owner,
**And** the Claim Moderator button MUST disappear (user is now
moderator; nothing to claim).

## Scenario: owner can hand the moderator role back

**Given** the human-owner is currently the acting moderator,

**When** the operator calls `/v1/rooms/demo/deputies` to elevate the
agent-moderator persona to deputy_rank=1 and then hits a hypothetical
`/relinquish` endpoint (TODO: define),

**Then** the agent MUST become the acting moderator again.

> **STATUS**: this scenario is flagged for `P3` until the relinquish
> endpoint lands; marked MAY rather than MUST for now.
