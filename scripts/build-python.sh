#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Build + install every Python package in the workspace as editable
# into a local .venv. No PyPI involved.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install it from the official documentation:" >&2
  echo "         https://docs.astral.sh/uv/getting-started/installation/" >&2
  echo "       (that installer is verified; 'pip install uv' unpinned resolves to" >&2
  echo "        whatever is current, which is not what you want for a build tool.)" >&2
  exit 2
fi

echo "[1/3] Creating .venv (python 3.12)..."
if [ ! -d .venv ]; then
  uv venv .venv --python 3.12
fi
# shellcheck disable=SC1091
. .venv/bin/activate

echo "[2/3] Installing from uv.lock (every workspace member, editable)..."
# --frozen installs exactly the tree recorded in uv.lock and does not re-resolve.
#
# The library manifests keep `>=` floors on purpose: a library that pins `==`
# cannot be co-installed with anything else that depends on the same package.
# The floors declare compatibility; uv.lock is what actually gets installed. That
# split is what makes a build reproducible without making the SDKs unusable, and
# it is why the previous `uv pip install -e ...` list is gone — it re-resolved
# every dependency on every run, so two clones of the same commit could install
# different trees.
#
# --all-packages is required because the workspace root is `package = false`.
uv sync --frozen --all-packages --group dev --python 3.12

echo "[3/3] Verifying CLIs..."
for cmd in agentstorming agentstorming-agent agentstorming-server agentstorming-admin agentstorming-mcp; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "WARN: $cmd not on PATH inside venv" >&2
  else
    echo "  ✓ $cmd"
  fi
done

echo "Python build complete."
