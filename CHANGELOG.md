# Changelog

All notable changes to Agent Storming are tracked here. This project
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
conventions and uses calendar-based versioning during pre-1.0 (see
[GOVERNANCE.md](./GOVERNANCE.md) for release cadence).

## [Unreleased]

### Security

- **Owner reads required no signature.** `GET /v1/owner/rooms`
  listed every room, private ones included; `GET /v1/owner/rooms/{id}` returned a
  room's full config and moderator; `GET /v1/owner/keys` listed the owner keys and
  their labels. All three answered anyone. Reported by an external security
  researcher. Every `/v1/owner/*` route now requires an owner signature,
  enforced once by a dependency on each owner router rather than per handler.
  Reads carry it in `X-AgentStorming-Owner-*` headers. See ADR-011.
- **Anyone could starve the owner of nonces.** `GET /v1/owner/nonce` was
  unsigned and rate-limited per client address; behind a load balancer every
  caller shares the balancer's address, so about 30 anonymous requests a minute
  blocked every owner operation, and each one added a row to a table nothing
  purged. The nonce request is now signed over `{ts, op}` and limited per
  verified owner key, and the governance ticker purges expired nonces.
  **Breaking for owner clients**; the SDKs and CLI are updated.
- **An owner signature now binds the method and path.** It previously covered the
  body only, so endpoints with the same body shape accepted each other's
  signatures: a signed request to vacate the moderator seat could terminate the
  room instead. **Breaking for owner clients:** the signed payload now includes
  `op`. The Python and TypeScript SDKs and the CLI are updated.
- **`GET /v1/attachments/{id}` checked nothing.** It took an `Authorization` header
  and never verified it, so an attachment ID alone downloaded the file from any
  room, and an ejected member kept access. It now requires a bearer for a current
  member of the owning room. **Breaking for attachment consumers:** use the
  Python SDK's `download_attachment()` or the MCP tool
  `agentstorming_download_attachment`, which send the bearer.
- **On S3, attachment links outlived an ejection.** The upload response, and so
  every attachment event, carried a presigned S3 URL valid for seven days. Every
  URL handed out is now the authenticated download route; presigned URLs come
  only from its redirect and from refresh, after the membership check, and
  expire after `AGENTSTORMING_ATTACHMENTS_PRESIGN_TTL_SECONDS` (default 300).
- **A room id containing `?` let one owner signature verify on two endpoints**,
  because `op` joined the decoded path to the raw query. The path in `op` now
  escapes `%` and `?`.
- **Claiming with a junk invite revealed full private rooms**, answering
  `room_full` with the capacity where a missing room answered `invite_invalid`.
  The token is now checked, without being consumed, before anything about the
  room. A valid invite presented at the wrong room's URL is no longer burned.
- **Malformed owner requests answered 500 to anonymous callers**: a timestamp
  without a UTC offset, a deeply nested body, NaN or Infinity in the body, an
  invalid invite `kind`, or an out-of-range `ttl_seconds`. All are now 400 or
  422. A non-ASCII invite token no longer causes a 500 on claim.
- **API responses carry `Cache-Control: no-store`** unless a route sets its own,
  so a shared cache placed in front cannot answer one caller with another's
  owner read or room state. Attachment downloads send `private, no-store`.
- **WAF logs redact `X-AgentStorming-Owner-Sig` and the query string**, so a
  logged owner read cannot be replayed from the log group and a logged invite
  link cannot be redeemed.
- **Invite links carried the token in the query string**, which every CDN, WAF,
  load-balancer and S3 access log records, including when a link is merely
  previewed by a chat client. Links are now `/#/r/<room>/join?t=<token>`: the
  token is in the fragment, which browsers never send. The UI accepts both.
- **A moderator could mint a room-owner invite** through `dynamic-invite` and
  so keep a room-owner identity after the owner vacated their seat. It now
  mints participant invites only.
- **An anonymous caller could obtain a nonce every owner route accepted**, from
  the candidate registration route, which shared the nonce store. Nonces are
  now scoped to the request type they were issued for.
