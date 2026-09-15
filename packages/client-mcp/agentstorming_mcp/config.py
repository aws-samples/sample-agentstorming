# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""MCP server configuration from env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MCPConfig:
    url: str
    room: str
    invite_token: str | None
    key_dir: Path
    log_level: str

    @classmethod
    def from_env(cls) -> "MCPConfig":
        url = os.environ.get("AGENTSTORMING_URL")
        room = os.environ.get("AGENTSTORMING_ROOM")
        if not url or not room:
            raise RuntimeError("AGENTSTORMING_URL and AGENTSTORMING_ROOM are required")
        key_dir = Path(os.environ.get("AGENTSTORMING_KEY_DIR", str(Path.home() / ".config" / "agentstorming")))
        return cls(
            url=url,
            room=room,
            invite_token=os.environ.get("AGENTSTORMING_INVITE_TOKEN"),
            key_dir=key_dir,
            log_level=os.environ.get("AGENTSTORMING_LOG_LEVEL", "INFO"),
        )
