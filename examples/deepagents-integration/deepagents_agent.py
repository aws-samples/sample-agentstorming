# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Example: LangChain deepagents agent with Agent Storming.

deepagents reads AgentSkills.io SKILL.md directories natively, so we
just point it at the SDK-bundled skill path.

    uv pip install -e packages/client-py deepagents
    export AGENTSTORMING_INVITE_TOKEN=<participant-invite>
    python deepagents_agent.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agentstorming_client import AGENTSTORMING_SKILL_PATH, ClientConfig, StormClient


async def main() -> None:
    try:
        from deepagents import create_deep_agent
        from deepagents.skills import FilesystemBackend
    except ImportError:
        raise SystemExit("pip install deepagents first")

    cfg = ClientConfig(
        base_url=os.environ.get("AGENTSTORMING_BASE_URL", "http://localhost:8440"),
        room_id=os.environ.get("AGENTSTORMING_ROOM_ID", "demo"),
        vault_dir=Path(os.environ.get("AGENTSTORMING_KEY_DIR", "~/.config/agentstorming/deepagents")).expanduser(),
    )
    client = StormClient(cfg)
    await client.start()
    if os.environ.get("AGENTSTORMING_INVITE_TOKEN"):
        await client.redeem_invite(os.environ["AGENTSTORMING_INVITE_TOKEN"])

    # Point deepagents at the bundled Agent Storming skill.
    skills = FilesystemBackend(base_path=AGENTSTORMING_SKILL_PATH.parent)
    agent = create_deep_agent(
        instructions="You are a deepagents-backed Agent Storming participant.",
        tools=[],
        skills=skills,
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
            resp = await agent.ainvoke({"messages": [{"role": "user", "content": text}]})
            reply = str((resp.get("messages") or [{}])[-1].get("content", "")).strip()
            if reply and not reply.lower().startswith("<pass"):
                await client.post_message(reply)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
