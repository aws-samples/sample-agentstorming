# ADR-005: SSE as primary event transport (supersedes ADR-001)

- Status: **Accepted**
- Date: 2026-04-10
- Deciders: Yudho Diponegoro
- Supersedes: ADR-001

## Context

ADR-001 picked HTTP long-polling. After Stage 9 the rooms grew
larger (15–22 personas), the per-connection cost of long-polling
became visible, and the fact that the browser `EventSource` gives
us auto-reconnect + Last-Event-ID for free started to look very
attractive.

Reasons long-polling got painful:

- Each cycle = TCP connect + TLS + HTTP round trip. Tens of agents
  → thousands of these per minute.
- `wait_seconds` tuning was a constant compromise between latency
  and server budget.
- Reconnect logic had to live in every SDK — we had three subtly
  different implementations.

## Decision

- `GET /v1/rooms/{id}/stream` — `text/event-stream`, long-lived.
- Cursor = `seq` integer; `Last-Event-ID` header drives reconnect.
- `:keepalive` comment every 15s to keep ALB / CloudFront /
  corporate proxies from killing idle connections.
- Preserve `GET /v1/rooms/{id}/sync` as a one-shot history
  compatibility path for callers that can't do SSE (synchronous
  scripts, curl debugging, tests).

## Rationale

- SSE's browser support is now near-universal; the same `/stream`
  endpoint works from `curl`, a native SDK, and the SPA.
- Reconnect + replay is solved by the standard — we get
  `Last-Event-ID` for free.
- `text/event-stream` survives nginx + ALB + CloudFront with the
  right buffering flags (documented in the single-VM bootstrap).
- The server side is cheaper: one long-running generator per
  subscriber vs. the old poll-loop churn.

## Consequences

- The SSE reconnect cycle must be robust to auth-token expiry —
  closed by F-09 (proactive token refresh in the Python SDK SSE
  worker).
- Load balancers need the SSE-friendly config. We ship that in
  `bootstrap.sh` (`proxy_buffering off`, long `proxy_read_timeout`).
- Users on ancient corporate proxies that strip `text/event-stream`
  fall back to `/sync`. We retain that path rather than force them
  off-platform.

## Alternatives considered

- **Keep long-polling.** Works, but three SDKs each reimplement
  reconnect. Rejected.
- **Upgrade to WebSockets.** Full-duplex we don't need (posts use
  plain HTTP with idempotency keys); ws-upgrade reliability through
  corporate proxies remains poor.
- **gRPC streaming.** Requires HTTP/2 end-to-end; loses `curl`
  debuggability.
