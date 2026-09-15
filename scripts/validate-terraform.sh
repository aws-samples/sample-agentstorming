#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# `terraform fmt -check` and `terraform validate` every Terraform directory.
#
# This exists because the serverless stack had never been validated. iam.tf
# referenced var.allowed_bedrock_model_arns, that stack never declared it, and
# `terraform validate` failed outright — meaning the sample could not be applied
# by anyone who tried it. It went unnoticed for as long as it did because
# validation was only ever run by hand, against one directory.
#
# Published infrastructure that does not validate is worse than none: a reader
# copies it, it fails, and they conclude the project does not work.
#
# `validate` needs providers, so this runs `init -backend=false` first. That
# reaches the Terraform registry; pass --offline to check formatting only.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OFFLINE=0
[ "${1:-}" = "--offline" ] && OFFLINE=1

command -v terraform >/dev/null 2>&1 || {
  echo "terraform not installed — skipping"; exit 0; }

FAILED=0
DIRS=$(find . -name '*.tf' -not -path '*/.terraform/*' -not -path '*/.venv/*' \
       -not -path '*/node_modules/*' | xargs -n1 dirname | sort -u)

for d in $DIRS; do
  printf '\n== %s\n' "$d"

  if out=$(terraform fmt -check "$d" 2>&1) && [ -z "$out" ]; then
    echo "  OK   fmt"
  else
    echo "  FAIL fmt — run: terraform fmt $d"
    echo "$out" | sed 's/^/       /'
    FAILED=1
  fi

  if [ $OFFLINE -eq 1 ]; then
    echo "  --   validate skipped (--offline)"
    continue
  fi

  if ! terraform -chdir="$d" init -backend=false -input=false -no-color \
       >/tmp/tf-init.log 2>&1; then
    echo "  FAIL init"; tail -5 /tmp/tf-init.log | sed 's/^/       /'
    FAILED=1; continue
  fi
  if out=$(terraform -chdir="$d" validate -no-color 2>&1); then
    echo "  OK   validate"
  else
    echo "  FAIL validate"; echo "$out" | sed 's/^/       /'
    FAILED=1
  fi
done

echo
[ $FAILED -eq 0 ] && echo "All Terraform directories format and validate." \
                  || { echo "Terraform problems above."; exit 1; }
