# Agent Storming test-case conventions

A cross-tool natural-language test language for end-to-end scenarios.
Composed from three existing standards:

1. **ISO/IEC/IEEE 29119-3** test-case attribute schema (YAML frontmatter).
2. **Gherkin** (Given / When / Then / And / But) step grammar.
3. **RFC 2119** normative keywords (MUST / MUST NOT / SHOULD / SHOULD NOT / MAY) in assertions.

Test cases live in Markdown files (`*.spec.md`). Any coding agent
(Claude Code, Cursor, Codex, Aider, Kiro, Windsurf) can read and
execute them. The runner composes a concrete environment — spawning
server + clients from the harnesses indicated in the front-matter —
and the agent walks the Gherkin steps, calling MCP tools
(`@playwright/mcp` for browser steps, `@agentstorming/mcp` for API steps).

## Example

```markdown
---
schema: agentstorming.test/v1
id: AS-E2E-007
title: Moderator interviews a registering candidate
priority: P1
tags: [moderator, registration, interview]
covers_spec: ["§8.3", "§9.3"]
deployment_mode: local
harnesses:
  moderator: { kind: native-agent, persona: .../project-lead }
  candidate: { kind: mcp, via: kiro-cli, prompt_file: ./prompts/rl-candidate.md }
  observer:  { kind: client-py, role: silent-listener }
preconditions:
  - server MUST be reachable at the local URL
  - moderator MUST hold a valid moderator invite
  - candidate MUST NOT yet be a room member
budget:
  wall_clock_seconds: 180
  max_bedrock_usd: 0.50
  max_messages: 40
---

# AS-E2E-007 — Moderator interviews a registering candidate

## Objective
...

## Scenario: candidate is accepted after adequate interview

**Given** a fresh `interview-room` and the moderator is idle,
**And** the observer client is subscribed,

**When** the candidate calls register with kind=participant,
**And** the moderator processes the registration_request,
**And** the moderator and candidate exchange ≥ 3 interview messages,
**And** the moderator calls acceptCandidate,

**Then** the candidate MUST be admitted as `member`,
**And** the observer MUST NOT see any interview message on the public transcript,
**And** a `registration_accepted` event MUST appear within 3 seconds,
**And** wall-clock MUST NOT exceed 180 seconds.
```

## Front-matter schema (agentstorming.test/v1)

| Field | Required | Type | Meaning |
|---|:---:|---|---|
| `schema` | yes | string | Always `agentstorming.test/v1` |
| `id` | yes | string | Stable test id (e.g. `AS-E2E-001`) |
| `title` | yes | string | Human-readable title |
| `priority` | yes | P0/P1/P2/P3 | P0=blocker, P1=release, P2=nightly, P3=manual |
| `tags` | no | list[string] | Free-form categorisation |
| `covers_spec` | no | list[string] | Spec sections this test validates |
| `requires_features` | no | list[string] | Feature flags / commits / PRs |
| `deployment_mode` | yes | enum | `local` \| `aws-single-vm` \| `aws-serverless` \| `any` |
| `harnesses` | yes | map | Role → `{kind, persona?, via?, prompt_file?}` |
| `preconditions` | no | list[string] | MUST/SHOULD preconditions |
| `budget` | no | map | Caps: `wall_clock_seconds`, `max_bedrock_usd`, `max_messages` |
| `artefacts` | no | list | Output paths the runner should collect |

## Harness kinds

| Kind | What it spawns |
|---|---|
| `native-agent` | `agentstorming-agent start --persona <path>` |
| `client-py` | Raw `agentstorming_client.StormClient` process |
| `client-ts` | Raw `@agentstorming/client` Node process |
| `mcp` | Coding-agent CLI (`claude`, `kiro`, `codex`, `aider`) with `@agentstorming/mcp` configured |
| `spa` | Playwright-driven browser session |
| `strands` | Strands agent using `agentstorming_client.StormClient` |
| `crewai` | CrewAI crew |
| `langgraph` | LangGraph graph |

## Gherkin step grammar

Use the standard keywords:

- `Given` — preconditions / setup
- `When` — the action under test
- `Then` — expected outcomes
- `And` / `But` — continuation of the previous keyword

Assertions inside `Then`/`And` MUST use RFC 2119 keywords when the
expectation is normative: `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`,
`MAY`. Test runners fail only on `MUST` / `MUST NOT` violations;
`SHOULD` failures are reported as warnings.

## Result format

Runners emit both:

- **TAP 14** to stdout (for streaming dashboards).
- **JUnit XML** at `./out/<id>.junit.xml` (for CI / GitHub Actions
  test reporting).

Both formats are standard; no Agent Storming-specific parser is needed
for result consumption.

## Layered test pyramid

This file describes **scenario tests** (Layer 4). Two lower layers
serve specific roles:

- **Layer 2 — contract tests** (`tests/contract/`): a single
  `protocol_cases.yaml` exercised by per-language runners (Python,
  TypeScript, MCP, native-agent). Ensures every SDK speaks the
  protocol identically.
- **Layer 3 — per-integration tests** (`tests/integration/`): one
  subdir per host framework (Strands, CrewAI, LangGraph, Pydantic
  AI, Claude Code, SPA, MCP, ...). Catches ecosystem-drift bugs.

Unit tests for each package stay inside that package's `tests/` dir.
Only genuinely cross-package assertions live under `tests/`.

## What NOT to put in tests/scenarios/

- **Brittle wording assertions.** Do not assert on "the agent said
  something polite" — use structured event counts, affiliation
  transitions, and numeric thresholds. Reserve LLM-as-judge for cases
  where deterministic assertions are insufficient, and always pair
  with a hard structural check.
- **Unbounded scenarios.** Every scenario MUST have a wall-clock +
  dollar-cost budget in its front-matter. The runner enforces them.
- **Stateful side effects between scenarios.** Each scenario brings
  up a fresh server + fresh Postgres. No shared state.

## See also

- `tests/schemas/agentstorming.test.v1.json` — JSON Schema for the
  front-matter (validated by the runner).
- `tests/scenarios/specs/` — canonical examples.
- `dev/designs/testing.md` — deeper design rationale for this test
  language and the layered pyramid.
