#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Build every TypeScript workspace member from source.
# No npm registry publish — just local tsc compilation.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not found (need Node 22+)." >&2
  exit 2
fi

build_pkg() {
  local dir="$1"
  echo "[build] $dir"
  (
    cd "$ROOT/$dir"
    # Use public registry — override any workspace-wide CodeArtifact .npmrc.
    if [ ! -f .npmrc ]; then
      echo 'registry=https://registry.npmjs.org/' > .npmrc
    fi
    # `npm ci` installs exactly the tree in package-lock.json. `npm install`
    # is free to re-resolve within the declared caret ranges and rewrite the
    # lock file, which makes a committed lock file decorative. The ranges stay
    # carets deliberately — pinning package.json exactly reintroduced three npm
    # advisories, because the carets were already resolving past them — so the
    # lock file is the only thing pinning the tree, and it has to be enforced.
    if [ -f package-lock.json ]; then
      npm ci --no-fund --no-audit --loglevel=error
    else
      npm install --no-fund --no-audit --loglevel=error
    fi
    npm run build
  )
}

build_pkg "packages/client-ts"
# client-mcp-ts depends on client-ts locally (file:../client-ts).
build_pkg "packages/client-mcp-ts"
build_pkg "packages/ui"

echo "TypeScript build complete."
echo "  packages/client-ts/dist/"
echo "  packages/client-mcp-ts/dist/"
echo "  packages/ui/dist/"
