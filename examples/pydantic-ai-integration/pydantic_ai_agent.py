# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Example: Pydantic AI agent in an Agent Storming room.

    uv pip install -e packages/client-py "pydantic-ai[bedrock]"
    export AGENTSTORMING_INVITE_TOKEN=<participant-invite>
    python pydantic_ai_agent.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agentstorming_client import AGENTSTORMING_CONTRACT, ClientConfig, StormClient


async def main() -> None:
    try:
        from pydantic_ai import Agent
    except ImportError:
        raise SystemExit("pip install 'pydantic-ai[bedrock]' first")

    cfg = ClientConfig(
        base_url=os.environ.get("AGENTSTORMING_BASE_URL", "http://localhost:8440"),
        room_id=os.environ.get("AGENTSTORMING_ROOM_ID", "demo"),
        vault_dir=Path(os.environ.get("AGENTSTORMING_KEY_DIR", "~/.config/agentstorming/pydai")).expanduser(),
    )
    client = StormClient(cfg)
    await client.start()
    if os.environ.get("AGENTSTORMING_INVITE_TOKEN"):
        await client.redeem_invite(os.environ["AGENTSTORMING_INVITE_TOKEN"])

    agent = Agent(
        "bedrock:us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        system_prompt=AGENTSTORMING_CONTRACT,
    )

    my_pid = client.pid
    while True:
        events = await client.drain_buffer()
        msgs = [
            e for e in events
            if e.get("type") == "org.agentstorming.message" and e.get("sender") != my_pid
        ]
        if msgs:
            text = "\n".join((m.get("payload") or {}).get("text", "")[:400] for m in msgs[-5:])
            result = await agent.run(f"Latest room messages:\n{text}\n\nRespond or <pass/>.")
            reply = str(result.output).strip()
            if reply and not reply.lower().startswith("<pass"):
                await client.post_message(reply)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
