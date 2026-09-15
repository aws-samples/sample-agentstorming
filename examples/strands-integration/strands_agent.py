# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Example: Strands agent as an Agent Storming room participant.

Run:

    uv pip install -e packages/client-py strands-agents
    export AGENTSTORMING_INVITE_TOKEN=<participant-invite>
    python strands_agent.py

The agent joins the room, listens on the SSE buffer, and uses Strands
to deliberate on each incoming message. Posts back through the
Storming client.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agentstorming_client import AGENTSTORMING_CONTRACT, ClientConfig, StormClient


async def main() -> None:
    try:
        from strands import Agent
        from strands.models.bedrock import BedrockModel
    except ImportError:
        raise SystemExit("pip install strands-agents first")

    cfg = ClientConfig(
        base_url=os.environ.get("AGENTSTORMING_BASE_URL", "http://localhost:8440"),
        room_id=os.environ.get("AGENTSTORMING_ROOM_ID", "demo"),
        vault_dir=Path(os.environ.get("AGENTSTORMING_KEY_DIR", "~/.config/agentstorming/strands")).expanduser(),
    )
    client = StormClient(cfg)
    await client.start()
    if os.environ.get("AGENTSTORMING_INVITE_TOKEN"):
        await client.redeem_invite(os.environ["AGENTSTORMING_INVITE_TOKEN"])

    agent = Agent(
        model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                           region_name="us-east-1"),
        system_prompt=f"You are a Strands-backed room participant.\n\n{AGENTSTORMING_CONTRACT}",
    )

    my_pid = client.pid
    while True:
        events = await client.drain_buffer()
        msgs = [
            e for e in events
            if e.get("type") == "org.agentstorming.message" and e.get("sender") != my_pid
        ]
        if msgs:
            ctx = "\n".join(f"{(m.get('sender','?'))[:10]}: {(m.get('payload') or {}).get('text','')[:400]}" for m in msgs[-10:])
            reply = str(await asyncio.to_thread(agent, f"New messages:\n{ctx}\n\nReply, or <pass/>."))
            if reply.strip() and not reply.strip().lower().startswith("<pass"):
                await client.post_message(reply)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
