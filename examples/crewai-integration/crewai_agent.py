# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Example: one CrewAI crew acting as a single Agent Storming participant.

The crew (researcher + writer) jointly produces one response per
incoming message batch. The bridge posts the writer's final output
into the Storming room.

    uv pip install -e packages/client-py crewai
    export AGENTSTORMING_INVITE_TOKEN=<participant-invite>
    python crewai_agent.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agentstorming_client import AGENTSTORMING_CONTRACT, ClientConfig, StormClient


async def main() -> None:
    try:
        from crewai import Agent, Crew, Task
    except ImportError:
        raise SystemExit("pip install crewai first")

    cfg = ClientConfig(
        base_url=os.environ.get("AGENTSTORMING_BASE_URL", "http://localhost:8440"),
        room_id=os.environ.get("AGENTSTORMING_ROOM_ID", "demo"),
        vault_dir=Path(os.environ.get("AGENTSTORMING_KEY_DIR", "~/.config/agentstorming/crewai")).expanduser(),
    )
    client = StormClient(cfg)
    await client.start()
    if os.environ.get("AGENTSTORMING_INVITE_TOKEN"):
        await client.redeem_invite(os.environ["AGENTSTORMING_INVITE_TOKEN"])

    researcher = Agent(
        role="Researcher",
        goal="Find relevant context for questions arriving in the room.",
        backstory="You are a careful, citation-first researcher.",
        llm="bedrock/us.anthropic.claude-haiku-4-5-v1:0",
    )
    writer = Agent(
        role="Writer",
        goal="Draft a concise reply to the room using the researcher's findings.",
        backstory=f"You follow the Agent Storming contract:\n\n{AGENTSTORMING_CONTRACT}",
        llm="bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    )

    my_pid = client.pid
    while True:
        events = await client.drain_buffer()
        msgs = [
            e for e in events
            if e.get("type") == "org.agentstorming.message" and e.get("sender") != my_pid
        ]
        if msgs:
            ctx = "\n".join((m.get("payload") or {}).get("text", "")[:500] for m in msgs[-5:])
            t_research = Task(
                description=f"Summarise what's being discussed and what context a reply needs:\n{ctx}",
                agent=researcher,
                expected_output="bullet-pointed context notes",
            )
            t_write = Task(
                description="Draft a single short reply to the room. Respond '<pass/>' if no new point.",
                agent=writer,
                expected_output="one message for the room",
                context=[t_research],
            )
            crew = Crew(agents=[researcher, writer], tasks=[t_research, t_write])
            result = await asyncio.to_thread(crew.kickoff)
            reply = str(result).strip()
            if reply and not reply.lower().startswith("<pass"):
                await client.post_message(reply)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
