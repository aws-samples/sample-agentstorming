#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Build everything from source. No PyPI, no npm registry publish.
# Produces:
#   - ./.venv/                          with every Python package installed editable
#   - packages/ui/dist/                 built SPA assets
#   - packages/client-ts/dist/          compiled TS SDK
#   - packages/client-mcp-ts/dist/      compiled TS MCP bridge
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

"$ROOT/scripts/build-python.sh"
"$ROOT/scripts/build-typescript.sh"

echo
echo "Build complete. Binaries:"
echo "  $ROOT/.venv/bin/agentstorming-server"
echo "  $ROOT/.venv/bin/agentstorming-agent"
echo "  $ROOT/.venv/bin/agentstorming"
echo "  $ROOT/.venv/bin/agentstorming-admin"
echo "  $ROOT/.venv/bin/agentstorming-mcp"
echo
echo "UI bundle: packages/ui/dist/"
echo "TS SDK:   packages/client-ts/dist/"
echo "TS MCP:   packages/client-mcp-ts/dist/"
