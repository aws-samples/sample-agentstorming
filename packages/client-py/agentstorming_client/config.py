# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Client configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ClientConfig(BaseModel):
    base_url: str
    room_id: str
    verify_tls: bool = True
    buffer_capacity: int = 10_000
    verify_signatures: bool = True
    allow_interruption: bool = False
    vault_backend: Literal["file", "memory"] = "file"
    vault_dir: Path | None = None
    access_token_refresh_margin_seconds: int = 60
    user_agent: str = Field(default="agentstorming-client/0.1")
