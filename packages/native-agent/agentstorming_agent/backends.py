# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Pluggable LLM backends for the native agent runtime.

The runtime does not care which provider answers a turn — it only
needs ``backend.deliberate(events, state) -> Decision``. Backends
implement that protocol.

Three backends ship today:

- :class:`LiteLLMBackend` — default. Uses the ``litellm`` library to
  route a LiteLLM-style ``provider/model`` string (e.g.
  ``bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0``,
  ``openai/gpt-4o``, ``ollama/llama3.2``, ...) to the appropriate
  provider. Covers 100+ providers via a single call site.

- :class:`ClaudeSDKBackend` — delegates to the Anthropic ``claude-agent-sdk``
  (which spawns the bundled ``claude`` CLI under the hood). Selected
  when the persona config sets ``backend: claude-sdk`` or when the
  model string starts with ``claude-sdk/``.

- :class:`ScriptedBackend` — deterministic, for tests + smoke runs.

Backend selection happens in :func:`make_backend` based on
``PersonaConfig.backend`` and the ``model`` string prefix.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import PersonaConfig
from .memory import consume_compact_hint
from .scratchpad import ScratchpadStore

log = logging.getLogger(__name__)


_LOGIN_ERROR_MARKERS = (
    "Not logged in",
    "Please run /login",
    "Invalid API key",
    "API key is required",
    "authentication failed",
)


@dataclass
class Decision:
    speak: bool
    text: str = ""
    raise_hand: bool = False
    hint: str = ""


class Backend(Protocol):
    """What the observe/deliberate/speak loop needs from a backend."""

    async def deliberate(self, events: list[dict], state: dict) -> Decision: ...


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------


_RELEVANT_TYPES = {
    "org.agentstorming.message",
    "org.agentstorming.attachment",
    "org.agentstorming.summary_updated",
}


def _format_events_for_llm(events: list[dict], state: dict) -> str:
    relevant = [e for e in events if e.get("type") in _RELEVANT_TYPES]

    def short_id(pid: str) -> str:
        return pid.split("@")[0][:10] if pid else "?"

    lines: list[str] = []

    # Room-level context: startup documents + rolling summary always come
    # first so the moderator/specialists ground their reply in the brief.
    docs = state.get("documents") or []
    if docs:
        lines.append("## Room documents (set at room creation):")
        for d in docs:
            title = d.get("title") or "(untitled)"
            dtype = d.get("type") or "reference"
            body = d.get("body") or ""
            lines.append(f"### {title}  ({dtype})")
            if body:
                # Cap each doc to ~2k chars to keep the context tight.
                lines.append(body.strip()[:2000])
            lines.append("")
    summary = state.get("summary")
    if summary:
        lines.append("## Current rolling summary:")
        lines.append(summary.strip())
        lines.append("")

    if relevant:
        lines.append("## New messages in the room (oldest -> newest):")
        for e in relevant[-15:]:
            sender = short_id(e.get("sender") or "")
            text = (e.get("payload") or {}).get("text", "")
            if e.get("type") == "org.agentstorming.summary_updated":
                lines.append("- [summary updated by moderator]")
                continue
            lines.append(f"- {sender}: {text.strip()[:600]}")
    else:
        # First boot or no messages yet — still let the moderator know
        # the room is open and tell it to look at the brief above.
        lines.append("## No messages have been posted yet.")
        if docs:
            lines.append(
                "The room has just been opened. Read the document(s) above and, "
                "if you are the moderator, post the opening turn that convenes "
                "the room around the brief."
            )

    lines.append("")
    mod_pid = state.get("moderator_pid")
    if mod_pid:
        lines.append(f"Moderator: {short_id(mod_pid)}")
    parts = state.get("participants", [])
    if parts:
        others = [short_id(p.get("pid", "")) for p in parts]
        lines.append(f"Participants: {', '.join(others)}")
    lines.append("")
    lines.append(
        "Reply as yourself to the most recent messages. If you have "
        "nothing new to add, reply with exactly `<pass/>`."
    )
    return "\n".join(lines)


