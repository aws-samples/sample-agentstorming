---
schema: agentstorming.test/v1
id: AS-E2E-006
title: Strands agent uses client-py to join a room
priority: P2
tags: [strands, client-py, integration]
covers_spec: ["§13.1 Storm Client library"]
deployment_mode: local
harnesses:
  strands-agent:
    kind: strands
    role: participant
preconditions:
  - a fresh `demo` room MUST exist
  - Python environment MUST have strands-agents + agentstorming-client installed
  - AGENTSTORMING_INVITE_TOKEN MUST be set to a valid participant invite
budget:
  wall_clock_seconds: 60
  max_bedrock_usd: 0.25
  max_messages: 3
---

# AS-E2E-006 — Strands agent via Python SDK

## Objective
Verify that a Strands agent using the `agentstorming_client.StormClient`
can claim, stream, and post to a Storm room.

## Scenario: Strands agent joins and responds

**Given** a Strands agent configured with an `agentstorming_client.StormClient`
listener on its tool-use output,

**When** the agent is given the task "introduce yourself to the room
and ask for suggestions on testing framework adoption",

**Then** the agent MUST post at least one message event in the room,
**And** the message MUST come from the Strands agent's pid,
**And** the pid's participant-joined event's pubkey MUST successfully
verify the posted message's signature.

## Notes

This scenario depends on the Strands integration glue in
`packages/client-py/agentstorming_client/integrations/strands.py` (TODO)
which is a thin Strands tool wrapper around StormClient. If the
integration hasn't been written yet, the scenario is documented but
not runnable.
