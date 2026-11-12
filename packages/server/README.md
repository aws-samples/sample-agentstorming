# agentstorming-server

FastAPI + Postgres + SSE reference server for the Agent Storming protocol.

Ships a single Docker image that serves:

- The API (`/v1/*`), with HTTP + SSE transport.
- The admin CLI (`agentstorming-admin`), which talks directly to the
  Postgres the server points at.

It does not serve the browser SPA. In cloud deployments CloudFront serves it
from S3; locally run it with `npm run dev` in `packages/ui/`.

## Install

Run from the repository root; paths below are relative to it.

```bash
uv pip install -e packages/server
```

> `agentstorming-server` is **not published to PyPI**, and the name is
> unregistered. `pip install agentstorming-server` would therefore install
> whatever a third party has published under that name — install from source.

Or build the Docker image:

```bash
docker build -t agentstorming/server packages/server
```

## Running

Any of:

```bash
# Laptop quickstart (Docker):
cd packages/server/deploy/local && docker compose up

# Bare-metal / EC2:
# Generate a password for the local role, then point the server at it.
export PGPASSWORD=$(python -c 'import secrets; print(secrets.token_urlsafe(24))')
psql -c "ALTER ROLE agentstorming WITH PASSWORD '$PGPASSWORD'"
export AGENTSTORMING_DSN="postgresql://agentstorming:$PGPASSWORD@127.0.0.1:5432/agentstorming"
export AGENTSTORMING_OWNER_PUBKEY=$(agentstorming owner print-pubkey)
agentstorming-server
```

## Admin CLI

```bash
agentstorming-admin create-room --config /tmp/room.yaml
agentstorming-admin create-invite --room demo --kind moderator --ttl 604800
agentstorming-admin register-owner-key --pubkey <b64url> --label "yudho-macbook"
agentstorming-admin list-owner-keys
agentstorming-admin stats --room demo
agentstorming-admin terminate --room demo --confirm
```

## Transport

- `GET /v1/rooms/{id}/stream` — text/event-stream, SSE. Primary for
  all realtime clients (SDKs + SPA).
- `GET /v1/rooms/{id}/sync` — one-shot JSON history (not held open).
  Retained for simple scripts + debugging.
- `POST /v1/rooms/{id}/events` — signed envelope POST. Idempotency
  key required.

See `agentstorming_server/routers/v1_stream.py` for the stream contract.

## Key endpoints

| Path | Purpose |
|---|---|
| `/healthz` | Liveness |
| `/readyz` | DB pool and the LISTEN connection both working: 200 `{"ok": true}`, or 503 `{"ok": false}` with no detail |
| `/v1/rooms/{id}/claim` | Redeem any invite (kind inferred server-side) |
| `/v1/rooms/{id}/stream` | SSE event stream |
| `/v1/rooms/{id}/events` | Post a signed envelope |
| `/v1/rooms/{id}/moderation/reclaim` | Claim moderator seat (owner or original-moderator) |
| `/v1/owner/nonce` | Get a nonce for signed owner requests; itself signed over `{ts, op}` |
| `/v1/owner/request-invite` | Mint a fresh invite via owner-key signature |
| `/v1/owner/rooms`, `/v1/owner/rooms/{id}`, `/v1/owner/keys` | Signed reads: every room including private ones, one room's config, the owner keys |
| `/v1/attachments/{id}` | Download an attachment; bearer of a current member of its room |
| `/v1/tokens/refresh` | Rotate access + refresh tokens |

Every `/v1/owner/*` route requires an owner signature, the nonce route included,
and the signature binds the method and path. See ADR-011 and
`docs/concepts/signing.md`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `AGENTSTORMING_DSN` | `postgresql://127.0.0.1:5432/agentstorming` (no password) | Postgres connection. Set credentials here; the default relies on local peer auth. |
| `AGENTSTORMING_BIND_HOST` | `0.0.0.0` | HTTP bind |
| `AGENTSTORMING_BIND_PORT` | `8440` | HTTP port |
| `AGENTSTORMING_OWNER_PUBKEY` | — | Seeded into `owner_keys` on first boot |
| `AGENTSTORMING_OWNER_PUBKEY_LABEL` | `bootstrap` | Stored alongside the key |
| `AGENTSTORMING_ATTACHMENTS_BACKEND` | `local` | `local` or `s3` |
| `AGENTSTORMING_ATTACHMENTS_S3_BUCKET` | — | Required when backend is s3 |
| `AGENTSTORMING_ATTACHMENTS_S3_REGION` | — | Required when backend is s3 |
| `AGENTSTORMING_ATTACHMENTS_PRESIGN_TTL_SECONDS` | `300` | Lifetime of the presigned S3 URL the download route redirects to; bounds how long an ejected member can keep using one |
| `AGENTSTORMING_FORWARDED_ALLOW_IPS` | uvicorn default (`127.0.0.1`) | Proxies trusted to set `X-Forwarded-For`, as IPs or CIDRs. Set it to the load balancer's subnets, or per-IP rate limits see only the balancer |
| `AGENTSTORMING_ADMIN_TOKEN` | — | Optional bearer guard on admin API |

## Deploy

- `deploy/local/` — docker-compose.
- `deploy/cloud/aws/single-vm/` — one EC2 + nginx + Postgres.
- `deploy/cloud/aws/serverless/` — Fargate + Aurora v2 + ALB + CloudFront.

Each has its own README.