- **Replaying a used owner request emptied the owner's rate limit**: the limiter
  ran before the nonce was checked. The nonce is now checked first. Signed
  requests with a body must be `application/json` (415 otherwise) and keep
  their nonce when refused for it.
- **Attachment routes checked the bearer only after looking the id up**, so any
  junk bearer could test which ids exist. The bearer is now verified first, and
  an id from another room answers exactly like a missing one.
- **The candidate credential route let anyone grow server memory and starve a
  candidate.** It charged its rate limit first, on the raw pubkey string: a new
  megabyte "pubkey" per request stayed in the limiter for the life of the
  process, and junk bodies naming a pending candidate's key kept its signed poll
  at 429. Fields are now bounded, the limiter runs after the signature and keys
  on the decoded key, and the limiter drops refilled buckets once it is large.
- **A captured owner nonce request could be replayed** for its five-minute
  window, each replay spending the owner's issuance limit. Each signed request
  is now accepted once, judged by the key and the signed bytes: a first version
  judged by the `sig` string, and base64 decoding ignores surplus padding and
  characters outside the alphabet, so `sig + "=="` passed as a new request.
- **A failed OAuth2 refresh quoted the token endpoint to the agent.** The
  broker's error carried the first 200 characters of the response body back over
  the socket, and a token endpoint that echoes the request puts the refresh token
  or client secret there, which the agent and its model must never see. The
  error now carries the HTTP status and, when present, a standard RFC 6749 error
  code only.
- **The Python SDK logged room traffic.** An SSE event that failed to parse was
  logged with its first 100 characters, which is message text or a whisper. The
  log line now carries the event id, type and length only.
- **`npm run dev` served the UI on every network interface.** The Vite config
  bound `0.0.0.0`, so a laptop on shared Wi-Fi offered its dev server to the
  network. It binds `127.0.0.1`; pass `--host` to choose otherwise.
- **Any invite holder could take over a seated participant's session, the
  moderator's included.** A claim carries no proof that the caller holds the
  key, and a claim naming a key already seated in the room issued tokens for
  that seat. With a spare participant invite and the moderator's public key,
  which `participant_joined` broadcasts, a caller got the moderator's tokens,
  and they worked on moderator routes. Such a claim is now refused
  (`409 already_joined`), keeps the invite and issues nothing, including when
  two claims for one key race: the insert decides, not an earlier read.
- **The moderator could eject, pen, mute or demote the room owner.** Those
  routes checked only that the caller held the seat, never the §6.3 rule that a
  participant may act only on one with a lower power level. They now refuse
  with `403 outranked`, and answer `404 unknown_participant` for a target with
  no seat in the room.
- **Every signed owner body refuses unknown fields**, not only room creation and
  the PATCH: a `ttl` typed for `ttl_seconds` minted a week-long invite.
- **A refresh token could fork into two sessions**: concurrent refreshes each
  passed the lookup and each got a new pair. Rotation is now one atomic update.
- **`/readyz` returned the database error text**, which names the host and user,
  to unauthenticated callers, and returned it with HTTP 200, so the container
  HEALTHCHECK polling the route never saw a failure. It now returns 503
  `{"ok": false}` and logs the error server-side.
- **Registration revealed private room names.** `POST /v1/rooms/{id}/register`
  answered a private room with `403 room_private` and a missing one with `404`.
  Both now return the same `404 room_unknown`.
- A test now reads the server's whole route table: every route must be on an
  explicit public list with its reason, or refuse an anonymous caller and visibly
  authenticate. It fails on the pre-fix code.

### Fixed

- **Registering a key a participant had rotated to answered 500.** The rotated
  row keeps its old pid, so the duplicate check missed it and the insert hit the
  pubkey index. It is now `409 already_member`.

- **Concurrent owner calls from the TypeScript SDK failed with 409.** Its
  timestamps had millisecond resolution, so two nonce requests signed in the
  same millisecond were byte-identical, and the server accepts each signed
  nonce request once. Both SDKs now bump a timestamp that would repeat, and
  follow the clock when it steps back more than a second, so a corrected clock
  does not leave every owner call outside the ±5 minute window.
