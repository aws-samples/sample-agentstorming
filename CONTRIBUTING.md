# Contributing to Agent Storming

Thanks for your interest. Two things before you file anything:

1. Read [README.md](./README.md) for the repository layout and build
   commands.
2. Read `docs/specification.md` to understand the protocol before
   proposing a server-side change.

## Workflow

1. Open an issue describing the change. For larger work, propose an
   [ADR](./docs/adr/) first.
2. Branch off `main`, push, open a PR.
3. Include tests:
   - Unit tests under `packages/<pkg>/tests/`.
   - If the change affects behaviour across clients, extend
     `tests/contract/protocol_cases.yaml`.
   - If the change is user-visible, add a scenario under
     `tests/scenarios/specs/`.
4. PR description MUST link to the issue, explain the motivation, and
   note any breaking changes.

## Local dev loop

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e packages/server -e packages/client-py -e packages/client-mcp -e packages/native-agent

pytest packages/server/agentstorming_server/tests/ packages/client-py/agentstorming_client/tests/

cd packages/ui && npm ci && npm run build
```

## Code style

- Python: `ruff check .`, `ruff format .` (line-length 100).
- TypeScript: `npm run lint` inside `packages/ui`.
- Async-first everywhere on the server side.
- No emoji in code or documentation unless the user asks.
- No unused imports, no commented-out code, no `print()`-debug leftovers.

## Security

- Never log Ed25519 private keys, refresh tokens, or invite tokens at
  a log level that ships to production.
- Never introduce a new transport without a threat-model note in the
  ADR — long-poll retired in favour of SSE in ADR-005.
- Report vulnerabilities privately per [SECURITY.md](./SECURITY.md).

## Architecture decisions

Non-trivial design choices get an ADR under `docs/adr/`:

```
docs/adr/ADR-<number>-<slug>.md
```

Use the template in `docs/adr/ADR-000-template.md`. Existing
ADRs:

- ADR-001 — HTTP long-polling (historical; superseded)
- ADR-002 — Postgres LISTEN/NOTIFY
- ADR-003 — Ed25519 + JCS signing
- ADR-004 — Owner-key bootstrap
- ADR-005 — Transport migration to SSE (replaces long polling)

## License

By contributing you agree your contributions are licensed under
the [MIT No Attribution license](./LICENSE).
