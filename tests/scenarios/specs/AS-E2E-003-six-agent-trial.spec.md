---
schema: agentstorming.test/v1
id: AS-E2E-003
title: Six LLM personas hold a substantive discussion and converge
priority: P1
tags: [native-agent, bedrock, multi-persona]
covers_spec: ["§6 turn-taking", "§11 moderator"]
deployment_mode: local
harnesses:
  project-lead:
    kind: native-agent
    persona: personas/project-lead
  mathematician:
    kind: native-agent
    persona: personas/mathematician
  deep-learning-scientist:
    kind: native-agent
    persona: personas/deep-learning-scientist
  physics-scientist:
    kind: native-agent
    persona: personas/physics-scientist
  fourier-transform-scientist:
    kind: native-agent
    persona: personas/fourier-transform-scientist
  neuron-biologist:
    kind: native-agent
    persona: personas/neuron-biologist
  director:
    kind: client-py
    role: human-director
preconditions:
  - AWS credentials with Bedrock invoke permission MUST be available in the agents' environment
  - six persona directories MUST exist at `personas/<role>/`, one per harness
    above, each a copy of `packages/native-agent/samples/single-persona/` with
    `persona.md` and `persona.yaml` edited for that role. The six-persona
    research sample this spec was written against is not published, so the
    personas are supplied by whoever runs the trial
  - a room MUST exist for the trial
  - each persona MUST hold its own invite token
  - the director (human) MUST hold a participant invite
budget:
  wall_clock_seconds: 300
  max_bedrock_usd: 2.00
  max_messages: 60
artefacts:
  - path: ./out/AS-E2E-003/transcript.md
  - path: ./out/AS-E2E-003/events.json
---

# AS-E2E-003 — Six LLM personas hold a substantive discussion

## Objective
Validate the full native-agent stack end-to-end: six Bedrock-backed
agents (Opus 4.6 moderator + 4× Sonnet 4.5 specialists + 1× Haiku 4.5
biologist) holding a bounded research discussion through SSE + signed
events + moderator synthesis.

## Background
Fresh server + a fresh room for the trial. Agents are joined but
quiet. Director has posted a kickoff message stating the problem the panel is to
solve.

## Scenario: substantive discussion emerges within budget

**Given** the director posts the kickoff prompt,

**When** all six native agents are allowed to deliberate freely,

**Then** within 5 minutes the room MUST produce at least **20**
message events from agents,
**And** at least **four** different specialist personas MUST post at
least one substantive message (≥ 50 characters of actual content, not
`<pass/>`, not auth-error boilerplate),
**And** the project-lead moderator MUST post at least **one**
synthesis message (≥ 100 characters, containing one of: "converge",
"summary", "charter", or a pinned summary update),
**And** no persona MUST post the phrase "Not logged in" or "Please
run /login" (login-error leakage regression).

## Scenario: signatures verify on every received event

**Given** the director client is consuming the SSE stream,

**When** the discussion concludes,

**Then** **every** `org.agentstorming.message` event the director
received MUST carry a valid Ed25519 signature whose pubkey matches a
`participant_joined` record the director has observed.