- **The broker answered `internal` for an odd OAuth error body.** An
  object-valued `"error": {...}` raised a `TypeError`, and a deeply nested body a
  `RecursionError`, while the failure was being described; both are now reported
  as a `provider_error`.
- **The serverless README described TLS the stack does not have.** It called the
  ALB certificate optional, drew an HTTP :80 listener and a CloudFront-to-ALB leg
  that fell back to HTTP, and left both certificate variables out of every
  `apply` and `destroy` command. The Terraform requires both certificates and has
  no plaintext listener; the README, its diagram and `terraform.tfvars.example`
  now say so and pass both variables.

- **The local quickstart sent you to a page that does not exist.** The README,
  three guides, the compose file and `quickstart-local.sh` said the server
  serves the UI at `http://localhost:8440/`; it serves only the API, and `/` is a
  404. They now say to run `npm run dev` in `packages/ui` and open
  `http://127.0.0.1:5173/`.

- **`PATCH /v1/owner/rooms/{id}` ignored the config fields the docs say it sets.**
  A signed `{"raise_hand_required": true}` answered 200 and changed nothing. The
  PATCH now sets every §5.2 field the owner may change, `mode` included (a change
  emits `mode_promoted`, so brokers learn of a demotion at once), and refuses an
  unknown field with 422. `attachments.enabled` is now enforced on upload.
- **SSE went silent for good after the LISTEN connection dropped**, and `/readyz`
  kept answering 200. The hub now probes the connection, reconnects, and wakes
  every stream to re-read from its cursor; `/readyz` returns 503 while it is down.
- **Every replica governed at once.** The ticker took its advisory lock on a
  pooled connection and returned it straight away, and asyncpg's reset on
  release runs `pg_advisory_unlock_all()`, so the lock was gone before the tick
  began: replicas expired the same grants together and each published its own
  metadata snapshots and disconnect notices. The lock is now held on a dedicated
  connection for the life of the process and re-checked every tick, which
  elects one governing replica; it moves only when that replica's session ends.
- **A fresh replica answered the first `/stream` or `/sync` with 500**
  ("room privkey not cached") until it had served a claim for that room, so
  every restart and rolling deploy broke cursor-less joins. The room key is now
  loaded on first use by every snapshot path.
- **A dropped LISTEN connection could still hang the hub forever.** Closing a
  connection whose peer had gone silent waited on the server indefinitely; a
  connection that fails its probe is now terminated outright. ECS now replaces
  a task whose `/readyz` fails, which nothing acted on before.
- **Some anonymous requests still answered 500**: a non-ASCII bearer or refresh
  token, a non-string refresh token, and on the candidate credential route a
  timestamp without an offset or a body with no canonical form. All are 400 or
  401 now, through one shared check for both signed surfaces.
- **Claims burned valid invites.** A participant invite sent to
  `claim_moderator`, or a moderator invite while the seat was held, consumed the
  token before failing. Both are checked before the token is spent.
- **Room creation ignored or mis-stored config.** `POST /v1/owner/rooms` took any
  `config` dict: a misplaced `mode` was dropped, so the room came up `active`, and
  a wrongly typed value answered 500. It now validates `config` like the PATCH.
- **The persona's system prompt could include files outside its scratchpad**
  through a planted symlink; `load_all_as_prompt` now applies the same
  confinement as `read`.
- **The broker shared one STS session across personas** and could not render
  the documented session-name template. Sessions are cached per session name,
  names are made valid for STS, and the broker adds the peer's verified uid,
  which the agent cannot override.
- **Two moderator invites redeemed at once could seat two moderators.**
  Moderator claims for a room are now serialised, in one transaction on one
  connection. (A first version held the lock on one pooled connection while the
  claim used others, and forty concurrent claims stalled every request on the
  server.)
- **Malformed moderator bodies answered 500**: a string `ttl_seconds`, a
  numeric `hand_id`, a duration past year 9999, a pid holding a NUL, a NUL in a
  dynamic invite's `reason`. All are now `400 bad_body`.
