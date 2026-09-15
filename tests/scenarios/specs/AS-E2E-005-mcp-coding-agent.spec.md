---
schema: agentstorming.test/v1
id: AS-E2E-005
title: Coding agent joins the room via @agentstorming/mcp
priority: P1
tags: [mcp, coding-agent, kiro, claude-code]
covers_spec: ["§13.5 Storm Client in interactive CLIs"]
deployment_mode: local
harnesses:
  agent-moderator:
    kind: native-agent
    persona: packages/native-agent/samples/single-persona
  coding-agent:
    kind: mcp
    via: kiro-cli
    prompt_file: tests/prompts/join-and-ask.md
preconditions:
  - a fresh `demo` room MUST exist
  - the agent-moderator MUST be live
  - the coding-agent harness MUST have @agentstorming/mcp configured in its MCP config file
  - the coding-agent harness MUST have network access to http://localhost:8440
budget:
  wall_clock_seconds: 240
  max_bedrock_usd: 1.00
  max_messages: 20
---

# AS-E2E-005 — Coding agent participates via MCP bridge

## Objective
Prove that a headless coding-agent CLI (Kiro-CLI, Claude Code,
Codex, Aider) can use the `@agentstorming/mcp` server to join a Storm
room, post a message, and react to replies.

## Scenario: coding agent joins, posts, reads, posts again

**Given** the `@agentstorming/mcp` server is configured in the coding
agent's MCP config (Claude Code: `.mcp.json`; Kiro: `.kiro/mcp.json`;
Codex: `.codex/config.toml`; ...),

**When** the coding-agent harness runs the prompt at
`tests/prompts/join-and-ask.md` in headless mode (`kiro-cli --prompt
<file>` or equivalent),

**Then** the coding-agent MUST invoke the MCP tool `join_room` with
the demo room URL + a participant invite,
**And** the coding-agent MUST post exactly one question message to
the room,
**And** the agent-moderator MUST post a synthesis in reply within 60
seconds,
**And** the coding-agent MUST invoke `check_buffer` or `get_room_state`
at least once,
**And** the coding-agent's post MUST NOT be empty, MUST NOT be
`<pass/>`, and MUST NOT be the MCP tool description text.
