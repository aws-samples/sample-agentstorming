# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Persona configuration loader.

A persona is a directory with:

- ``persona.yaml``  — structured config, parsed into :class:`PersonaConfig`.
- ``persona.md``    — Markdown system-prompt body (free-form).
- ``mcp.json``      — optional MCP servers config
                      (schema: ``{"mcpServers": {"<name>": {...}}}``).
                      Legacy ``.mcp.json`` filenames are still read if
                      present but ``mcp.json`` is the preferred name
                      for personas.
- ``skills/``       — optional per-persona skill directories
                      (AgentSkills.io spec). Merged with the SDK's
                      canonical Agent Storming skill at startup.

The ``mcps`` field in :class:`PersonaConfig` has been removed in favour
of the sibling ``mcp.json`` file (``{"mcpServers": {...}}`` schema,
the same shape Claude Code, Cursor, Windsurf, and Claude Desktop use
at the repo root, minus the leading dot).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class MCPSpec(BaseModel):
    """An MCP server declaration."""

    name: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class TriageConfig(BaseModel):
    enabled: bool = False
    model: str = "bedrock/us.anthropic.claude-haiku-4-5-v1:0"
    max_tokens: int = 500


class BrokerConfig(BaseModel):
    """Storm credential-broker connection.

    When ``socket_path`` is set, the agent connects to the broker for
    plane-3 credentials and tool-call mediation (see Stage 13 spec).
    Default is unset — the agent runs broker-less for backward compat.
    """

    socket_path: str | None = None
    timeout_s: float = 30.0
    required: bool = False  # if True, agent refuses to run without broker


class ModelConfig(BaseModel):
    """Provider-specific model settings.

    The ``model`` field on :class:`PersonaConfig` is a LiteLLM-style
    string (e.g. ``bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0``
    or ``openai/gpt-4o`` or ``ollama/llama3.2``). These extras tune the
    call — region, base_url for self-hosted OpenAI-compatible endpoints,
    which env var holds the API key, per-call temperature, etc.
    """

    region: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    # Optional: when set, the agent asks the broker for the API key
    # via prepare_credentials(broker_provider) instead of reading
    # api_key_env from its own process environment. The broker holds
    # the secret and the agent never sees it via env.
    broker_provider: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    extra_params: dict[str, Any] = Field(default_factory=dict)


class PersonaConfig(BaseModel):
    name: str
    display_name: str

    # LiteLLM-style "provider/model" string.
    model: str = "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    model_config_: ModelConfig = Field(default_factory=ModelConfig, alias="model_config")

    # Legacy compatibility: accept ``bedrock_region: us-east-1`` and copy
    # into model_config.region automatically.
    bedrock_region: str | None = None

    max_tokens_per_turn: int = 4000
    compact_at_tokens: int = 120_000

    # The native-agent runtime chooses which backend to spawn. By default
    # it selects based on the provider prefix of ``model``; setting this
    # explicitly lets you force e.g. ``claude-sdk`` for richer Anthropic-
    # specific features (hooks, subagents).
    backend: str = "auto"

    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    triage: TriageConfig = Field(default_factory=TriageConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    allow_interruption: bool = False

    room_url: str
    room_id: str
    invite_token_env: str = "AGENTSTORMING_INVITE_TOKEN"
    invite_kind: str = "participant"  # participant | moderator | owner
    key_dir: str = "~/.config/agentstorming"

    # Populated at load time from the sibling .mcp.json file, if present.
    mcps: list[MCPSpec] = Field(default_factory=list)

    def expanded_key_dir(self) -> Path:
        return Path(self.key_dir).expanduser()

    class Config:
        populate_by_name = True


def _load_mcp_json(persona_dir: Path) -> list[MCPSpec]:
    """Read sibling mcp.json. Fall back to .mcp.json for legacy trees."""
    for name in ("mcp.json", ".mcp.json"):
        path = persona_dir / name
        if path.exists():
            break
    else:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"{path}: invalid JSON ({e})")
    servers = data.get("mcpServers", {})
    out: list[MCPSpec] = []
    for server_name, cfg in servers.items():
        out.append(MCPSpec(name=server_name, **cfg))
    return out


def load_persona(persona_dir: Path) -> tuple[PersonaConfig, str]:
    """Load persona.yaml + persona.md + (optional) mcp.json."""
    persona_dir = Path(persona_dir)
    yml = persona_dir / "persona.yaml"
    md = persona_dir / "persona.md"
    if not yml.exists():
        raise FileNotFoundError(f"persona.yaml not found in {persona_dir}")
    raw = yaml.safe_load(yml.read_text()) or {}
    # Legacy compatibility: move bedrock_region into model_config.region
    # if no explicit model_config is supplied.
    if "bedrock_region" in raw and "model_config" not in raw:
        raw.setdefault("model_config", {})["region"] = raw["bedrock_region"]
    cfg = PersonaConfig.model_validate(raw)
    if cfg.bedrock_region and not cfg.model_config_.region:
        cfg.model_config_.region = cfg.bedrock_region
    cfg.mcps = _load_mcp_json(persona_dir)
    body = md.read_text() if md.exists() else ""
    return cfg, body
