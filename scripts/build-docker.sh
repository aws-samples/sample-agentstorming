#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Build local Docker images for the server + native-agent.
# No registry push — tags stay local.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Accept either docker or finch.
DOCKER="${DOCKER:-docker}"
if ! command -v "$DOCKER" >/dev/null 2>&1; then
  if command -v finch >/dev/null 2>&1; then
    DOCKER=finch
  else
    echo "ERROR: neither docker nor finch is installed." >&2
    exit 2
  fi
fi

echo "Using container engine: $DOCKER"

echo "[1/2] Building agentstorming/server:local..."
"$DOCKER" build -t agentstorming/server:local -f "$ROOT/packages/server/Dockerfile" "$ROOT/packages/server"

echo "[2/2] Building agentstorming/agent:local (context = repo root, needed by COPY packages/client-py)..."
"$DOCKER" build -t agentstorming/agent:local -f "$ROOT/packages/native-agent/Dockerfile" "$ROOT"

echo "Docker build complete. Images:"
"$DOCKER" images | grep -E 'agentstorming/(server|agent)' | head
