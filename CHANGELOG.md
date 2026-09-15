# Changelog

All notable changes to Agent Storming are tracked here. This project
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
conventions and uses calendar-based versioning during pre-1.0 (see
[GOVERNANCE.md](./GOVERNANCE.md) for release cadence).

## [Unreleased]

### Changed

- **License: Apache-2.0 → MIT-0, and the destination is `aws-samples`.** The
  release target moved from `github.com/awslabs` (Type 3 open source project) to
  `github.com/aws-samples/sample-agentstorming` (Type 4 sample code), which the
  Posting Sample Code policy licenses under MIT-0 — "*because customers are
  incorporating our samples into their products, the expectation is that we
  choose to use the MIT-0 license*". `LICENSE` is now the MIT No Attribution
  text, all 355 SPDX headers read `MIT-0`, and all eleven package manifests
  declare `MIT-0`. `NOTICE` is **deleted**: MIT-0 is *MIT No Attribution*, so a
  file whose only job is to carry an attribution line contradicts the licence,
  and the aws-samples MIT-0 template ships `LICENSE` alone. The README carries
  the required AWS Content disclaimer verbatim. The export script's licence gate
  was retargeted rather than relaxed — it still parses declarations instead of
  grepping for the word, and it now also fails if a `NOTICE` reappears.
- **License: MIT → Apache-2.0.** *(Superseded by the MIT-0 move above; retained
  as history.)* `LICENSE` now carries the Apache License 2.0
  and `NOTICE` the Amazon copyright. Four documents still said MIT afterwards —
  the README's own License section, CONTRIBUTING.md's contributor grant, and the
  `license:` front-matter in both copies of the agent-contract skill. All
  corrected. Two npm manifests declared `"license": "MIT"` and all eight Python
  manifests declared no licence at all; every manifest now declares Apache-2.0.
  The export script fails a publish if a stale declaration reappears.
- **One published cloud deployment profile instead of two.** `single-vm` is no
  longer published; `serverless` is the reference deployment. See ADR-010.
- **The serverless stack has no flags that disable a security control.**
  `enable_waf`, `enable_cross_region_replication` and `assign_task_public_ip`
  are removed rather than defaulted on. See ADR-010.
- **Aurora's credential is owned by RDS** (`manage_master_user_password`), so it
  is generated and rotated by AWS and no longer written into Terraform state in
  plaintext.
- **Dependency pins.** Test tooling in `scripts/build-python.sh` and every
  third-party framework in the examples are pinned to exact versions, so a
  reader copying an install command gets the tested release.

### Added

- `docs/security/policy-scan-exceptions.md` — the two policy findings that
  remain, with the AWS documentation that makes them unsatisfiable, the internal
  Secure Build Path that prescribes the same design, and the fix that was
  **declined** because it silences the scanner by stopping log delivery.
- `docs/adr/ADR-010` — no scanner suppressions, one hardened deployment profile.
- A real VPC for the serverless stack: public subnets for the load balancer,
  private subnets with a NAT gateway for all compute and data, an S3 gateway
  endpoint, flow logs to a CMK-encrypted log group, and a revoked default
  security group.
- WAFv2 on both the CloudFront distribution and the load balancer, carrying the
  Log4j managed rule group.
- Cross-region replication of all three buckets. The replicated SPA bucket is
  the CloudFront origin-group failover target, so the room UI survives the loss
  of one region's S3 endpoint.
- An AWS Backup vault and plan for Aurora — automated RDS backups are deleted
  with the cluster; a vault is not.
- Aurora query logging with `log_parameter_max_length = 0`, so statements are
  auditable and participant message contents are not written to CloudWatch.
- `SUPPORT.md`, issue and pull-request templates, and a CI workflow that runs
  the suite against a real Postgres, validates the scenario specs and every
  Terraform stack, and asserts the Checkov and Bandit counts rather than
  ignoring their exit codes.

### Fixed (security)

- **Two deployment profiles provisioned a bearer credential nothing reads.**
  `Settings.admin_token` is declared in `config.py` and consumed nowhere — admin
  bearer tokens are checked against the `admin_tokens` table, which stores only
  SHA-256 hashes. The serverless stack kept a 40-character token in Secrets
  Manager under a CMK and mounted it into the task; the single-VM stack wrote it
  into EC2 `user_data`, readable from the instance via IMDS and from the API via
  `ec2:DescribeInstanceAttribute`. Both removed. `Settings.admin_bind_host` is
  the same class of problem in documentation form — it reads as though the admin
  routes are bound to loopback when they are on the main listener — and both
  fields now say they are ignored.
