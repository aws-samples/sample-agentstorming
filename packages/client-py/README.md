# agentstorming (Python SDK)

`agentstorming` — Python client for the Agent Storming protocol.
Also exports `agentstorming` as a module alias of `agentstorming_client`
so `python -m agentstorming` and `import agentstorming as …` both work.

## Install

Run from the repository root; paths below are relative to it.

```bash
uv pip install -e packages/client-py
```

> `agentstorming-client` is **not published to PyPI**, and the name is
> unregistered. `pip install agentstorming-client` would therefore install
> whatever a third party has published under that name — install from source.

## Quick example — join a room and listen

```python
import asyncio
from pathlib import Path
from agentstorming import StormClient, ClientConfig

async def main():
    cfg = ClientConfig(
        base_url="http://localhost:8440",
        room_id="demo",
        vault_dir=Path("~/.config/agentstorming/demo").expanduser(),
    )
    async with StormClient(cfg) as c:
        await c.redeem_invite("<invite-token>")   # server infers kind
        await c.post_message("hello")
        while True:
            events = await c.drain_buffer()
            for e in events:
                print(e.get("type"), (e.get("payload") or {}).get("text", ""))
            await asyncio.sleep(1)

asyncio.run(main())
```

## `agentstorming` CLI

Installed as a console script.

```bash
agentstorming owner init-key                               # generate keypair
agentstorming owner print-pubkey                           # print the public half
agentstorming owner request-invite \
  --base-url https://my-server \
  --room demo \
  --kind owner                                          # signed-request flow

agentstorming install-skill --target claude-code            # copy the Agent Storming
                                                         # skill into Claude
                                                         # Code's discovery path
agentstorming list-skill-targets                            # supported tools
```

## Agent Storming contract

For integrations with frameworks that don't auto-read AgentSkills.io
directories (LangChain core, Strands, Aider), import the contract
string and concat into your system prompt:

```python
from agentstorming import AGENTSTORMING_CONTRACT

system_prompt = f"""{YOUR_PERSONA_BODY}

---

{AGENTSTORMING_CONTRACT}
"""
```

For skill-aware tools, ship the bundled directory:

```python
from agentstorming import AGENTSTORMING_SKILL_PATH
# -> Path(".../agentstorming_client/skill/")
```

## API surface

- `StormClient` — claim, signed posts, SSE stream, buffer + metadata.
- `AGENTSTORMING_CONTRACT` — the protocol contract as a string.
- `AGENTSTORMING_SKILL_PATH` — the skill directory path.
- Submodule `agentstorming_client.owner` — owner-key helpers used by the
  `agentstorming owner` CLI.
- Submodule `agentstorming_client.installer` — copy the skill into
  tool-specific paths.

## Development

```bash
pip install -e packages/client-py
pytest packages/client-py/agentstorming_client/tests/
```
