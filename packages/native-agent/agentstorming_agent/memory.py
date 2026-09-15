# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Compaction hints + restart mechanism."""

from __future__ import annotations

from pathlib import Path

COMPACT_HINTS_DIR = Path("/var/lib/agentstorming/compact-hints")


class RestartRequested(Exception):
    """Agent requested a clean restart (supervisor should restart us)."""

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason


def compact_hint_path(persona_name: str) -> Path:
    return COMPACT_HINTS_DIR / f"{persona_name}.md"


def save_compact_hint(persona_name: str, summary: str) -> Path:
    COMPACT_HINTS_DIR.mkdir(parents=True, exist_ok=True)
    p = compact_hint_path(persona_name)
    p.write_text(summary)
    return p


def consume_compact_hint(persona_name: str) -> str | None:
    p = compact_hint_path(persona_name)
    if not p.exists():
        return None
    try:
        content = p.read_text()
    except Exception:
        return None
    archive = COMPACT_HINTS_DIR / f"{persona_name}.consumed.md"
    try:
        p.replace(archive)
    except Exception:
        pass
    return content