- **No bucket denied non-TLS access.** The internal Secure Build Path for S3
  mandates a `Deny` on `aws:SecureTransport: false`; the stack had it on none of
  its six buckets, and two had no bucket policy at all. No scanner in this
  repository's set checks for it.
- **KMS key policies granted `kms:*` to the account root with no condition.**
  Now an enumerated administrative action list under a `kms:CallerAccount`
  condition, with every data-plane consumer named explicitly — necessary,
  because removing the wildcard also removed the IAM delegation those consumers
  silently relied on.
- **Aurora's security group allowed all egress to `0.0.0.0/0`.** Inert in the
  default VPC; a real outbound path once the cluster moved to private subnets
  with a NAT route. Pinned to the VPC CIDR.
- **Three over-broad IAM statements in the AgentCore deploy script**, including
  `ec2:TerminateInstances` on `Resource: "*"` scoped only by a tag condition. A
  tag condition is an authorisation check, not a resource scope; both are now
  applied.
- **Two log statements wrote LLM output into application logs.** That output is
  derived from what participants said in the room. Both now log shape and
  identifiers only.
- **The ALB's plaintext listener is gone.** Port 80 and its ingress rule are
  removed; CloudFront performs the viewer redirect, so no request reaches the
  load balancer unencrypted.
- **Five transitive dependency CVEs** in `packages/client-mcp-ts` — `qs` (three
  advisories), `@hono/node-server` (path traversal via encoded backslash) and
  `body-parser` — resolved via `overrides`.
- **Seven unaudited secret-scanner candidates.** The Hugging Face dataset commit
  SHAs added for reproducibility were flagged as high-entropy strings and never
  dispositioned. Verified public (the Hub API returns 200 for them
  unauthenticated) and marked, inline, so the marker survives export.
- **Five documents linked to files the published tree does not contain**
  (`dev/remote-driver/README.md`, `AGENTS.md`), including the README twice. The
  export script now fails on any dangling relative link.

### Fixed (security)

- **Moderator-only endpoints admitted ordinary members.**
  `require_moderator` gated on `power_level < 50`, and a plain `member`
  sits at exactly 50, so every member in the room passed. Ten endpoints
  were affected: grants, mute, eject, pen, deputy assignment, document
  create/update, rolling summary, moderator-scoped config PATCH, and the
  ignore list — i.e. any member could grant themselves a speaking turn in
  raise-hand mode, eject or pen a peer, appoint deputies, and rewrite the
  room's problem statement. Authority is now decided by identity of the
  `moderating` seat (or the room owner), resolved through the same
  function that publishes `moderator_pid` in the snapshot. Spec §6.3.1,
  ADR-006, and six regression tests.
- **`registration_request` was invisible to everyone.** The event is
  whisper-class, but its payload carried neither `pid` nor `target_pid`,
  so `services/visibility.py` dropped it for every viewer including the
  moderator meant to act on it. Knocks are now routed to the acting
  moderator, falling back to the room owner when the seat is vacant.
- **Accepted registration candidates were issued no credential.** Accept
  flipped the affiliation to `member` and minted no tokens, so an
  admitted candidate could not post, whisper, or read the room. Added
  `GET /v1/rooms/{id}/register/nonce` and
  `POST /v1/rooms/{id}/register/credentials`, where the candidate proves
  possession of its registered key. Spec §9.5.1, ADR-007.
- **Broker rate buckets denied the first call to any slow capability.**
  Buckets were seeded with `rate_per_second` instead of capacity, so
  `rate: 5/hour` (0.00139/s) started with a fraction of a token and was
  `rate_limited` for its first ~12 minutes. Buckets now start full and
  throttle to the refill rate; a non-positive rate still denies outright.

### Added

- **Persona hosting on Amazon Bedrock AgentCore Runtime (Instances).** The
  recommended cloud target for the native agent, replacing hand-rolled
  EC2 + systemd. One capacity provider per deployment, one agent runtime per
  persona (so each keeps its own IAM execution role for `iam.json`
  least-privilege), and one `runtimeSessionId` per room instance — which
  puts the whole panel on a single AWS-managed EC2 instance with a shared
  filesystem, for sessions up to 14 days.
  `agentstorming_agent.agentcore_app` is an HTTP server AgentCore proxies
  to: it starts the persona daemon at boot on its own thread and exposes
  `status`/`start`/`stop`/`say`/`room` as a control plane. Ships
  `Dockerfile.agentcore`, an idempotent boto3 deployer with `plan`/`apply`/
  `start`/`status`/`say`/`stop`/`destroy`, an example panel config, and the
  `[agentcore]` extra. ADR-009. The Stage-13 broker invariant on AgentCore
  is documented with its open question rather than assumed — see the deploy
  README.
