#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# scripts/quickstart-local.sh
#
# Laptop-one-shot: generate owner key, bring up server+postgres,
# create the `demo` room, mint six invites for the neural-experiments
# sample, then bring up the agent containers.
#
# Requires: docker (compose), python 3.11+, agentstorming-client (pip).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/6] Generate owner key (if needed)"
if ! python -m agentstorming owner print-pubkey >/dev/null 2>&1; then
  python -m agentstorming owner init-key
fi
OWNER_PUBKEY=$(python -m agentstorming owner print-pubkey)
echo "     owner pubkey: ${OWNER_PUBKEY}"

echo "[2/6] Bring up server + postgres"
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

echo "[3/6] Wait for /healthz"
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8440/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS http://localhost:8440/healthz || { echo "server didn't come up"; exit 1; }

export AGENTSTORMING_DSN="postgresql://agentstorming:${PG_PASSWORD}@127.0.0.1:5433/agentstorming"

echo "[4/6] Create the demo room"
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

echo "[5/6] Mint one invite per persona"
mint() { agentstorming-admin create-invite --room demo --kind "$1" --ttl 604800 | python -c 'import json,sys; print(json.load(sys.stdin)["invite_token"])'; }
export AGENTSTORMING_INVITE_PROJECT_LEAD=$(mint moderator)
export AGENTSTORMING_INVITE_MATHEMATICIAN=$(mint participant)
export AGENTSTORMING_INVITE_DLS=$(mint participant)
export AGENTSTORMING_INVITE_PHYS=$(mint participant)
export AGENTSTORMING_INVITE_FOURIER=$(mint participant)
export AGENTSTORMING_INVITE_NEURO=$(mint participant)
echo "     6 invites minted."

echo "[6/6] Bring up the six native-agent containers"
cd "${ROOT_DIR}/packages/native-agent/deploy/local"
docker compose --profile neural-experiments up -d --build
echo ""
echo "Done. Open http://localhost:8440/ in your browser and paste one of"
echo "the invite tokens above. Room ID is 'demo'."
