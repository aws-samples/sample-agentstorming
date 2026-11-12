# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Persona's private on-disk scratchpad."""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

#: A topic names one file in the scratchpad, so it may not carry a path. The
#: topic is typically chosen by the persona's model, which reads peer messages,
#: so a topic such as "../../x" is a prompt-injection away; without this check it
#: read or overwrote any .md file the agent process could reach.
_TOPIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ScratchpadStore:
    def __init__(self, persona_dir: Path) -> None:
        self.base = Path(persona_dir) / "scratchpad"
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, topic: str) -> Path:
        if not isinstance(topic, str) or not _TOPIC.fullmatch(topic):
            raise ValueError(
                f"invalid scratchpad topic {topic!r}: use letters, digits, '.', '_' or '-'"
            )
        path = (self.base / f"{topic}.md").resolve()
        # Resolving also follows a symlink planted in the scratchpad, so a link
        # pointing outside it is refused like a "../" topic is.
        if not path.is_relative_to(self.base.resolve()):
            raise ValueError(f"scratchpad topic {topic!r} resolves outside the scratchpad")
        return path

    def read(self, topic: str) -> str:
        p = self._path(topic)
        return p.read_text() if p.exists() else ""

    def write(self, topic: str, content: str) -> None:
        self._path(topic).write_text(content)

    def _confined_files(self) -> list[Path]:
        """Regular .md files that really are inside the scratchpad.

        ``load_all_as_prompt`` goes straight into the persona's system prompt,
        so it gets the same confinement as ``read``: a symlink planted in the
        scratchpad and pointing outside it is skipped, where it used to put
        the target file's contents into the prompt.
        """
        base = self.base.resolve()
        out = []
        for p in sorted(self.base.glob("*.md")):
            resolved = p.resolve()
            if resolved.is_relative_to(base) and resolved.is_file():
                out.append(p)
            else:
                log.warning("skipping scratchpad entry %s: it resolves outside the scratchpad", p.name)
        return out

    def list_topics(self) -> list[str]:
        """The topics ``read`` will accept. A file whose name is not a valid
        topic, written before topics were checked, is still loaded into the
        prompt but is not offered as a topic to read."""
        return [p.stem for p in self._confined_files() if _TOPIC.fullmatch(p.stem)]

    def load_all_as_prompt(self) -> str:
        parts = []
        for p in self._confined_files():
            parts.append(f"### {p.stem}\n{p.read_text().strip()}")
        return "\n\n".join(parts)