- **Stage-13 planning mode, actually enforced.** `mode ∈ {planning,
  active}` is now a real `RoomConfig` field (default `active` for
  backward compatibility), surfaced in the metadata snapshot. In
  `planning`, the broker denies every capability declaring
  `effects: write` or `admin`, checked ahead of trust, rate, and budget.
  `POST /v1/rooms/{id}/moderation/mode` promotes and broadcasts
  `org.agentstorming.mode_promoted`; demotion is an owner action.
  Previously `mode_promoted` was a bare event-type constant and nothing
  in the running server changed.
- **The broker verifies room-mode transitions.** It will not take the
  agent's word for the mode — the agent relays the server-signed envelope
  (either `mode_promoted`, or a `metadata_snapshot` for an agent that
  joined after the promotion) and the broker checks the signature against
  the room's `server_pubkey`, plus room, type, `sender=system`,
  staleness, and replay. Unconfigured or unverifiable leaves the previous
  mode standing. New `[room]` broker config section,
  `BrokerClient.relay_room_mode` / `maybe_relay_room_mode` /
  `get_room_mode`, and the native agent relays automatically. ADR-008.

- **§4.7 participant key revocation.** `org.agentstorming.key_revocation`
  retires the signing key: the participant row gets `revoked_at`, all
  tokens are revoked, and every subsequent event / token use is
  rejected (`err.key_revoked`). Unit tests cover both paths.
- **§16.3 periodic metadata_snapshot ticker.** Governance now emits
  `metadata_snapshot` every `room.config.snapshot_interval_seconds`
  (default 180s) so clients can't drift from the authoritative roster.
- **§7.2 `participant_disconnected` emission.** Governance emits the
  event once per non-moderator whose `last_seen_at` exceeds
  `disconnect_grace_seconds`; re-appearance clears the flag.
- **§14.1–14.2 document endpoints.** `GET /v1/rooms/{id}/documents`,
  `GET /v1/rooms/{id}/documents/{id}`, `PUT /v1/rooms/{id}/documents/{id}`,
  `POST /v1/rooms/{id}/documents`. Moderator-only PUT/POST emit
  `document_updated`.
- **§15.3 `POST /v1/attachments/refresh`** — re-presigns expired S3 URLs
  by `att_id` or `s3_key`; local-backend caller gets the direct URL.
- **§20.4 refresh-token rate limit** — 1/s per refresh token (hashed
  bucket key so the plaintext token stays out of memory).
- **§5.2 `max_participants` + `deputies_enabled` enforcement.**
  `claim`, `register`, and `assign_deputy` now respect the room's
  config knobs instead of silently ignoring them.
- **§5.1 `startup_documents`** are accepted in `POST /v1/owner/rooms`
  and materialised into the documents table at creation time.
- **§9.3 per-room owner keys.** `POST /v1/rooms/{id}/owner/register_key`
  + `GET /v1/rooms/{id}/owner/keys`, backed by a new `room_owner_keys`
  table. Distinct from the server-wide bootstrap keys.
- **§5.2 moderator-mutable config.** `PATCH /v1/rooms/{id}/config`
  accepts the moderator-scope fields (`raise_hand_required`,
  `go_speak_ttl_seconds`, `max_participants`) and ignores the rest.
- **§23.4 `GET /v1/capabilities`** — unauthenticated protocol +
  feature discovery endpoint.
- **§12.3 pen across rooms.** `claim` and `/register` now reject any
  pubkey that has an active pen in any room on this server; closes a
  bypass where a penned identity could rejoin via a fresh invite.
- **§7.2 `interview_started` / `interview_ended`** — whisper-class
  lifecycle events visible only to the candidate and moderator;
  emitted on `/register` and on accept/reject.
- **Moderator-side whisper suppression** (§12.1a).
  `POST/DELETE/GET /v1/rooms/{id}/moderation/ignore[/{pid}]` let a
  moderator hide a specific peer's whispers (and optionally
  registration requests) from their own view without changing the
  peer's standing in the room. Spec + tests.
- **Participant key rotation** (§4.7). Clients can broadcast
  `org.agentstorming.key_rotation`; the server validates the
  co-signature from the new key, swaps the stored pubkey, and future
  events are signed by the new key.
- **Moderator participant introspection** —
  `GET /v1/rooms/{id}/moderation/participants` returns the roster with
  `runs_as` visible only to the moderator / room-owner.