def build_system_prompt(persona_dir: Path, cfg: PersonaConfig) -> str:
    """Compose persona body + Agent Storming contract + prior memory + scratchpad."""
    persona_md = persona_dir / "persona.md"
    body = persona_md.read_text() if persona_md.exists() else f"You are {cfg.display_name}."

    try:
        from agentstorming_client import AGENTSTORMING_CONTRACT
        contract = AGENTSTORMING_CONTRACT
    except Exception as e:  # pragma: no cover — only fires in a broken install
        log.warning("Agent Storming contract unavailable: %s", e)
        contract = ""

    prior = consume_compact_hint(cfg.name) or ""
    sp = ScratchpadStore(persona_dir).load_all_as_prompt()
    parts = [body, contract]
    if prior:
        parts.append("## Prior-session memory\n" + prior)
    if sp:
        parts.append("## Your scratchpad\n" + sp)
    return "\n\n---\n\n".join(p for p in parts if p.strip())


def _post_process(raw: str) -> Decision:
    joined = (raw or "").strip()
    if not joined:
        return Decision(speak=False)
    if joined.lower().startswith("<pass"):
        return Decision(speak=False)
    matched = next((m for m in _LOGIN_ERROR_MARKERS if m in joined), None)
    if matched is not None:
        # Log which marker matched and how long the text was — not the text.
        # This used to log `joined[:120]`. A truncated slice is still content:
        # what the backend returns here is derived from what participants said
        # in the room, and 120 characters is plenty to leak a sentence of it
        # into an operator's log. The marker name is what identifies the
        # failure; the body adds nothing diagnostic.
        log.error(
            "Backend returned auth-failure text, suppressing: marker=%r chars=%d",
            matched, len(joined),
        )
        return Decision(speak=False)
    return Decision(speak=True, text=joined)


# ----------------------------------------------------------------------
# LiteLLM backend (default)
# ----------------------------------------------------------------------


class LiteLLMBackend:
    """Universal LLM backend via the ``litellm`` library.

    The LiteLLM-style ``provider/model`` string in
    :attr:`PersonaConfig.model` is passed straight through. Extra
    per-provider config comes from :attr:`PersonaConfig.model_config_`.
    """

    def __init__(self, persona_dir: Path, cfg: PersonaConfig,
                 broker: Any | None = None) -> None:
        self.cfg = cfg
        self.persona_dir = persona_dir
        self.system_prompt = build_system_prompt(persona_dir, cfg)
        self.broker = broker
        self._apply_provider_env(cfg)

    @staticmethod
    def _apply_provider_env(cfg: PersonaConfig) -> None:
        mc = cfg.model_config_
        if mc.region:
            os.environ.setdefault("AWS_REGION", mc.region)
            os.environ.setdefault("AWS_REGION_NAME", mc.region)
        if mc.base_url and cfg.model.startswith("openai_compatible/") or \
                cfg.model.startswith(("ollama/", "vllm/", "lm_studio/")):
            os.environ.setdefault("OPENAI_BASE_URL", mc.base_url)

    async def deliberate(self, events: list[dict], state: dict) -> Decision:
        try:
            import litellm  # type: ignore
        except Exception as e:
            log.error("litellm import failed: %s", e)
            return Decision(speak=False)

        user = _format_events_for_llm(events, state)
        if user == "<no_new_content/>":
            return Decision(speak=False)

        mc = self.cfg.model_config_
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user},
            ],
            "max_tokens": self.cfg.max_tokens_per_turn,
        }
        if mc.region:
            kwargs["aws_region_name"] = mc.region
        if mc.base_url:
            kwargs["api_base"] = mc.base_url
        # Credential resolution priority:
        #   1. broker_provider (broker holds the secret)  — strongest
        #   2. api_key_env (read from process env)        — back-compat
        if mc.broker_provider and self.broker is not None:
            try:
                creds = self.broker.prepare_credentials(
                    provider=mc.broker_provider,
                    trust="user",
                    room_id=self.cfg.room_id,
                )
                if creds and creds.get("credentials"):
                    api_key = creds["credentials"].get("api_key")
                    if api_key:
                        kwargs["api_key"] = api_key
            except Exception:
                log.exception("broker credential fetch failed; falling back to env")
        if "api_key" not in kwargs and mc.api_key_env and os.environ.get(mc.api_key_env):
            kwargs["api_key"] = os.environ[mc.api_key_env]
        if mc.temperature is not None:
            kwargs["temperature"] = mc.temperature
        if mc.top_p is not None:
            kwargs["top_p"] = mc.top_p
        for k, v in (mc.extra_params or {}).items():
            kwargs.setdefault(k, v)

        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception:
            log.exception("litellm.acompletion failed; staying silent")
            return Decision(speak=False)

        try:
            content = resp["choices"][0]["message"]["content"] or ""
        except Exception:
            # Log the shape, never the body. A completion response carries the
            # model's reply, which is derived from what participants said in the
            # room, so `%r` on the whole object wrote room content into the
            # application log on every malformed response. The keys and the
            # model id are what actually help diagnose a shape mismatch.
            log.warning(
                "unexpected litellm response shape: type=%s keys=%s model=%s",
                type(resp).__name__,
                sorted(resp.keys()) if hasattr(resp, "keys") else "n/a",
                (resp.get("model") if hasattr(resp, "get") else None) or "unknown",
            )
            return Decision(speak=False)
        return _post_process(content)


