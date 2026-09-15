# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Persona's private on-disk scratchpad."""

from __future__ import annotations

from pathlib import Path


class ScratchpadStore:
    def __init__(self, persona_dir: Path) -> None:
        self.base = Path(persona_dir) / "scratchpad"
        self.base.mkdir(parents=True, exist_ok=True)

    def read(self, topic: str) -> str:
        p = self.base / f"{topic}.md"
        return p.read_text() if p.exists() else ""

    def write(self, topic: str, content: str) -> None:
        (self.base / f"{topic}.md").write_text(content)

    def list_topics(self) -> list[str]:
        return [p.stem for p in sorted(self.base.glob("*.md"))]

    def load_all_as_prompt(self) -> str:
        parts = []
        for p in sorted(self.base.glob("*.md")):
            parts.append(f"### {p.stem}\n{p.read_text().strip()}")
        return "\n\n".join(parts)
