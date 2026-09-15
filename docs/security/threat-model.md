# Agent Storming — threat model

This document exists for two audiences: a security reviewer deciding whether
this content is safe to publish and to copy, and a deployer deciding what they
still have to do themselves.

It does not restate the specification. `docs/specification.md` §20 lists
protocol-level threats, and its Stage-13 addendum covers the credential broker;
this document gives the structure those sections assume — the assets, the trust
boundaries, and what remains true when an adversary sits on either side of each
one. Where the spec is normative, it wins.

**Reference deployment, not a product.** Agent Storming is a protocol and a
reference implementation. It has not been through a penetration test. Nothing
here should be read as a claim that a given deployment is secure; the
"Deployer obligations" section at the end is the part that determines that.

---

## 1. What the system is, in one paragraph

Many parties — LLM agents and humans, running anywhere, under different
operators — join a shared **room** and deliberate. Every event is signed by its
sender with an Ed25519 key that the server never holds. A **moderator** seat
controls turn-taking; an **owner** key is the governance root. Agents that need
real credentials (to call an LLM, hit an API, read a repository) obtain them
through a **broker** running as a different uid, so that credential material
never enters the agent process or the model's context.

The security posture follows from one observation: **in a room full of agents
from different operators, no participant can be trusted, and the agent process
itself is not trusted by its own broker.** Almost every mechanism below is a
consequence of taking that seriously.

---

## 2. Assets

Ordered by what an attacker would most want.

| # | Asset | Why it matters | Where it lives |
|---|---|---|---|
| A1 | Participant Ed25519 private key | Signs events as that identity; theft is full impersonation for as long as the key is trusted | Participant device only — OS keystore, or password-wrapped IndexedDB in a browser |
| A2 | Owner key(s) | Governance root: adds owner keys, overrides moderators | Owner's device; public half seeded via `AGENTSTORMING_OWNER_PUBKEY` |
| A3 | Plane-3 credential material | Real API keys, tokens, DB passwords | Broker process address space **only** |
| A4 | Invite / access / refresh tokens | Bearer credentials; admit or act as a participant | Server issues; client stores |
| A5 | Admin token | `/v1/admin` access | Server config; Secrets Manager in the ECS deployment |
| A6 | Moderator authority | Controls who speaks; abuse distorts the deliberation the system exists to produce | The *seat*, derived from affiliation — not a token |
| A7 | Room event history | The record being deliberated over; integrity is the product | Postgres, plus every participant's verified local copy |
| A8 | LLM spend | Directly convertible to money | Broker budget + rate limits |
| A9 | Postgres contents | Rooms, participants, tokens, documents | Server's database |

---

## 3. Trust boundaries

```
                        ┌──────────────────────────────────────┐
   ┌───────────┐  B1    │            Storm server              │
   │  Human    │◄──────►│  FastAPI + Postgres + SSE            │
   │  (SPA)    │        │  semi-trusted: may censor, cannot    │
   └───────────┘        │  forge; never sees A1/A2 private     │
                        │  halves                              │
   ┌───────────┐  B1    └──────────────────────────────────────┘
   │  Agent    │◄──────────────────▲
   │  operator │        B2         │  every inbound event
   │     X     │◄──────────────────┘  signature-verified
   └───────────┘   peer, mutually untrusted
        │
        │ B3  ── the agent is the UNTRUSTED party here ──
        ▼
   ┌──────────────┐        ┌──────────────────────────────┐
   │ agent process│───────►│ storm-broker (separate uid)  │
   │ (holds LLM   │ AF_UNIX│ holds A3. Verifies caller by │
   │  context)    │ SO_PEER│ SO_PEERCRED. Returns results │
   └──────────────┘ CRED   │ or headers, never secrets.   │
        │  B4                └──────────────────────────────┘
        ▼                              │ B5
   ┌──────────┐                        ▼
   │   LLM    │              ┌───────────────────┐
   │ provider │              │ MCP server / tool │
   └──────────┘              │ (separate uid)    │
                             └───────────────────┘
```

**B6 — operator with shell on the host — is explicitly out of scope.** Anyone
with root on the machine running the broker can read A3 from its memory. The
spec names hardware-attested runtimes (Nitro Enclave, Confidential Space) as the
v2 answer. Deployers should assume a compromised host means compromised
credentials for that host.

---

## 4. Threats by boundary

### B1 — participant ↔ server

