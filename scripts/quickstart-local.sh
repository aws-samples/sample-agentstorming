#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# scripts/quickstart-local.sh
#
# Laptop-one-shot: generate owner key, bring up server+postgres, create the
# `demo` room, mint an invite, then bring up the sample agent container.
#
# This brings up ONE agent, from packages/native-agent/samples/single-persona.
# That is the sample this repository ships. The six-persona research room is an
# internal overlay (docker-compose.neural-experiments.yml) whose personas are not
# published, so driving it from here would fail on a missing bind mount -- which
# is exactly what an earlier version of this script did.
#
# To add more participants, mint another invite and point a second container at
# your own persona directory; see packages/native-agent/deploy/local/README.md.
#
# Requires: docker (compose), python 3.12+, and a built workspace
# (./scripts/build-python.sh).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/5] Generate owner key (if needed)"
if ! python -m agentstorming owner print-pubkey >/dev/null 2>&1; then
  python -m agentstorming owner init-key
fi
OWNER_PUBKEY=$(python -m agentstorming owner print-pubkey)
echo "     owner pubkey: ${OWNER_PUBKEY}"

echo "[2/5] Bring up server + postgres"
cd "${ROOT_DIR}/packages/server/deploy/local"
# Reuse an existing local password so repeat runs keep the same volume;
# otherwise generate one. The compose file has no default, on purpose.
if [ -f .env ] && grep -q '^POSTGRES_PASSWORD=.' .env; then
  PG_PASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | head -1 | cut -d= -f2-)"
else
  PG_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
fi
cat > .env <<EOF
AGENTSTORMING_OWNER_PUBKEY=${OWNER_PUBKEY}
POSTGRES_PASSWORD=${PG_PASSWORD}
EOF
chmod 600 .env
docker compose up -d --build

echo "[3/5] Wait for /healthz"
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8440/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS http://localhost:8440/healthz || { echo "server didn't come up"; exit 1; }

export AGENTSTORMING_DSN="postgresql://agentstorming:${PG_PASSWORD}@127.0.0.1:5433/agentstorming"

echo "[4/5] Create the demo room"
cat > /tmp/room-demo.yaml <<'YAML'
id: demo
config:
  visibility: private
  raise_hand_required: false
  max_participants: 50
YAML
agentstorming-admin create-room --config /tmp/room-demo.yaml > /tmp/room-invites.json || {
  echo "room exists — continuing";
}
cat /tmp/room-invites.json 2>/dev/null || true

echo "[5/5] Mint invites and bring up the sample agent"
mint() { agentstorming-admin create-invite --room demo --kind "$1" --ttl 604800 | python -c 'import json,sys; print(json.load(sys.stdin)["invite_token"])'; }

# One for the agent container, one for you to join from the browser as moderator.
export AGENTSTORMING_INVITE_SMOKE=$(mint participant)
HUMAN_INVITE=$(mint moderator)

cd "${ROOT_DIR}/packages/native-agent/deploy/local"
docker compose --profile smoke up -d --build

echo ""
echo "Done. Open http://localhost:8440/ and join room 'demo' as moderator with:"
echo ""
echo "    ${HUMAN_INVITE}"
echo ""
echo "The sample agent is already in the room. Logs:"
echo "    docker compose -f packages/native-agent/deploy/local/docker-compose.yml logs -f smoke-agent"
