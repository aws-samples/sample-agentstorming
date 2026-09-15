#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Run every test in the workspace. Assumes ./scripts/build-all.sh has been run.
#
# Needs a local Postgres reachable at 127.0.0.1:5432 with an `agentstorming`
# role and an `agentstorming_test` database. Only the server tests need it; the
# rest run without one.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  echo "ERROR: .venv not found — run ./scripts/build-python.sh first." >&2
  exit 2
fi
# shellcheck disable=SC1091
. .venv/bin/activate

# No password in the default: local Postgres is reached over peer/trust auth,
# and a literal one here is a password every reader of the repository has. Set
# AGENTSTORMING_TEST_DSN (or PGPASSWORD) if your local instance wants one.
export AGENTSTORMING_TEST_DSN="${AGENTSTORMING_TEST_DSN:-postgresql://agentstorming@127.0.0.1:5432/agentstorming_test}"

# Every package that has tests. Kept explicit rather than discovered so that a
# new package without tests is a visible omission here, not a silent pass.
# storm-broker's tests/ is not a package and relies on rootdir-relative
# imports, so it gets its own invocation.
PYTEST_PATHS=(
  packages/server/agentstorming_server/tests
  packages/client-py/agentstorming_client/tests
  packages/native-agent
)

echo "[1/4] Python unit + integration tests"
psql -h 127.0.0.1 -U agentstorming -d agentstorming_test \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" 2>/dev/null || true
pytest "${PYTEST_PATHS[@]}" -q

echo
echo "[2/4] storm-broker + shim tests"
pytest packages/storm-broker packages/storm-broker-shim -q

echo
echo "[3/4] Scenario spec validation"
python tests/scenarios/runner.py validate

echo
echo "[4/4] End-to-end against a live server"
# These need a running server, so they are conditional rather than skipped
# silently: a suite that quietly omits its only end-to-end coverage is how you
# end up believing a transport works because 210 unit tests pass.
BASE="${AGENTSTORMING_BASE_URL:-http://127.0.0.1:8440}"
if curl -fsS --max-time 3 "$BASE/readyz" >/dev/null 2>&1; then
  echo "  server reachable at $BASE"
  AGENTSTORMING_BASE_URL="$BASE" python tests/scenarios/runner.py run AS-E2E-001
  AGENTSTORMING_BASE_URL="$BASE" python scripts/e2e-agentcore.py
else
  echo "  SKIPPED — no server at $BASE"
  echo "  To include end-to-end coverage, start one first:"
  echo "    export AGENTSTORMING_DSN=postgresql://agentstorming@127.0.0.1:5432/agentstorming_test"
  echo "    export AGENTSTORMING_ATTACHMENTS_LOCAL_DIR=\$(mktemp -d)"
  echo "    export AGENTSTORMING_OWNER_PUBKEY=\$(agentstorming owner print-pubkey)"
  echo "    agentstorming-server &"
fi

echo
echo "All tests passed."
