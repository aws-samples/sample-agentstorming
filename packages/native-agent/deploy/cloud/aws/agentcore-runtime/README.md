# Persona panel on Amazon Bedrock AgentCore Runtime (Instances)

Runs a room's personas as AgentCore agent runtimes backed by AWS-managed EC2
instances in your own account. Replaces hand-rolled EC2 + systemd for cloud
deployments: AgentCore provisions, patches, scales, and tears down the
compute, and you keep EC2 pricing benefits because the instances are yours.

## Why the Instances compute type, not microVMs

| Need | microVMs | Instances |
|---|---|---|
| A deliberation that runs overnight | 8h session cap | **14 days** |
| Whole panel on one host, sharing a filesystem | one agent per runtime | **1:N per session** |
| Credential broker as a separate uid | no OS access | **direct OS access** |
| GPU for an ML-experimenter persona | not supported | `g5`/`g6`/`g6e`/`g7e`, drivers provisioned |

A room is a long-lived, multi-participant, stateful thing. That is the shape
Instances is built for.

## How a room maps onto AgentCore

```
capacity provider  ─── the EC2 fleet the panel runs on (one per deployment)
        │
        ├── agent runtime "panel_project_lead"   ─┐
        ├── agent runtime "panel_mathematician"   │  invoke all of them with
        ├── agent runtime "panel_physicist"       ├─ the SAME runtimeSessionId
        └── agent runtime "panel_…"              ─┘  → one instance, shared FS
```

One runtime per persona is deliberate: each gets its **own IAM execution
role**, which is where a persona's `iam.json` least-privilege policy belongs.
A single runtime for the whole panel would force every persona to share one
identity, and the point of the sample's AWS-MLOps persona is that it holds
permissions the paediatrician persona must not.

The `runtimeSessionId` is the panel's identity. Use one per room instance
(e.g. `room-demo-2026-09-04`) so a restart re-attaches the same EBS volumes
and the personas find their scratchpads intact.

## What runs in the container

`Dockerfile.agentcore` starts `agentstorming_agent.agentcore_app`, which is
an HTTP server AgentCore proxies invocations to. Two things matter:

1. **The persona daemon starts at boot** (`AGENTSTORMING_AUTOSTART=1`), so
   the participant is actually listening on its SSE stream without anyone
   invoking it first. A room participant that only exists while being called
   is not a participant.
2. **Invocations are a control plane**, not the agent's reason for living:

   ```bash
   {"action": "status"}                # loop health, turns taken, broker mode
   {"action": "start"} / {"action": "stop"}
   {"action": "say", "text": "..."}    # post as this persona
   {"action": "room"}                  # current room-state snapshot
   ```

The daemon runs on its own thread with its own event loop, so a multi-minute
deliberation never blocks a health check.

## Prerequisites

- A reachable storm server. The Instances compute type is **VPC-only**, so
  the subnets you give the capacity provider need a route to it — a NAT
  gateway for a public server, or same-VPC placement.
- An ECR image built for the capacity provider's architecture:

  ```bash
  # from the repository root; --platform MUST match operatingSystem
  docker build -f packages/native-agent/Dockerfile.agentcore \
    --platform linux/arm64 \
    -t "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/agentstorming/agent-agentcore:latest" .
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
  docker push "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/agentstorming/agent-agentcore:latest"
  ```
- The persona directory baked into the image or mounted at
  `AGENTSTORMING_PERSONA_DIR` (default `/persona`).
- A region that offers runtime instances: `us-east-1`, `us-east-2`,
  `us-west-2`, `ap-south-1`, `ap-southeast-1`, `ap-southeast-2`,
  `ap-northeast-1`, `eu-central-1`, `eu-west-1`.

## Deploy