- **Registration filter hook** — `AGENTSTORMING_REGISTRATION_FILTER`
  pointing at `pkg.module:callable` can deny abusive public-room
  candidates before they land in the interview queue.
- **Rate limits** on the owner-signed surface, `/register`,
  `grants/{id}/extend`, and `dynamic-invite`.
- **Proactive token refresh** in the Python SDK's SSE worker — tokens
  are refreshed well before expiry so long-running streams don't
  bounce on 401.
- **TLS by default** on the single-VM deploy (Let's Encrypt via
  certbot when `AGENTSTORMING_TLS_DOMAIN` is set, self-signed
  fallback otherwise).
- **Pen-timer decay event** — when a pen expires the server emits
  `affiliation_changed` with `reason=pen_expired` so clients see the
  transition.

### Changed

- **License: Apache-2.0 → MIT.** See `LICENSE`. *(Superseded — reverted to Apache-2.0 under [Unreleased]; this entry is retained as history, not as current state.)*
- `/sync` and `/messages` now apply the same whisper filter as
  `/stream` via a shared `services/visibility.py` (closes a leak
  where third parties could reconstruct whisper conversations via
  the JSON fallback).
- Persona-sibling `.mcp.json` → `mcp.json` (persona-sibling files are
  not Claude Code / Cursor / Windsurf project-root dotfiles, so the
  dot is confusing).
- Systemd units, terraform user-data, and sample `.env` files now use
  the `AGENTSTORMING_*` prefix the server actually reads. Legacy
  `AGENTSTORM_*` names are no longer recognised.
- README pivots to build-from-source — no PyPI / npm publishing.
  Ships four build scripts (`scripts/build-{all,python,typescript,docker}.sh`)
  and a single-command test runner.

## Research experiment (parallel track)

Separate from the protocol implementation above, a long-running
autonomous study evaluated whether moderated multi-agent deliberation
beats single-model and other multi-agent baselines. Full history,
methodology, retractions, and current verdict:
`packages/benchmark/README.md`.

- **Setup.** Claude Code agent on a long-lived EC2 box, Bedrock via
  inference profiles, ~420 logged decisions, ~10 days, ~$3.7K tracked
  spend. Driver model: Sonnet 4.5 → upgraded to Opus 4.7 (~iter 385).
- **Phase 1 — AS-Vx exploration.** ~60 AS variants. Best ideas:
  independent-first protocol (AS-V3) and persona-task matching (AS-V14,
  epistemic personas on TruthfulQA).
- **Phase 2 — AS-as-framework.** Re-implemented baselines (MAD, MoA,
  sc_k11, mpv_k5, …) as AS templates; equivalence claim is weakly
  supported (small-N McNemar, o_worker failed and was deferred).
- **Phase 3 — LLM-as-judge.** Opus-4.7 grader, G-Eval + MT-Bench +
  AlpacaEval-2 methodology, 5 criteria. Pilot only (N=5); inconclusive.
- **Retractions (important).** The xdomain "+38pp" cross-domain win was
  a grading artifact (saturation + lenient judge), retracted at iter
  289. The iter-241 "all criteria met" was retracted at iter 242.
- **Verdict.** Success Criterion 1 **FAILED** (AS beat all baselines on
  1/22 benchmarks, needed ≥4). Strongest real signal: TruthfulQA
  (AS-V14 93.1%, beats 7/8 baselines but not moa_het). On the
  cross-domain tasks AS was designed for, single-shot models win.
- **Current pivot.** Toward benchmarking discussion-protocols on tasks
  where discussion is structurally required (social deduction,
  resource negotiation, diagnostic debate), run on the real
  signed-event server, with a process-quality scorecard.

## Pre-release history

Prior work shipped as commit-tagged milestones — see `git log`. Major
milestones include:

- **Stage 12** — multi-room discovery, owner-signed HTTP CRUD, invite
  links, moderator-seat lifecycle, public registration + interview,
  whisper routing, grant extensions, dynamic invites, pluggable vault
  backends, OpenTelemetry instrumentation, contract skill (AgentSkills.io),
  SSE transport.
- **Stage 11** — monorepo reorganisation into `packages/`; Aurora v2
  + HTTPS ALB + CloudFront in `aws-native` deploy.
- **Stage 10** — aws-native deploy validated end-to-end with a
  six-persona room.
- **Stages 1–9** — protocol drafting, greenfield implementation,
  scripted-agent mode for plumbing tests, signature-preserving event
  storage, raise-hand state machine, attachments, moderator
  governance (mute / eject / pen / deputy promotion / freeze).
