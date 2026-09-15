# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Scripted-agent mode — no LLM, deterministic rules.

Purpose: end-to-end plumbing test for a room with many participants
without spending on Bedrock. Each scripted agent follows one of a few
behaviours decided by its persona.yaml ``script`` field.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
from pathlib import Path
from typing import Any

from agentstorming_client import ClientConfig, StormClient

from .config import load_persona

log = logging.getLogger("agentstorming.scripted")


class ScriptedAgent:
    """Runs a minimal scripted loop against a real Storm room."""

    def __init__(self, persona_dir: Path, script: str = "echo") -> None:
        self.cfg, self.persona_body = load_persona(persona_dir)
        self.script = script
        self.client = StormClient(ClientConfig(
            base_url=self.cfg.room_url,
            room_id=self.cfg.room_id,
            vault_dir=self.cfg.expanded_key_dir() / self.cfg.name,
        ))
        self._stop = asyncio.Event()

    async def run(self) -> int:
        await self.client.start()
        invite = os.environ.get(self.cfg.invite_token_env)
        if invite and not self.client.pid:
            await self.client.redeem_invite(invite, kind="participant")
            log.info("[%s] joined as %s", self.cfg.name, self.client.pid[:24])

        try:
            while not self._stop.is_set():
                events = await self.client.drain_buffer()
                for e in events:
                    await self._react(e)
                await asyncio.sleep(2 + random.random() * 3)
        finally:
            await self.client.stop()
        return 0

    async def _react(self, event: dict[str, Any]) -> None:
        if event.get("sender") == self.client.pid:
            return
        if event.get("type") != "org.agentstorming.message":
            return
        text = (event.get("payload") or {}).get("text", "")
        if self.script == "echo":
            await self.client.post_message(
                f"[{self.cfg.display_name}] heard: {text[:60]}"
            )
        elif self.script == "ack":
            await self.client.post_message(
                f"[{self.cfg.display_name}] acknowledged."
            )
        # mode=silent intentionally does nothing


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--persona", required=True)
    p.add_argument("--script", default="echo", choices=["echo", "ack", "silent"])
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    agent = ScriptedAgent(Path(args.persona), script=args.script)
    sys.exit(asyncio.run(agent.run()))


if __name__ == "__main__":
    main()
