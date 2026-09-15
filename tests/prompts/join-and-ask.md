You are a coding agent running in headless mode. An Agent Storming MCP
server is available to you as the `agentstorming` MCP.

Your task:

1. Call the `agentstorming` MCP's `join_room` tool with:
   - base_url: http://localhost:8440
   - room_id: demo
   - invite_token: taken from the environment variable AGENTSTORMING_INVITE_TOKEN
2. Call `check_buffer` to see what's already in the room.
3. Post ONE message of at most 80 words asking the current moderator
   whether the discussion should prioritise architecture depth or
   benchmark coverage next.
4. Wait up to 60 seconds, calling `check_buffer` periodically, until
   you see the moderator reply.
5. Stop.

Do not post more than one question message. Do not summarise or
editorialise. Quote the moderator's reply verbatim if asked, but do
not post that quote back into the room.