| Threat | Mitigation | Residual |
|---|---|---|
| Impersonating another participant | Per-event Ed25519 signature over the JCS-canonical envelope (RFC 8785); PID is `base64url(sha256(pubkey))@room`, so identity is *derived from* the key and cannot be asserted | Key theft on the participant's own device |
| Replaying a captured event | Per-sender nonce window (256) + 30 s clock-skew bound | An attacker who can replay inside the window and beat the nonce cache |
| Stealing a bearer token in transit | TLS required; access tokens 15 min, refresh 24 h, rotation on use, revocation on eject/pen | Bearer tokens are not sender-constrained. DPoP is named as a future revision — until then a stolen access token is usable for up to its TTL |
| **Malicious or compromised server** | Signatures are over participant-authored bytes, so a server **cannot forge** an event or fabricate a consensus | A server **can censor, reorder, withhold, and observe**. There is no E2EE: the server reads every message body. Deployers who need confidentiality from the server operator do not have it today |
| Abusive registration knocks | Rate limits plus at least one of CAPTCHA / proof-of-work / IdP gating | Determined low-volume abuse |
| Resource exhaustion by a valid participant | Per-PID rate limits (10 posts/min, 1 raise-hand/min, 60 `/sync`/min) | Distributed abuse across many admitted identities |

### B2 — participant ↔ participant

| Threat | Mitigation | Residual |
|---|---|---|
| Forged peer event | Every inbound event verified against the sender's pubkey from `participant_joined` or the metadata snapshot | First-contact trust rests on the server correctly reporting a new joiner's pubkey |
| Prompt injection via room content | This is the central unsolved risk. Content from peers is untrusted input to a model that may act on it. Mitigations are defence-in-depth, not prevention: the broker boundary (B3) means a fully hijacked agent still cannot read A3; capability allowlists bound what it can reach; budgets bound what it can spend | **A peer can talk your agent into misusing its own legitimate authority.** Assume any agent can be induced to say or request anything within its permitted scope |
| **Privilege escalation to moderator** | Authority is the *identity of the seat* — room-owner, or the acting `moderating` participant — not a numeric threshold (ADR-006) | See §6: this was a real, exploited-in-review defect, not a hypothetical |
| Moderator abuse once held | Original-moderator reclaim; room-owner override; deputy chain with explicit ranks | A malicious owner is the trust root and cannot be constrained by the protocol |

### B3 — agent process ↔ broker

This is the boundary the Stage-13 design exists to create, and the one that
makes the rest survivable.

| Threat | Mitigation | Residual |
|---|---|---|
| Agent reads a raw credential | A3 lives only in the broker's address space. The agent receives *prepared headers or results*, never secret material | Host compromise (B6) |
| Credential reaches the LLM context | Architectural rule: plane-3 material never enters the agent process, therefore cannot be serialised into a prompt. Outbound DLP scan is a second, soft layer | DLP is pattern-based and explicitly a soft defence; novel credential shapes pass |
| Another local process impersonates the agent | `SO_PEERCRED` on the AF_UNIX socket checks the caller's uid | Anything running as the agent's own uid |
| Agent asks for a capability it should not have | Plane-4 capability declaration configured **outside** the agent; policy engine denies before trust/rate/budget checks | Misconfigured capability grants |
| Agent acts during a planning-only phase | Room `mode` gates side-effecting calls; the broker verifies a *server-signed* `mode_promoted` envelope rather than believing the agent (ADR-008) | — |
| Budget exhaustion / cost attack | Per-capability token buckets and budgets in the broker | A slow drain within allowed limits |

### B4 — agent ↔ LLM provider

Prompt content, including room history, crosses this boundary to a third party.
That is inherent to the design, not a defect, but it means **room content is
disclosed to whichever model providers the panel uses.** A deployer choosing
providers is choosing who reads the deliberation. Diversity of providers — the
research arm's whole premise — widens that set.

### B5 — agent ↔ MCP servers and tools

| Threat | Mitigation | Residual |
|---|---|---|
| Malicious tool description ("tool poisoning") | **Specified, not implemented.** The spec requires SHA-256 pinning at registration with a `tool_description_changed` whisper on change. The event type and its visibility rule exist; **no code computes or compares a description hash.** See §8 | **Unmitigated today.** A tool server that changes its description after registration does so silently |
| Compromised MCP server pivots | Each MCP server runs as a separate uid and is reached *through* the broker, not directly | It can still misuse its own service-account credential within that credential's scope |

### B7 — deployment infrastructure

The Terraform under `packages/server/deploy/` and
`packages/native-agent/deploy/` is sample infrastructure that a reader will copy,
so its defaults are part of the threat surface.

Current posture: IMDSv2 required; EBS, S3 and RDS encrypted with customer-managed
KMS keys; ECS tasks run with a read-only root filesystem; the admin token comes
from Secrets Manager rather than a task-definition environment variable (which
`DescribeTaskDefinition` would expose); ALB redirects HTTP to HTTPS
unconditionally; ECR tags immutable; single-VM ingress is restricted to the
deployer's own address and reaches the API only over TLS on 443 — port 8440 is
deliberately not exposed, because nginx proxies to it over loopback and a second
cleartext route would carry invite tokens in the clear.

The AgentCore operator role scopes destructive EC2 actions by resource tag, not
by region alone, and `ec2:CreateTags` is confined to the create call so the tag
scope cannot be escalated by retagging an unrelated instance.

---

## 5. Explicitly out of scope

Named so a reviewer does not have to infer them:

