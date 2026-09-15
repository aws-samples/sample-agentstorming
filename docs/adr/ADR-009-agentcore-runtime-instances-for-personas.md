# ADR-009: Host cloud personas on AgentCore Runtime Instances, not bare EC2

- Status: **Accepted**
- Date: 2026-09-04
- Deciders: Yudho Diponegoro
- Related: spec Stage-13 §broker invariant, ADR-008, `dev/architectures/agentstorming-native-agent.md`

## Context

Cloud deployment of the persona runtime was hand-rolled EC2: launch an
instance, install the package, write a systemd unit per persona, and own the
patching, scaling, and teardown forever. The research driver did the same
thing for itself (`dev/remote-driver/terraform/`), and the cost of that
choice is still visible — a `c7i.2xlarge` with AdministratorAccess sat in
the account for months after the experiment ended because nothing was
responsible for reaping it.

Amazon Bedrock AgentCore Runtime added a second compute type, **runtime
instances** (GA 2026-08-06): AWS-managed EC2 in your own account, with
AgentCore handling provisioning, patching, scaling, and lifecycle. It is
a much closer fit to a persona panel than the serverless microVM option:

| Requirement | microVMs | Instances |
|---|---|---|
| Session lifetime | 8 hours | **14 days** |
| Agents per session | 1 | **N, sharing a filesystem** |
| OS access (needed for the broker) | no | **yes** |
| GPU | no | yes (`g5`/`g6`/`g6e`/`g7e`, drivers provisioned) |
| Networking | PUBLIC or VPC | VPC only |

## Decision

Persona runtimes deploy to **AgentCore Runtime, Instances compute type**.

- **One capacity provider per deployment** defines the EC2 fleet.
- **One agent runtime per persona**, all sharing that capacity provider.
- **One `runtimeSessionId` per room instance.** Invoking every persona's
  runtime with the same session id lands the whole panel on one managed
  instance with a shared filesystem — which is the shape a room already has.
- The container entrypoint is `agentstorming_agent.agentcore_app`, an HTTP
  server AgentCore proxies to. It **starts the persona daemon at boot** and
  treats invocations as a control plane (`status`, `start`, `stop`, `say`,
  `room`).
- The daemon runs on its own thread with its own event loop.

Bare EC2 + systemd remains supported and documented; this is an additional
target, not a replacement. `dev/remote-driver/terraform/` is unchanged —
that is the research driver's own box, a different concern.

## Rationale

- **One runtime per persona is the whole point.** Each runtime carries its
  own IAM execution role, so `iam.json` least-privilege per persona finally
  has somewhere real to land. A single runtime for the panel would force
  every persona to share one identity, and the sample deliberately contains
  an AWS-MLOps persona that may spend money and a paediatrician persona that
  must not.
- **The daemon/invocation inversion is small and contained.** AgentCore is
  invocation-driven; a room participant has to be *listening*. Because the
  SDK's app is a long-lived HTTP server and an Instances session persists
  for days, the daemon simply runs inside that server. `NativeAgent` is
  untouched — the same class the systemd and compose deployments use.
- **A participant that only exists while being invoked is not a
  participant.** Hence autostart at boot rather than requiring a first
  invocation.
- **It removes the class of bug we already paid for.** AgentCore reaps
  instances on an idle timeout it enforces; a forgotten panel stops costing
  money without anyone remembering to run `terraform destroy`.
- **14 days matches what a deliberation actually needs.** The strongest
  criticism of our own benchmark harness is that it capped discussion at a
  handful of turns. An 8-hour ceiling would re-import that constraint into
  the platform.

## Consequences

- **VPC-only.** The subnets must route to the storm server — NAT gateway for
  a public server, or same-VPC placement. Documented in the deploy README.
- **Region-limited** to the nine regions that offer runtime instances. The
  deployer validates this rather than failing opaquely mid-apply.
- **Immutable resources.** Compute type is fixed at runtime creation, and a
  capacity provider can only have its description edited. Changing instance
  types or subnets means creating a new provider.
- **You cannot SSH into or patch the instances.** They are EC2 *managed*
  instances, hidden from console lists by default. That is the trade for not
  owning their lifecycle, and it is a real loss for debugging.
- **The broker story is not fully closed.** Stage-13 wants the broker in a
  separate process under a different uid with kernel-attested peer identity.
  Instances gives the OS access that makes this possible at all, but whether
  `SO_PEERCRED` uids are comparable across two agents sharing one instance
  depends on AgentCore's isolation and is **unverified**. Until it is, the
  shipped image runs agent-only and non-root, and the README states the two
  configurations with their trade-offs rather than implying the invariant is
  satisfied. This is the main open item from this ADR.
- **`bedrock-agentcore` becomes an optional dependency** (`[agentcore]`
  extra), so nothing changes for local or systemd deployments.

## Alternatives considered

- **Keep bare EC2 + systemd.** Works, and stays the documented path for
  people who want it. Rejected as the *primary* cloud target because we then
  keep owning patching, scaling, teardown, and the forgotten-instance
  failure mode — for infrastructure that is not the contribution.
- **microVM compute type.** Cheaper and faster to start, and genuinely right
  for a request/response agent. Rejected here: the 8-hour cap and one-agent-
  per-runtime model both cut against what a room is, and no OS access means
  no broker.
- **ECS Fargate task per persona** (what the architecture doc originally
  proposed). Comparable management burden to EC2, no shared filesystem
  between personas, and no GPU path for the ML-experimenter persona.
- **One runtime hosting all personas, multiplexed internally.** Fewer
  resources to manage, but collapses per-persona IAM into one role and makes
  a single crash take down the whole panel. The isolation is the feature.
- **Wait for Terraform support** and express this as HCL. The AWS provider
  has no `capacity_provider` resource yet, and the deployer needs to be
  usable now; a boto3 deployer that is idempotent and has `destroy` is close
  enough in practice. Revisit when the provider catches up.
