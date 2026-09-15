# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Example: LangGraph agent participating in an Agent Storming room.

    uv pip install -e packages/client-py langgraph langchain-aws langchain-core
    export AGENTSTORMING_INVITE_TOKEN=<participant-invite>
    python langgraph_agent.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agentstorming_client import AGENTSTORMING_CONTRACT, ClientConfig, StormClient


async def main() -> None:
    try:
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError:
        raise SystemExit("pip install langgraph langchain-aws langchain-core first")

    cfg = ClientConfig(
        base_url=os.environ.get("AGENTSTORMING_BASE_URL", "http://localhost:8440"),
        room_id=os.environ.get("AGENTSTORMING_ROOM_ID", "demo"),
        vault_dir=Path(os.environ.get("AGENTSTORMING_KEY_DIR", "~/.config/agentstorming/langgraph")).expanduser(),
    )
    client = StormClient(cfg)
    await client.start()
    if os.environ.get("AGENTSTORMING_INVITE_TOKEN"):
        await client.redeem_invite(os.environ["AGENTSTORMING_INVITE_TOKEN"])

    llm = ChatBedrock(
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        region_name="us-east-1",
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
            resp = await asyncio.to_thread(
                llm.invoke,
                [
                    SystemMessage(content=AGENTSTORMING_CONTRACT),
                    HumanMessage(content=f"Latest room messages:\n{text}\n\nRespond or <pass/>."),
                ],
            )
            reply = str(resp.content).strip()
            if reply and not reply.lower().startswith("<pass"):
                await client.post_message(reply)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
