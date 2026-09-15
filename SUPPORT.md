<!--
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
-->

# Support

Agent Storming is a **reference implementation, not production software**, and it
is maintained on a best-effort basis. There is no SLA, and there is no AWS
Support entitlement for it — an AWS Support case will be routed back here.

Read [the security notice in the README](./README.md) and
[`docs/security/threat-model.md`](./docs/security/threat-model.md) before
deploying it anywhere that matters.

## Where to go

| You want to | Use |
|---|---|
| Report a security vulnerability | **Not** an issue — see [SECURITY.md](./SECURITY.md) |
| Report a bug | A GitHub issue, using the bug template |
| Propose a feature or protocol change | A GitHub issue, using the feature template |
| Ask how something works | A GitHub Discussion, or an issue if Discussions are off |
| Propose a change to the protocol itself | An issue first, then an ADR — see [CONTRIBUTING.md](./CONTRIBUTING.md) |

## Before opening an issue

Most reports we cannot act on are missing one of these:

1. **Which version.** A commit SHA, since nothing is published to PyPI or npm.
2. **Which deployment.** `local` (docker-compose) or `cloud/aws/serverless`.
   They fail differently.
3. **What you expected against what happened.** For protocol behaviour, quote
   the relevant part of [`docs/specification.md`](./docs/specification.md) — if
   the code and the spec disagree, that is itself the bug and worth saying.
4. **Whether signature verification is involved.** Anything touching signing,
   canonicalisation, replay or admission needs the event envelope (with the
   payload redacted if it contains anything you would not publish).

## What we will not do

- Debug a deployment we cannot reproduce from the repository.
- Accept a patch that removes a security control to make a scanner quieter. See
  [`docs/security/policy-scan-exceptions.md`](./docs/security/policy-scan-exceptions.md)
  for why two findings are deliberately left standing.
- Add a configuration flag whose only effect is to disable a control that is on
  by default.
