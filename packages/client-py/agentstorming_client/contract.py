# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Expose the Agent Storming participation contract to Python callers.

The canonical form is the bundled SKILL.md directory at
``agentstorming_client/skill/``. Two convenience exports for frameworks
that can't consume skill files directly:

- ``AGENTSTORMING_SKILL_PATH``: absolute ``Path`` to the bundled skill
  directory — pass this to deepagents / CrewAI / pydantic-ai-skills
  / the Claude Agent SDK / etc.
- ``AGENTSTORMING_CONTRACT``: the SKILL.md body as a plain Markdown
  string — concat into any framework's system-prompt slot (LangChain
  core, Strands, Aider, etc.).
"""

from __future__ import annotations

from pathlib import Path

SKILL_DIR = Path(__file__).parent / "skill"
AGENTSTORMING_SKILL_PATH = SKILL_DIR
SKILL_MD = SKILL_DIR / "SKILL.md"


def _load_contract_body() -> str:
    raw = SKILL_MD.read_text(encoding="utf-8")
    # Strip the YAML frontmatter; callers concatenating into a system
    # prompt don't need the agent-skills metadata preamble.
    lines = raw.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            end = lines.index("---", 1)
            return "\n".join(lines[end + 1 :]).lstrip()
        except ValueError:
            return raw
    return raw


AGENTSTORMING_CONTRACT = _load_contract_body()
"""SKILL.md body (no YAML frontmatter), ready to concat into a system prompt."""
