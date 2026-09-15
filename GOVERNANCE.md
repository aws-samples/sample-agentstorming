# Governance

Agent Storming is an open-source protocol and reference implementation.
This document describes how the project is run today and how decisions
are made.

## Scope

The project ships:

- `docs/specification.md` — the protocol spec.
- `packages/server/` — the reference FastAPI + Postgres server.
- `packages/client-py/`, `packages/client-ts/` — the SDKs.
- `packages/client-mcp/`, `packages/client-mcp-ts/` — the MCP bridges.
- `packages/native-agent/` — the persona runtime.
- `packages/benchmark/` — the evaluation harness.
- `packages/ui/` — the reference web UI.

The protocol is the contract. The reference implementations exist to
prove the contract and give adopters a working starting point.

## Roles

- **Maintainer.** Currently a single maintainer (Yudho Diponegoro).
  Responsible for merging PRs, cutting releases, updating the spec,
  and keeping the reference implementations aligned.
- **Contributor.** Anyone who files an issue, posts a PR, or
  participates in design discussions in GitHub issues / discussions.

As the project grows we expect to move to a multi-maintainer model
and document the selection criteria then. Until then, treat the
maintainer's decisions as tentative and be prepared to revisit them
when the governance model matures.

## Decision-making

- **Spec changes.** Open an issue labelled `spec` describing the
  change, the motivation, and the proposed wording. Non-trivial spec
  changes should include an ADR in `docs/adr/` that captures the
  trade-offs.
- **Implementation changes.** Follow the standard PR flow. Breaking
  changes MUST be called out in the PR description and in
  `CHANGELOG.md`.
- **Contentious decisions.** If there is no clear consensus within a
  week of discussion, the maintainer will make a call and document
  the reasoning in the ADR.

## Release cadence

Pre-1.0 releases happen on demand — every time the spec + reference
implementation are jointly stable enough to be consumed. There is no
fixed time-based release schedule yet.

Each release MUST include:

- A dated entry in `CHANGELOG.md` listing Added / Changed / Deprecated / Removed / Fixed / Security sections as appropriate.
- A git tag of the form `v<year>.<month>.<patch>` (e.g. `v2026.05.0`).
- If the spec changed, an updated `docs/specification.md` with
  the `Stage N addendum` section refreshed.

## Security

Security vulnerabilities are reported privately — see
[SECURITY.md](./SECURITY.md). Public issues describing an unreviewed
vulnerability will be redacted.

## Conduct

All project spaces (GitHub issues, PRs, discussions, email) are
governed by [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).
