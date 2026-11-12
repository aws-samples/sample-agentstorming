<!--
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
-->

# Policy-scan exceptions

This repository is scanned with [Checkov](https://www.checkov.io/) for
infrastructure, and with Bandit, Semgrep, detect-secrets, grype, npm audit,
cfn-nag, cdk-nag and syft via
[ASH](https://github.com/awslabs/automated-security-helper) for everything else.

The published tree contains **no Checkov or Semgrep suppressions**: no
`checkov:skip` and no `nosemgrep`, and the export script fails a publish if it
finds either. An earlier revision said this while two `# nosemgrep` comments sat
in a test fixture and the export checked only for `checkov:skip`; both comments
are gone and the export now checks for both. The `# nosec` comments are the
exception, inventoried below.

Everything that is not fixed is recorded here rather than left as a comment in a
file, because a suppression is invisible unless you are already reading the line
it sits on.

## Summary

| Scanner | Critical | High | Medium | Low |
|---|---|---|---|---|
| Checkov 3.4.0 (all frameworks) | 2 | 0 | 0 | 0 |
| Bandit 1.9.4 (tests included) | 0 | 0 | 0 | 737 |
| Bandit 1.9.4 **`--ignore-nosec`** | 0 | **0** | **17** | 742 |
| Semgrep / opengrep | 0 | 0 | 0 | 0 |
| grype, npm audit, syft | 0 | 0 | 0 | 0 |
| cfn-nag, cdk-nag | 0 | 0 | 0 | 0 |

The two Checkov findings are the S3 log-delivery targets described below. Checkov's
*passed* count is version-dependent, because newer releases add rules: 763 under
3.2.484, 889 under 3.3.16, and 890 under 3.4.0, which adds CKV_AWS_394 (pin the
Availability Zones). So it is quoted with a version or not at all. The failed
count is 2 under all three. The
Bandit lows are `assert` in tests (B101), `random` for non-cryptographic
jitter (B311), and `try/except/pass` in teardown paths (B110/B112); none is a
credential or an injection path. The low count grows with the test suite, since
most of it is B101, so it is quoted with the tool version and nothing else is
inferred from it.

**Read the two Bandit rows together, and prefer the second.** The first honours
the `# nosec` comments in the tree; the second discards all of them. The number
that matters is the same in both: **0 HIGH, with or without suppressions.** The
medium count is not — it is 0 only because seventeen findings carry a `# nosec`.
An earlier revision of this document, and of the review tickets that cite it,
said the tree contained *zero inline scanner suppressions*. That was true of
`checkov:skip` and of `nosemgrep`, and false of `# nosec`. The inventory below
exists so the claim is checkable rather than taken on trust, and CI now asserts
`bandit -lll --ignore-nosec` so the meaningful guarantee cannot be manufactured
by annotating a finding away.

### The seventeen suppressed Bandit mediums

Counted on the **published** tree, which is the tree that matters. An earlier
revision of this table said sixteen, of which fifteen were said to ship, on the
reasoning that the single `B310` sat in `samples/neural-experiments/` and the
export excludes that directory. Both halves were wrong: `scripts/e2e-agentcore.py`
carries **two more** `B310` findings and `scripts/` does ship, so the published
figure is higher than the internal one the table was describing, not lower. Every
row below was re-counted against a real `bandit 1.9.4 --ignore-nosec` run of the
exported tree rather than carried forward.

| Test | Count | Where | Why it is suppressed rather than fixed |
|---|---|---|---|
| B108 `hardcoded_tmp_directory` | 11 | test fixtures in 5 packages | The tests create AF_UNIX sockets. macOS caps `sun_path` at 104 bytes and pytest's `tmp_path` alone exceeds it, so the fixtures must use a short base. `tempfile.gettempdir()` returns the long `/var/folders/...` path on macOS, so it is not a substitute. Test-only; no shipped code path. |
| B104 `hardcoded_bind_all_interfaces` | 2 | `server/config.py`, `native-agent/agentcore_app.py` | Binding all interfaces is the intent: the process always sits behind nginx, an ALB, or the AgentCore proxy, which is what restricts reachability. Narrow it with a security group, not by guessing an interface. |
| B103 `set_bad_file_permissions` | 1 | `storm_broker/server.py` | `chmod 0o660` on the broker socket. Group access is the mechanism by which the agent process reaches the broker while other users cannot. |
| B608 `hardcoded_sql_expressions` | 1 | `server/repo/events.py` | The only interpolated values are literal predicate strings and `$N` placeholder *indices* generated from an integer counter. Every value goes through an asyncpg parameter. Verified by reading each branch. |
| B310 `blacklist` (urlopen) | 2 | `scripts/e2e-agentcore.py` lines 84, 92 | Both URLs are built from a constant `f"http://127.0.0.1:{AGENT_PORT}{path}"` — fixed scheme, loopback host, no caller-supplied component reaches either. B310 fires on the *possibility* of a `file://` or `ftp://` scheme, which cannot arise from a literal `http://` prefix. This is a local end-to-end harness, not a shipped code path. |
| B310 `blacklist` (urlopen) | *(1, does not ship)* | `samples/.../_safe_http.py` | Scheme is allowlisted by `check_url` before the call. The export excludes `samples/neural-experiments/`, so this one is absent from the published tree and is **not** counted in the seventeen above. |

None of these is fixable without either breaking the thing it protects or
rewriting the pattern purely to evade the match, which would leave the same
behaviour with a quieter report. That is the distinction this document draws
throughout: a suppression with a reason is acceptable, a suppression that hides
a real defect is not, and the nineteen Checkov ones removed earlier were mostly
the second kind.

## The Checkov exceptions

| Check | Resource | Status |
|---|---|---|
| `CKV_AWS_145` — buckets encrypted with KMS by default | `aws_s3_bucket.logs` | Cannot be satisfied |
| `CKV_AWS_145` — buckets encrypted with KMS by default | `aws_s3_bucket.logs_replica` | Cannot be satisfied |

Both are the S3 log-delivery target — one per region.

## Why they cannot be satisfied

A bucket that receives ALB access logs or S3 server access logs must use
SSE-S3. AWS states this in two places:

> The only server-side encryption option that's supported is Amazon S3-managed
> keys (SSE-S3).
>
> — [Enable access logs for your Application Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html)

> The destination bucket must use Amazon S3 managed keys (SSE-S3). If the
> destination bucket uses SSE-KMS, Amazon S3 might deliver log objects that are
> encrypted with a key that you can't access.
>
> — [Enabling Amazon S3 server access logging](https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-server-access-logging.html)

Meanwhile `CKV_AWS_18` requires every bucket to have access logging, which needs
a target bucket, and `CKV_AWS_91` requires the ALB to log to S3.

**Be precise about what kind of conflict this is.** An earlier version of this
page said "there is no configuration that satisfies both; the conflict is in the
rule set". That was wrong, and the correction matters. Checkov *is* satisfiable
here: a log-delivery target that carries a CMK and logs its own access to itself
passes `CKV_AWS_145` and `CKV_AWS_18` together — we built it and measured it, and
the only check it fails is `CKV_AWS_144`, which replication already answers.

So the conflict is not between two rules. It is between the rule set and AWS.
Checkov would accept the configuration; S3 and ELB would stop delivering logs
into it. These two findings exist because we would rather have the logs.

Two buckets rather than one because S3 server access logging requires the target
to be in the same region as the source, so the replica region needs its own.

## This is the prescribed design, not a deviation from it

The two-bucket split here — an SSE-S3 access-logs bucket plus SSE-KMS/CMK data
buckets — is not a compromise we invented to work around the AWS constraint. It
is the design AWS's own guidance prescribes.

AWS Prescriptive Guidance's published *Enterprise Audit and Assessment* pattern
carries a Checkov disposition table for its own logging bucket which records
`CKV_AWS_19` (encryption), `CKV_AWS_21` (versioning) and `CKV_AWS_18` (access
logging) against that bucket as not applicable — the same checks, on the same
kind of resource, dispositioned the same way.

So a reviewer seeing `CKV_AWS_145` on a log-delivery target is seeing what this
recommended pattern looks like when a scanner reads it, rather than a stack that
skipped a control.

Two intentional deviations, both in the stricter direction:

- Versioning is **enabled** on the log buckets. The guidance leaves it off for
  log buckets; `CKV_AWS_21` wants it on, and it costs nothing.
- The log buckets **log their own access**, to themselves, under a prefix that
  expires in 7 days. The guidance treats access logging as not applicable for a
  log bucket; `CKV_AWS_18` wants it, and a bounded self-log satisfies both.

## Why two, and not fewer

Measured against the alternatives rather than asserted:

| Design | Findings | Why |
|---|---|---|
| **This stack** — two regions, both log targets SSE-S3 | **2** | `CKV_AWS_145` on each log target |
| Single region, no replication | 4 | `CKV_AWS_144` ×3 (attachments, SPA, logs) + `CKV_AWS_145` ×1 |
| Drop ALB access logs | 2+ | trades `CKV_AWS_145` for `CKV_AWS_91` on the load balancer |
| Drop bucket access logging entirely | 6+ | `CKV_AWS_18` on every bucket |
| Give the log targets CMKs | **0** | and silently stops log delivery |

Two is the floor for a cross-region design that actually works. Zero is
available, and costs the audit trail.

## The fix we did not apply

Setting `sse_algorithm = "aws:kms"` without a key ID **does** make `CKV_AWS_145`
pass. We tested it. It also breaks log delivery, per the second quotation above.

That option is recorded here on purpose. It is the obvious way to turn this page
into a clean scan report, it would not be caught by any scanner, and the first
symptom would be an empty log prefix noticed weeks later during an
investigation. If a future change makes these two findings disappear, check that
this is not how.

## What is compensating

- Both buckets have SSE-S3 encryption, versioning, a public-access block,
  `BucketOwnerEnforced` ownership, a bucket policy restricting writes to the two
  AWS log-delivery service principals with an `aws:SourceAccount` condition, and
  lifecycle expiry.
- They are not readable by the application: no task role has `s3:GetObject` on
  them.
- Log data is replicated to the second region, so the loss of one region does
  not lose the audit trail.
- Every bucket in the stack, log targets included, denies any request that did
  not arrive over TLS (`aws:SecureTransport: false`). No scanner in this
  repository's set checks for that; it was added because the S3 guidance
  mandates it and reading the guidance is how it was noticed.

## The Semgrep subprocess finding, and why it is gone

`scripts/e2e-agentcore.py` used to be reported HIGH for
`python.lang.security.audit.dangerous-subprocess-use-audit`, and this section
recorded it as an accepted false positive. The call was
`subprocess.Popen([sys.executable, "-m", "agentstorming_agent.agentcore_app"])`:
a fixed argv with no shell. The rule cannot tell `sys.executable` from a variable,
and the only form it accepts, `["python3", "-m", ...]`, would have run whatever
`python3` is on `PATH` instead of the project venv. In-code suppression does
nothing on the review platform, which is correct for a review tool.

The harness now launches the agent with `multiprocessing` (`spawn`) and `runpy`
instead. `spawn` runs the child on the same interpreter by construction, and no
command line is built at all, so there is no argv for input to reach. That is a
narrower design, not the same call in a form the rule cannot see: the
`subprocess` import, its `# nosec B404` and the `# nosec B603` went with it. The
harness behaves as before. Its seven end-to-end checks pass, and an agent that
dies at startup is still reported with its exit code and its log.

## Bandit: a note on tool versions

Bandit reports **0 HIGH and 0 MEDIUM** on the published tree with test files
included.

Worth recording because it caused a wrong number in an earlier revision of the
security attestation: Bandit **1.8.6** reports seven MEDIUM `B615` findings
("Hugging Face Hub download without revision pinning") against
`packages/benchmark/.../datasets/*.py`, and Bandit **1.9.4** reports none. The
findings were false positives in 1.8.6 — every one of those calls passes
`revision=resolve("<repo_id>")`, and `resolve()` returns a pinned commit SHA from
`datasets/_revisions.py` — and 1.9.4 recognises the pattern.

The attestation briefly said "0 high, 7 medium" because it quoted the older tool.
If you reproduce this and see seven mediums, check `bandit --version` before
concluding anything changed in the code.

## Everything else

Every other Checkov rule the stack is subject to passes without suppression —
and here "without suppression" is literal: the tree contains no `checkov:skip`
and no `nosemgrep`, and `scripts/make-public-tree.sh` fails a publish if either
appears. The `# nosec` comments inventoried above are the exception to that
sentence and the only one.

The rules that pass include the ones this repository previously argued its way out of:
including the ones this repository previously argued its way out of: WAFv2 on
both the distribution and the load balancer with the Log4j managed rule group,
cross-region replication, CloudFront origin failover, geo restriction, KMS key
policies without wildcard administration, private subnets for all compute and
data, RDS-managed credential rotation, an AWS Backup plan, and query logging
with bound-parameter values suppressed.

Reproduce with:

```bash
checkov -d packages/server/deploy/cloud/aws/serverless --framework terraform
```
