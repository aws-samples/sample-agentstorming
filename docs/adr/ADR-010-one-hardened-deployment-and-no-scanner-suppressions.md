<!--
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
-->

# ADR-010 — One hardened deployment profile, and no scanner suppressions

- **Status:** Accepted
- **Date:** 2026-09-07
- **Supersedes:** the deployment posture assumed by ADR-009
- **Deciders:** Yudho Ahmad Diponegoro

## Context

The repository carried nineteen inline `checkov:skip` suppressions and reported
its security posture as *"0 high excluding the registered skips"*. That claim was
challenged on the grounds that a clean scan with a nineteen-item annexe is not a
clean scan.

Re-scanning with every suppression stripped gave **37 failures across 17 rules**,
not the 19 the register acknowledged. Examining them individually produced three
findings that mattered more than the count:

1. **Seven suppressions were dead** — they covered checks that never fired.
2. **One was mislabelled.** The KMS entry claimed `Resource: "*"` in a key policy
   is resource-based so the identity-policy rules do not apply. The premise is
   correct — AWS requires `"*"` there, and a statement naming the key ARN instead
   is silently ineffective. The conclusion was not: the finding was `kms:*`
   granted to the account root with no condition. A probe appeared to confirm the
   false-positive reading and only passed because it was written with a
   single-line `principals { ... }` block, a form Checkov mis-parses into nothing.
3. **Most "cannot" justifications were "have not"** — WAF as "an operator cost
   decision", origin failover as needing "a second origin group", a backup plan
   as "an account-level concern", query logging as "a privacy regression".

## Decision

### 1. No scanner suppressions in the published tree

No `checkov:skip`, no `nosemgrep`. `scripts/make-public-tree.sh` fails a publish
if either appears. Anything that genuinely cannot be fixed is recorded in
`docs/security/policy-scan-exceptions.md`, which ships with the repository, with
the evidence attached — not in a comment only reachable by opening the file it
sits in.

Two findings remain under this rule, both `CKV_AWS_145` on an S3 log-delivery
target. They are not a rule-set contradiction: a log target carrying a CMK that
logs its own access passes both `CKV_AWS_145` and `CKV_AWS_18`, measured. The
conflict is between the rule set and AWS, which requires an SSE-S3 destination
for ALB access logs and S3 server access logs. Zero findings is reachable and
costs the audit trail, so we keep the audit trail. This matches the internal
Secure Build Path for S3, which prescribes an SSE-S3 access-logs bucket beside
SSE-KMS/CMK data buckets.

### 2. Controls are not optional

`enable_waf` and `enable_cross_region_replication` are removed rather than
defaulted to on, as is `assign_task_public_ip`. Two reasons, in ascending
importance. `count` hides the gated resources from graph-based policy analysis,
so a toggled stack cannot be *shown* to be configured correctly even when it is.
And a flag whose only effect is to disable a control is how a deployment ends up
without one while the documentation still says it has it.

### 3. One published cloud profile

Only `serverless` is published. `single-vm` stays on the internal branch.

Its instance needs a public IP, and it cannot become private without moving
certbot from HTTP-01 to DNS-01 — but that is not the reason. All three of its
ingress rules are scoped to the deployer's own address, so nobody else can reach
the room. **A protocol whose premise is many participants from many
organisations is not demonstrated by a box only its operator can open.** It is a
developer convenience. Publishing one hardened reference deployment is a stronger
artefact than publishing two and explaining which one not to copy.

### 4. KMS key policies grant administration, not use

The account-root statement in every key policy is an enumerated list of
administrative actions under a `kms:CallerAccount` condition, and contains no
data-plane actions. `kms:PutKeyPolicy` stays in the list, so the anti-lockout
property AWS's default policy provides is preserved.

The consequence is deliberate and worth stating, because it will surprise the
next person to add a consumer: **an IAM grant alone is no longer sufficient to
use a CMK.** Every consumer needs a statement in the key policy too. Dropping
`kms:*` also dropped the IAM delegation that consumers had been relying on
without anything saying so.

### 5. RDS owns the database credential

`manage_master_user_password = true`. A `random_password` resource previously
wrote the credential into Terraform state in plaintext and nothing rotated it.

## Consequences

**Cost.** The idle figure rises from roughly USD 45/month to roughly USD 110 —
mostly a NAT gateway (~$32), two web ACLs (~$16), and the replica region's
storage. `local` remains free and is the right choice for a solo experiment.
Stated in the README rather than discovered on a bill.

**Verbosity.** Key policies are longer, and the two WAFv2 ACLs are written out
rather than generated from a list, because a `dynamic` block is opaque to graph
analysis — the rules are present at apply time but a scanner reading the
configuration cannot see that `AWSManagedRulesKnownBadInputsRuleSet` is among
them. The duplication is the price of the configuration being readable by a tool
as well as a person.

**A deployer who wants the cheap shape** deletes `replication.tf` and `waf.tf`,
which is a clearer thing to ask for than setting a flag, and leaves an obvious
trace in their own scan.

## What this cost, as a record

Nineteen suppressions became two documented findings. Along the way, changes that
no scanner asked for:

- Both deployment profiles provisioned, encrypted and injected a bearer
  credential the application never reads (`Settings.admin_token`). One kept it in
  Secrets Manager under a CMK; the other wrote it into EC2 `user_data`. Removed.
- No bucket denied non-TLS access. The S3 Secure Build Path mandates it; the
  stack had it on none of six. Added.
- Aurora's security group allowed all egress to `0.0.0.0/0` — inert in the
  default VPC, a real outbound path once private subnets with a NAT route
  existed.
- Two log statements wrote LLM output, which is derived from room content, into
  application logs.
- `ec2:TerminateInstances` on `Resource: "*"` scoped only by a tag condition, in
  a script customers copy.
- Two npm packages declared `license: "MIT"` in an Apache-2.0 repository; eight
  Python manifests declared no licence; four documents including the README named
  MIT while `LICENSE` was Apache-2.0.

Every one of those was found by reading — a standard, a docstring, an AWS page —
rather than by a tool. That is the durable lesson, and the reason
`policy-scan-exceptions.md` records the fix we *declined* alongside the ones we
took.
