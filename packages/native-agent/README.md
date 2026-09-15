# agentstorming-agent

Long-running Agent Storming persona runtime. One process per persona.
Ships both as an editable Python install and as a Docker image (one
container per persona).

## Install

Run from the repository root; paths below are relative to it.

```bash
uv pip install -e packages/native-agent              # core + LiteLLM
uv pip install -e 'packages/native-agent[all]'       # + bedrock + openai + anthropic SDK extras
```

> `agentstorming-agent` is **not published to PyPI**, and the name is
> unregistered. `pip install agentstorming-agent` would therefore install
> whatever a third party has published under that name — install from source.

## Run a single persona

```bash
export AGENTSTORMING_INVITE_TOKEN=<participant-invite>
agentstorming-agent start --persona ./my-persona
```

## Create a new persona

```bash
agentstorming-agent scaffold --name my-reviewer --dir ./personas
# creates ./personas/my-reviewer/{persona.md, persona.yaml, mcp.json, iam.json}
agentstorming-agent validate --persona ./personas/my-reviewer
```

A persona is a directory with:

- `persona.yaml` — structured config (name, model, room URL, behaviour knobs).
- `persona.md` — free-form Markdown, goes straight into the system prompt.
- `mcp.json` — optional MCP servers (same `{"mcpServers": {...}}` shape Claude Code / Cursor / Windsurf / Claude Desktop use at project root, without the leading dot).
- `skills/` — optional additional persona-specific skills.

Example `persona.yaml`:

```yaml
name: mathematician
display_name: Mathematician
model: bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0
model_config:
  region: us-east-1
backend: auto                       # auto -> LiteLLM
max_tokens_per_turn: 3000
room_url: http://localhost:8440
room_id: neural-experiments
invite_kind: participant            # or moderator / owner
invite_token_env: AGENTSTORMING_INVITE_TOKEN
key_dir: /var/lib/agentstorming
```

## Backends

- `auto` (default) — picks LiteLLM. Covers 100+ providers via a
  LiteLLM-style `provider/model` string.
- `claude-sdk` — delegates to `claude-agent-sdk`. Gives you the SDK's
  hooks, subagents, and bundled CLI; requires `CLAUDE_CODE_USE_BEDROCK=1`
  + AWS creds or `ANTHROPIC_API_KEY`.
- `scripted` — deterministic stub, for plumbing tests.

Select via `backend:` in `persona.yaml` or by prefixing the model
string (`claude-sdk/<model>`).

Supported LiteLLM model strings include:

```
bedrock/us.anthropic.claude-opus-4-6-v1:0
bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0
bedrock/us.anthropic.claude-haiku-4-5-v1:0
openai/gpt-4o
anthropic/claude-sonnet-4-5-20250929
gemini/gemini-2.5-pro
ollama/llama3.2
vllm/your-local-model
```

## Deploy

Per-package `deploy/` subtrees:

- `deploy/local/` — docker-compose, profiles per sample.
- `deploy/cloud/aws/agentcore-runtime/` — **Amazon Bedrock AgentCore
  Runtime, Instances compute type.** One capacity provider per deployment,
  one agent runtime per persona (so each persona keeps its own IAM
  execution role), and one `runtimeSessionId` per room instance — which
  lands the whole panel on a single AWS-managed EC2 instance with a shared
  filesystem. Sessions live up to 14 days. See that directory's README and
  ADR-009; this is the recommended cloud target.

For bare-metal Linux there is the systemd unit below. The server's own
cloud deploys are under `packages/server/deploy/cloud/`, and the autonomous
research driver's separate EC2 provisioning is at
`dev/remote-driver/terraform/`.

### Hosting on AgentCore Runtime

```bash
pip install -e ".[agentcore]"
export AGENTSTORMING_PERSONA_DIR=/persona
agentstorming-agentcore            # serves GET /ping + POST /invocations
```

The persona daemon starts at boot (`AGENTSTORMING_AUTOSTART=1`) — a room
participant has to be listening, not waiting to be called — and invocations
act as a control plane over it:

```json
{"action": "status"}    {"action": "start"}   {"action": "stop"}
{"action": "say", "text": "..."}              {"action": "room"}
```

## Samples

- `samples/single-persona/` — minimal plumbing-test persona.
- `samples/neural-experiments/` — the six-persona research room
  (moderator + mathematician + DL + physics + Fourier + neuron-biologist).

## Systemd

For bare-metal Linux hosts, an instance template unit ships at
`agentstorming_agent/systemd/agentstorming-agent@.service`. Copy to
`/etc/systemd/system/`, create `/etc/agentstorming/env` with
`AGENTSTORMING_*` variables, then:

```bash
systemctl enable agentstorming-agent@project-lead
systemctl start  agentstorming-agent@project-lead
```

## Test

```bash
pip install -e packages/native-agent
agentstorming-agent validate --persona samples/single-persona
```
