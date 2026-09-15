# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""``agentstorming-agent`` CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .loop import NativeAgent


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentstorming-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Run the agent against a persona dir")
    start.add_argument("--persona", required=True, help="Path to persona directory")
    start.add_argument("--log-level", default="INFO")

    scaffold = sub.add_parser("scaffold", help="Create a new persona directory skeleton")
    scaffold.add_argument("--name", required=True)
    scaffold.add_argument("--dir", default=".")

    validate = sub.add_parser("validate", help="Validate a persona directory")
    validate.add_argument("--persona", required=True)

    args = parser.parse_args()

    if args.cmd == "start":
        logging.basicConfig(
            level=args.log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        agent = NativeAgent(Path(args.persona))
        code = asyncio.run(agent.run())
        sys.exit(code)
    elif args.cmd == "scaffold":
        _scaffold(args.name, Path(args.dir))
    elif args.cmd == "validate":
        from .config import load_persona
        cfg, _ = load_persona(Path(args.persona))
        print(
            f"persona {cfg.name} valid "
            f"(model={cfg.model}, backend={cfg.backend}, room={cfg.room_id}, "
            f"mcps={len(cfg.mcps)})"
        )


def _scaffold(name: str, base: Path) -> None:
    d = base / name
    if d.exists():
        print(f"ERROR: {d} already exists", file=sys.stderr)
        sys.exit(2)
    d.mkdir(parents=True)
    (d / "persona.md").write_text(f"# {name}\n\nWrite your persona's system prompt here.\n")
    (d / "persona.yaml").write_text(_template_yaml(name))
    (d / "mcp.json").write_text(_template_mcp_json())
    (d / "iam.json").write_text(_template_iam(name, _DEFAULT_SCAFFOLD_MODEL))
    print(f"scaffolded persona at {d}")


#: The model a freshly scaffolded persona starts on. Referenced by both the
#: YAML template and the IAM template so the policy is scoped to the model the
#: persona will actually invoke.
_DEFAULT_SCAFFOLD_MODEL = "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"


def _template_yaml(name: str) -> str:
    return f"""name: {name}
display_name: {name.replace('-', ' ').title()}

# LiteLLM-style provider/model string (see litellm docs for 100+ providers).
model: {_DEFAULT_SCAFFOLD_MODEL}
model_config:
  region: us-east-1

max_tokens_per_turn: 4000
compact_at_tokens: 120000

# Choose a specific backend ("auto" picks LiteLLM). "claude-sdk" uses
# the Claude Agent SDK; "scripted" uses a deterministic stub.
backend: auto

tools: []
skills: []

triage:
  enabled: false

allow_interruption: false

room_url: http://localhost:8440
room_id: default
invite_token_env: AGENTSTORMING_INVITE_TOKEN
invite_kind: participant
key_dir: ~/.config/agentstorming
"""


def _template_mcp_json() -> str:
    return json.dumps(
        {
            "mcpServers": {
                # "example": {"command": "npx", "args": ["-y", "some-mcp-server"]}
            }
        },
        indent=2,
    ) + "\n"


def _bedrock_arn_for(model: str) -> str:
    """Best-effort scoped Bedrock ARN for a LiteLLM model string.

    `bedrock/us.anthropic.claude-sonnet-4-5-...` is a cross-region inference
    profile, not a foundation model, so it needs the inference-profile ARN
    shape. Region and account stay as placeholders — this template is a
    starting point the operator completes, not a generated policy.
    """
    ident = model.split("/", 1)[1] if "/" in model else model
    if not ident:
        return "arn:aws:bedrock:REGION::foundation-model/MODEL-ID"
    # A leading geo prefix (us./eu./apac./global.) marks an inference profile.
    if ident.split(".", 1)[0] in ("us", "eu", "apac", "global"):
        return f"arn:aws:bedrock:REGION:ACCOUNT-ID:inference-profile/{ident}"
    return f"arn:aws:bedrock:REGION::foundation-model/{ident}"


def _template_iam(name: str = "PERSONA", model: str = "") -> str:
    """Least-privilege starting point for a persona's execution role.

    Deliberately NOT `"Resource": "*"`. Both bedrock:InvokeModel and the
    CloudWatch Logs write actions support resource-level scoping, and this
    template gets copied verbatim — a wildcard here becomes a wildcard in
    every deployment that scaffolds a persona. The placeholders are written
    so the file does not apply cleanly until they are filled in, which is the
    point: an agent's blast radius should be a decision, not a default.
    """
    model_arn = _bedrock_arn_for(model) if model else (
        "arn:aws:bedrock:REGION::foundation-model/MODEL-ID")
    return f"""{{
  "Version": "2012-10-17",
  "Statement": [
    {{"Sid": "InvokeOnlyThisPersonasModel",
     "Effect": "Allow",
     "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
     "Resource": [
       "{model_arn}"
     ]}},
    {{"Sid": "WriteOnlyThisPersonasLogs",
     "Effect": "Allow",
     "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
     "Resource": [
       "arn:aws:logs:REGION:ACCOUNT-ID:log-group:/agentstorming/{name}",
       "arn:aws:logs:REGION:ACCOUNT-ID:log-group:/agentstorming/{name}:log-stream:*"
     ]}}
  ]
}}
"""