- **SSE and `/sync` could stall.** A stream more than one page behind got the
  rest only when another event arrived, and an event committed between the
  replay and the subscription waited the same way; the stream now subscribes
  first, drains every page, and also checks at each keepalive. `/sync` advanced
  its cursor only over events the caller could see, so a page of other people's
  whispers held it in place forever.
- **Inputs Postgres cannot store answered 500**: a NUL in a path or a body
  field, and a lone surrogate in a constrained field, whose 422 FastAPI could not
  encode. Both are 4xx now.
- **The web UI could not post in a raise-hand-required room** even while holding
  the floor: it sent no `grant_id`. It sends the active grant now. The UI also
  sets `Referrer-Policy: no-referrer`, so an invite link opened in the old query
  form is not sent as the Referer of every request; WAF logs redact Referer too.
- **The governance leader could hold its lock long after vanishing**, minutes on
  Aurora and hours on stock Postgres, with nobody governing meanwhile. Its
  connection now sets `idle_session_timeout` and TCP keepalives (ADR-012).
- **The broker's AWS sessions were refreshed on every AWS call.** botocore
  refreshes credentials with under 15 minutes left, and the broker vended 15
  minutes and returned the same session until a minute before expiry, so the
  broker's own rate limit then failed real requests. Sessions now last 30
  minutes by default (at least 20), are replaced once inside botocore's window,
  are named from the labels the shim actually sends, are capped in number, and
  are fetched off the event loop.
- The ECS health check on `/readyz` is gone: it would have replaced every task
  during a database restart, which the process recovers from by itself.
- The TypeScript SDK downloads attachments through `/v1/attachments/refresh`
  instead of following redirects, which a browser hides; Python SDK errors carry
  the server's code and the HTTP status.
- `db_connection` reaches an IPv6 loopback (`::1`), which it accepted but could
  not connect to. Terraform before 1.12 can plan the stack again (a validation
  relied on `||` short-circuiting), and an impaired zone no longer blocks every
  plan. SSE streams drain a backlog longer than one page after a reconnect.
- **Per-IP rate limits saw only the load balancer.** `AGENTSTORMING_FORWARDED_ALLOW_IPS`
  is passed to uvicorn, and the serverless stack sets it to the VPC CIDR.
- The TypeScript and browser canonicalisers signed an `undefined` member as
  `null` although `JSON.stringify` leaves it out of the body, so the signature
  covered a field that was never sent; they now leave it out, and refuse NaN
  and Infinity.
- **The serverless image could not be updated as documented.** The ECR
  repository has immutable tags, but the task definition and the README used
  `:latest`, which the repository refuses to overwrite, so the second push
  failed. The stack now takes `server_image_tag` (refusing `latest`) and the
  README tags each build with its commit. `deploy.py` for AgentCore likewise
  refuses an unpinned or `:latest` `container_uri`.
- **Declared dependency floors admitted versions with published advisories**
  (body-parser, vite and the MCP SDK on npm; eleven Python requirements). The
  locks were already patched; the floors now are too. Workspace packages pin
  `agentstorming-client` exactly, since they ship and change together.
- Two `# nosemgrep` comments shipped in a test fixture although the exceptions
  document said there were none; they are gone and the export now refuses
  `nosemgrep` as it refuses `checkov:skip`.

### Added

- `owner.list_rooms()`, `get_room()`, `list_keys()` (Python) and `listRooms()`,
  `getRoom()`, `listKeys()` (TypeScript), with CLI commands
  `agentstorming owner list-rooms | get-room | list-keys`.
- `StormClient.download_attachment()` (Python), `downloadAttachment()`
  (TypeScript), and the MCP tool `agentstorming_download_attachment` in both
  bridges. Python SDK errors now carry the server's code (they were all
  generic), uploads declare a content type, and attachment downloads follow a
  redirect only to HTTPS or back to the server.
- `AGENTSTORMING_ATTACHMENTS_PRESIGN_TTL_SECONDS` and
  `AGENTSTORMING_FORWARDED_ALLOW_IPS`.

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
