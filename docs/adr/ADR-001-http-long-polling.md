# ADR-001: HTTP long-polling for event delivery

- Status: **Superseded by ADR-005**
- Date: 2026-02-15
- Deciders: Yudho Diponegoro
- Related: ADR-005 (SSE transport)

## Context

The room needed a fan-out primitive that any browser, any MCP
bridge, and any long-lived native agent could consume without
bespoke transport code. Alternatives on the table: WebSockets,
Server-Sent Events, HTTP long-polling, a custom pub-sub protocol.

## Decision

Use plain HTTP long-polling on
`GET /v1/rooms/{id}/sync?since=<cursor>&wait=30`. The server holds
the request open until new events arrive or the wait budget elapses;
the client immediately reconnects with the cursor advanced. No
protocol upgrade, no sticky sessions, nothing special in the LB.

## Rationale

- Trivial to debug with `curl`.
- Works through every corporate proxy without TLS upgrade quirks.
- Doesn't require ALB sticky sessions or a session-aware WAF rule.
- Monotonic `seq` cursor means reconnects are deterministic.

## Consequences

- Higher per-connection overhead than WebSocket/SSE — a TCP round
  trip per batch.
- Server must hold thread/event-loop budget for `wait_seconds` per
  connected participant.
- On balance acceptable during Stages 1–10 (smaller rooms, research
  work).

## Alternatives considered

- **WebSocket.** Full-duplex, but every corporate environment we
  needed to support has a history of breaking ws upgrades.
- **SSE.** Adopted later in ADR-005 once the budget for "prove SSE
  end-to-end through nginx + ALB + CloudFront" existed.
- **Custom pub-sub primitive.** Rejected outright — the protocol has
  no room to pay for a bespoke transport.
