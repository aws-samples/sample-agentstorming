# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Small-LLM triage pre-filter.

Cheap gate that decides whether the expensive main-model deliberation
is worth running on a batch of incoming events. Honours the
``triage`` block of ``persona.yaml``:

```yaml
triage:
  enabled: true
  model: bedrock/us.anthropic.claude-haiku-4-5-v1:0
  max_tokens: 200
```

Returns:

- ``True``  → run the main backend.
- ``False`` → stay silent this turn.

Implementation uses the same LiteLLM that the main backend uses. If
LiteLLM is unavailable the triage silently returns True (conservative:
let the main call run).

§6.8 ``allow_interruption`` note: actually aborting an in-flight
main-LLM call is provider-specific and only feasible for native
runtimes with full event-loop control; the native agent honours the
flag by declining to swallow a triage-positive event while another
deliberation is already queued. Agentic-coding CLIs (Claude Code /
Cursor / Kiro / Codex) ignore the flag because they don't own the
event loop.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import PersonaConfig

log = logging.getLogger(__name__)


_SYSTEM = (
    "You are a triage gate. Decide if a larger model should reply to the "
    "latest messages in an Agent Storming discussion room, or if the room "
    "has nothing that needs a reply yet. Reply with exactly one word: "
    "'RESPOND' or 'PASS'. 'RESPOND' means the main model should draft a "
    "message. 'PASS' means stay silent this turn."
)


async def maybe_triage(cfg: PersonaConfig, events: list[dict], state: dict) -> bool:
    if not getattr(cfg.triage, "enabled", False):
        return True
    try:
        import litellm  # type: ignore
    except Exception:
        return True

    # Compact context: last 10 messages.
    lines = []
    for e in events[-10:]:
        if e.get("type") not in (
            "org.agentstorming.message",
            "org.agentstorming.attachment",
            "org.agentstorming.hand_raised",
        ):
            continue
        s = (e.get("sender") or "?")[:10]
        t = (e.get("payload") or {}).get("text", "")[:400]
        lines.append(f"- {s}: {t}")
    if not lines:
        return False
    user = "Recent room messages:\n" + "\n".join(lines) + "\n\nShould I respond?"

    try:
        resp = await litellm.acompletion(
            model=cfg.triage.model,
            max_tokens=getattr(cfg.triage, "max_tokens", 200),
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        text = (resp["choices"][0]["message"]["content"] or "").strip().upper()
    except Exception as e:
        log.debug("triage failed, defaulting to RESPOND: %s", e)
        return True
    return "RESPOND" in text