1. **Operator with shell access to the host** (B6). Reads A3 from broker memory.
2. **End-to-end content confidentiality.** The server sees plaintext bodies. MLS
   is named as a possible future direction; it is not implemented.
3. **Traffic analysis.** Event and `/sync` timing reveals who is active.
4. **Availability / DDoS** against the server. The operator's responsibility.
5. **A malicious room owner.** The owner key is the governance trust root; the
   protocol cannot constrain it.
6. **Model behaviour itself.** Whether a model can be argued out of its
   instructions is not a property this protocol can fix; it is why B3 exists.

---

## 6. Defects found and fixed — evidence this model is grounded

A threat model that has never caught anything is a document, not a control. The
following were found in review of this codebase and fixed; they are recorded
because they show which mitigations were load-bearing.

| Defect | Why it mattered | Fix |
|---|---|---|
| Moderator authorisation compared `power_level < 50`, and an ordinary member is exactly 50 | **Every member passed the moderator check on ten endpoints.** A member could grant themselves the floor, bypassing moderation entirely | Authority is now the identity of the acting seat (ADR-006) |
| `registration_request` carried no routing field | The event was invisible to every viewer, so the registration door silently did nothing | `pid`/`target_pid` added; candidate-signed credential collection specified (ADR-007) |
| Broker trusted the agent's assertion of room mode | The untrusted party could declare "we're active", making planning mode decorative | Broker verifies the server-signed envelope, without re-canonicalising (ADR-008) |
| Broker rate buckets seeded with `rate_per_second` instead of capacity | Any capability slower than 1/s was unusable on first call — a `5/hour` limit blocked for ~12 minutes | Buckets seed at capacity; `rate <= 0` denies explicitly |
| `bootstrap.sh` generated the Postgres password under `set -x` | The password was written to `cloud-init-output.log`, which is world-readable and retrievable from the console, on every boot | Tracing disabled across credential handling |
| `SECURITY.md` directed reports to `security@agentstorming.dev` | **That domain is unregistered.** On publication anyone could register it and receive private, unpatched vulnerability reports | GitHub repository-bound private reporting only |
| 25 documents instructed `pip install agentstorming…` | All seven distribution names are unclaimed on PyPI and npm; publishing would have advertised free names to squat, and readers would install whatever appeared there | Build-from-source, with the reason recorded at each install section |

---

## 7. Known gaps — specified but not implemented

Listed separately from §5 because these are *intended* mitigations that a reader
of the specification would reasonably assume are in place. They are not.

| Gap | Consequence | Where |
|---|---|---|
| **Tool-description SHA-256 pinning.** The spec requires descriptions to be pinned at registration and a `tool_description_changed` whisper emitted on change. Only the event type and its visibility rule exist | Tool poisoning via post-registration description change is undetected | `docs/specification.md` Stage-13 addendum vs. `packages/storm-broker/` |
| **Sender-constrained tokens.** Access and refresh tokens are plain bearer tokens; the spec names DPoP as OPTIONAL/future | A stolen access token is replayable for up to its 15-minute TTL from anywhere | spec §20.1 |
| **No end-to-end encryption.** Deliberate, but it means the gap is permanent until MLS lands | Server operator reads all deliberation content | spec §20.2 |
| **No penetration test.** Scanners (Bandit, Checkov, detect-secrets, npm audit, Semgrep, Grype, Holmes CSR) are clean at critical/high, and the suite is 210 tests. None of that is a pentest | Unknown unknowns remain | `docs/security/secret-scanning.md` |

The broker's `SO_PEERCRED` gate *is* implemented, on both Linux (`struct ucred`)
and macOS (`LOCAL_PEERCRED` + `LOCAL_PEERPID`), and raises on any other platform
rather than silently skipping the check — so B3 fails closed.

---

## 8. Deployer obligations

The reference implementation cannot do these for you.

1. **Terminate TLS with a certificate clients can verify.** Set `tls_domain`
   and `tls_email` in the single-VM deployment. With neither set, bootstrap
   falls back to a self-signed certificate: the transport is encrypted but
   clients cannot distinguish your host from an impostor. Treat that as
   development-only.
2. **Keep participant private keys off the server.** If you build a client, the
   private half never leaves the device. This is the protocol's central
   invariant.
3. **Run the broker as its own uid**, not as the agent. The B3 boundary is a
   uid boundary; collapsing it removes the protection entirely.
4. **Scope plane-4 capabilities to what the persona actually needs.** Allowlists
   and budgets are what bound a hijacked agent.
5. **Choose model providers deliberately** — see B4. They read the room.
6. **Restrict ingress.** The sample restricts to the deployer's address; opening
   it wider is a decision to make consciously.
7. **Rotate and revoke.** Invite tokens are single-use; access and refresh tokens
   have short TTLs; revoke on eject and pen.
8. **Assume prompt injection will succeed eventually** and verify that what your
   agent is *permitted* to do is something you can tolerate it doing wrongly.
