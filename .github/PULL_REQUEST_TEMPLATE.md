<!--
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
-->

**What this changes**

**Why**

## Checklist

- [ ] `pytest packages/ tests/` passes
- [ ] `python tests/scenarios/runner.py validate` passes
- [ ] `./scripts/validate-terraform.sh` passes, if any `.tf` changed
- [ ] Every new source file carries the Apache-2.0 SPDX header
- [ ] No new scanner suppression. If a finding cannot be fixed, it goes in
      `docs/security/policy-scan-exceptions.md` with the evidence, not in a
      `checkov:skip` or `nosemgrep` comment
- [ ] If the protocol changed: `docs/specification.md` updated **and** an ADR
      added under `docs/adr/`
- [ ] If a security control changed: no new variable whose only effect is to
      turn it off

## Anything a reviewer should distrust

<!-- Optional, and the most useful box here. If part of this is unverified, only
     tested one way, or you are unsure about it, say so. A reviewer who knows
     where to look is worth more than a confident description. -->
