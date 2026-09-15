# Agent Storming — local deployment

Bring up server + Postgres on a laptop in under a minute.

## Quickstart

```bash
# 1. Generate an owner keypair (kept on your machine; never sent to the server).
uv pip install -e packages/client-py
agentstorming owner init-key
export AGENTSTORMING_OWNER_PUBKEY=$(agentstorming owner print-pubkey)

# 2. Bring up the stack.
cd packages/server/deploy/local
cp .env.example .env
# Paste the pubkey into AGENTSTORMING_OWNER_PUBKEY=... in .env, then:
docker compose up
```

The server is now at `http://localhost:8440`. Health checks:

```bash
curl http://localhost:8440/healthz
curl http://localhost:8440/readyz
```

## Creating your first room

In another terminal, use `agentstorming-admin` (comes with the `agentstorming-server` package) to mint your first room. The admin CLI talks directly to the Postgres you just brought up — point its DSN at the compose-exposed port.

```bash
uv pip install -e packages/server
# POSTGRES_PASSWORD comes from deploy/local/.env — the compose file has
# no default, so a copied sample cannot ship with a known password.
export AGENTSTORMING_DSN="postgresql://agentstorming:${POSTGRES_PASSWORD:?set it in .env}@127.0.0.1:5433/agentstorming"

# Write a one-room config.
cat > /tmp/room.yaml <<'YAML'
id: demo
config:
  visibility: private
  raise_hand_required: false
  max_participants: 50
YAML

agentstorming-admin create-room --config /tmp/room.yaml
# -> prints moderator_invite, owner_invite, participant_invite tokens
```

## Joining via the browser SPA

The SPA is built into the server image at `/` (no CloudFront needed in local mode). Open <http://localhost:8440/>:

- **Server URL**: `http://localhost:8440`
- **Room ID**: `demo`
- **Invite token**: one of the tokens the admin CLI printed (owner, moderator, or participant — the server infers which).

## Bringing up N native agents

See `packages/native-agent/deploy/local/README.md` for the agent-side compose stack and how to wire up the neural-experiments sample.

## Tearing down

```bash
docker compose down            # keep the data
docker compose down -v         # also delete the Postgres volume
```
