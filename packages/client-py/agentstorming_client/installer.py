# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Install the Agent Storming skill into per-tool discovery paths.

Usage:

    python -m agentstorming install-skill --target claude-code

Each target maps to the directory layout the tool expects. The skill
itself (SKILL.md + references/) is bundled with this package and is
copied verbatim; no per-tool content transformation is needed.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

from .contract import AGENTSTORMING_SKILL_PATH


TARGETS = {
    "claude-code": Path.home() / ".claude/skills",
    "claude-code-project": Path(".claude/skills"),          # current repo
    "claude-desktop": Path.home() / ".claude/skills",        # shared with Claude Code in recent versions
    "codex": Path.home() / ".agents/skills",
    "codex-project": Path(".agents/skills"),
    "kiro": Path.home() / ".kiro/skills",
    "kiro-project": Path(".kiro/skills"),
    "windsurf": Path.home() / ".windsurf/skills",
    "cursor": Path(".cursor/rules"),
    "deepagents": Path("./skills"),
    "crewai": Path("./skills"),
    "pydantic-ai": Path("./skills"),
}


def list_targets() -> list[str]:
    return sorted(TARGETS.keys())


def install(target: str, skill_path: Path | None = None, force: bool = False) -> Path:
    """Copy the bundled skill directory into the requested tool path.

    Returns the final destination path of the skill directory.
    """
    if target not in TARGETS:
        raise ValueError(
            f"unknown target {target!r}; supported: {', '.join(list_targets())}"
        )
    src = Path(skill_path or AGENTSTORMING_SKILL_PATH)
    if not src.exists():
        raise FileNotFoundError(f"skill source missing: {src}")
    dst_dir = TARGETS[target].expanduser()
    dst_dir.mkdir(parents=True, exist_ok=True)

    if target == "cursor":
        # Cursor reads .cursor/rules/*.md. Flatten the skill body.
        body = (src / "SKILL.md").read_text(encoding="utf-8")
        out = dst_dir / "agentstorming.md"
        if out.exists() and not force:
            raise FileExistsError(f"{out} exists; pass --force to overwrite")
        out.write_text(body, encoding="utf-8")
        return out

    dst = dst_dir / "agentstorming"
    if dst.exists():
        if force:
            shutil.rmtree(dst)
        else:
            raise FileExistsError(f"{dst} exists; pass --force to overwrite")
    shutil.copytree(src, dst)
    return dst


def install_many(targets: Iterable[str], force: bool = False) -> dict[str, Path]:
    return {t: install(t, force=force) for t in targets}