```bash
cp panel.example.json panel.json    # then edit subnets, SGs, container_uri

./deploy.py plan    --config panel.json
./deploy.py apply   --config panel.json

# Bring the panel up. Every persona shares one session → one instance.
./deploy.py start   --config panel.json --session-id room-demo-001
./deploy.py status  --config panel.json --session-id room-demo-001

./deploy.py say --config panel.json --session-id room-demo-001 \
    --persona project-lead --text "Let us start with the KV-cache question."

./deploy.py stop    --config panel.json --session-id room-demo-001
./deploy.py destroy --config panel.json
```

Every step is idempotent — a partial `apply` can simply be repeated.

### What bounds the compute the operator role may launch

Two config keys are load-bearing for the IAM policy, not just for provisioning:

| Key | Default | What it bounds |
|---|---|---|
| `instance_types` | `["c7g.2xlarge"]` | becomes an `ec2:InstanceType` condition on the operator role's `RunInstances` grant |
| `max_volume_size_gb` | `200` | becomes an `ec2:VolumeSize` ceiling on volume creation; volumes must also be encrypted |

Set `instance_types` to whatever the panel actually needs — including the GPU
types above for an ML-experimenter persona — because the role is generated from
this list. Adding a type here widens the IAM grant, and leaving one out means
AgentCore cannot launch it even if you set it elsewhere.

`max_volume_size_gb` does not size anything. AgentCore takes the volume size
from the AMI; this is only the ceiling the role is permitted to create under.
Raise it if a persona needs a large working set.

The reason these live in IAM as well as in the capacity provider: the capacity
provider's `allowedInstanceTypes` is a configuration control, and
`ensure_capacity_provider()` skips a provider that already exists, so a
pre-existing one is never corrected. Anything holding the operator role's
credentials also calls `RunInstances` directly, where that configuration is not
consulted. The IAM conditions are the bound that holds in both cases.

## Cost

The instances are billed to your account at EC2 rates, plus an AgentCore
management fee. **A session is a running instance, not a paused one**: it
costs money while idle until `idleInstanceTimeout` (default 1h here) expires.
`destroy` deletes the capacity provider, which stops and deletes all of its
sessions *and their persistent volumes*.

## The credential broker on AgentCore — read this before enabling it

Stage-13 requires the credential broker to be a **separate process under a
different OS uid** from the agent, talking over a socket with kernel-attested
peer identity. The shipped image does not start a broker, and runs as
non-root, so it satisfies nothing about the broker invariant by itself.

Two ways to satisfy it here, with different trade-offs:

1. **Broker as its own agent in the same session.** Instances hosts multiple
   agents per session with a shared filesystem, so the broker can be a second
   runtime whose socket lives on the shared mount. Each runtime already gets
   its own IAM execution role, which is the separation Stage-13 is really
   after. **Open question:** whether `SO_PEERCRED` uids are comparable across
   two agents on one instance depends on how AgentCore isolates them, and we
   have not yet confirmed it. Until we have, do not rely on the broker's
   `allowed_uids` gate in this configuration.
2. **Broker and agent in one image, privileges dropped at start.** A root
   entrypoint spawns the broker as uid 1001 and the agent as uid 1000, so
   peer credentials are unambiguous. Costs you a root `USER` line, which
   container scanners will flag and which the v3 audit deliberately removed.

Whichever you choose, set the broker's `[room]` config so it can verify a
relayed mode transition rather than trusting the agent (ADR-008):

```toml
[room]
room_id = "demo"
mode = "planning"                 # start closed; the moderator promotes
server_pubkey = "…base64url…"     # from any metadata_snapshot
```

## Observability

AgentCore emits the same logs, metrics, and traces as the microVM compute
type. The execution role this deployer creates grants CloudWatch Logs and
X-Ray. Per-persona log groups appear under
`/aws/bedrock-agentcore/runtimes/<runtime-id>`.

## Known limits

- The compute type cannot be changed after a runtime is created; switching
  means recreating the runtime.
- A capacity provider is immutable apart from its description — to change
  instance types or subnets, create a new one.
- Managed instances are hidden from EC2 console lists by default and you
  cannot SSH into or patch them. That is the trade for AgentCore managing
  their lifecycle.