# ----------------------------------------------------------------------
# Claude Agent SDK backend
# ----------------------------------------------------------------------


class ClaudeSDKBackend:
    """Delegate to ``claude-agent-sdk`` (spawns the bundled Claude CLI)."""

    def __init__(self, persona_dir: Path, cfg: PersonaConfig) -> None:
        self.cfg = cfg
        self.persona_dir = persona_dir
        self.system_prompt = build_system_prompt(persona_dir, cfg)
        try:
            import claude_agent_sdk  # noqa: F401
            self._ok = True
        except Exception:
            self._ok = False
        os.environ.setdefault("CLAUDE_CODE_USE_BEDROCK", "1")
        mc = cfg.model_config_
        if mc.region:
            os.environ.setdefault("AWS_REGION", mc.region)
            os.environ.setdefault("ANTHROPIC_BEDROCK_REGION", mc.region)

    async def deliberate(self, events: list[dict], state: dict) -> Decision:
        if not self._ok:
            return Decision(speak=False)

        user = _format_events_for_llm(events, state)
        if user == "<no_new_content/>":
            return Decision(speak=False)

        try:
            from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # type: ignore

            model = self.cfg.model
            if model.startswith("claude-sdk/"):
                model = model.split("/", 1)[1]
            opts = ClaudeAgentOptions(model=model, system_prompt=self.system_prompt)
            async with ClaudeSDKClient(options=opts) as client:
                await client.query(user)
                parts: list[str] = []
                async for msg in client.receive_response():
                    for blk in getattr(msg, "content", []) or []:
                        t = getattr(blk, "text", None)
                        if t:
                            parts.append(t)
        except Exception:
            log.exception("Claude SDK call failed; staying silent")
            return Decision(speak=False)

        return _post_process("\n".join(parts))


# ----------------------------------------------------------------------
# Scripted backend (deterministic, for tests)
# ----------------------------------------------------------------------


class ScriptedBackend:
    """Emit a fixed string on every deliberation. Useful for plumbing tests."""

    def __init__(self, persona_dir: Path, cfg: PersonaConfig, *, reply: str | None = None) -> None:
        self.cfg = cfg
        self.persona_dir = persona_dir
        self.system_prompt = build_system_prompt(persona_dir, cfg)
        self._reply = reply if reply is not None else f"<pass/>"

    async def deliberate(self, events: list[dict], state: dict) -> Decision:
        return _post_process(self._reply)


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def make_backend(persona_dir: Path, cfg: PersonaConfig,
                 broker: Any | None = None) -> Backend:
    choice = (cfg.backend or "auto").lower()
    if choice == "scripted":
        return ScriptedBackend(persona_dir, cfg)
    if choice == "claude-sdk" or cfg.model.startswith("claude-sdk/"):
        return ClaudeSDKBackend(persona_dir, cfg)
    if choice == "litellm":
        return LiteLLMBackend(persona_dir, cfg, broker=broker)
    # auto — pick LiteLLM unless the user explicitly asked for the SDK.
    return LiteLLMBackend(persona_dir, cfg, broker=broker)
