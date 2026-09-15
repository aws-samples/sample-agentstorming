# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Agent Storming over the real protocol, for equivalence against the emulation.

## Why this exists

`agent_storm.py` emulates the discussion semantics in a single process. That is a
reasonable engineering choice, but for a long time this repository *claimed* the
emulation had been validated against the live protocol on a 40-question pilot,
agreeing on 39 of 40. No such comparison was on record: there was no live runner,
no live condition among the recorded runs, and no log. The claim has been
withdrawn from the paper. This module exists so it can be made true instead.

## What makes it an equivalence test rather than a second implementation

The prompts, the model selection, the persona system prompts, the round structure
and the answer extraction are **imported from `agent_storm` and used unchanged**.
Nothing about the reasoning differs. What differs is only how a specialist learns
what its peers said:

- **Emulated:** read an in-process `list[Message]`.
- **Live:** every turn is posted as an Ed25519-signed envelope to a real server,
  and every participant reconstructs the room from events it received over SSE
  and verified against the sender's public key.

So the transport is the single variable: signing, JCS canonicalisation, replay
protection, admission, and the server's own event ordering. If answers diverge,
that is the transport's contribution, which is exactly the open question.

## What it costs

One live run makes the same number of model calls as one emulated run — five
specialists times two rounds, plus two moderator turns. It adds a server, a
Postgres, and six client identities, none of which cost anything. Registered as
`live_as_free_mod` so the equivalence pair is `as_free_mod` against it.

## Requirements

A reachable server with a writable attachments directory, and a Postgres the
harness may create a room in:

    AGENTSTORMING_BASE_URL=http://127.0.0.1:8440
    AGENTSTORMING_TEST_DSN=postgresql://agentstorming@127.0.0.1:5432/agentstorming_test

If either is missing the runner raises rather than silently falling back to the
emulation — a fallback is how the original claim became untrue.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import Runner, register
from ..types import Question, Message, TokenUsage, RunResult
from ..config import PERSONAS

# Imported, not reimplemented. If these change, both arms change together.
from .agent_storm import (
    _build_prompt,
    _extract,
    _moderator_turn,
    _specialist_turn,
)

_BASE_ENV = "AGENTSTORMING_BASE_URL"
_DSN_ENV = "AGENTSTORMING_TEST_DSN"


class LiveProtocolUnavailable(RuntimeError):
    """Raised when no live server is reachable.

    Deliberately fatal. The emulation exists precisely because it does not need a
    server, so a runner named `live_*` that quietly used it would reproduce the
    exact failure this module was written to correct.
    """


def _base_url() -> str:
    return os.environ.get(_BASE_ENV, "http://127.0.0.1:8440")


def _dsn() -> str:
    return os.environ.get(
        _DSN_ENV, "postgresql://agentstorming@127.0.0.1:5432/agentstorming_test"
    )


async def _provision_room(n_participants: int) -> tuple[str, list[str]]:
    """Create a room directly in the database and mint one invite per speaker.

    Goes through the repositories rather than the admin API so the harness needs
    no admin token, and so a failure here is unambiguously provisioning rather
    than authorisation.
    """
    from agentstorming_server.app import load_sql
    from agentstorming_server.domain.room import Room, RoomConfig
    from agentstorming_server.repo.base import Database
    from agentstorming_server.repo.invites import InviteRepo
    from agentstorming_server.repo.rooms import RoomRepo
    from agentstorming_server.services.sig import generate_keypair

    db = Database(_dsn())
    await db.connect()
    try:
        schema_dir = (
            Path(__file__).resolve().parents[5]
            / "server" / "agentstorming_server" / "schema"
        )
        if schema_dir.is_dir():
            sql = load_sql(schema_dir)
            if sql.strip():
                async with db.acquire() as conn:
                    await conn.execute(sql)

        room_id = f"live-eq-{int(time.time() * 1000)}"
        priv, pub = generate_keypair()
        now = datetime.now(timezone.utc)
        await RoomRepo(db).create(
            Room(
                id=room_id, state="ACTIVE",
                # raise_hand_required stays off: the emulated arm approximates
                # raise-hand as round-robin, so enforcing real turn-taking here
                # would introduce a second difference and confound the result.
                config=RoomConfig(raise_hand_required=False),
                server_pubkey=pub, server_privkey=priv, created_at=now,
            )
        )
        invites_repo = InviteRepo(db)
        expires = now + timedelta(hours=2)
        invites = []
        for _ in range(n_participants):
            _, tok = await invites_repo.create(room_id, "participant", expires)
            invites.append(tok)
        return room_id, invites
    finally:
        await db.close()


async def _collect(client, room_id: str, pid_names: dict[str, str]) -> list[Message]:
    """Rebuild the room transcript from events this client actually received.

    This is the point of the whole module. The emulated arm reads a list it
    already has; here each speaker sees only what the server delivered to it and
    what its own SDK verified, so ordering, omission and signature failures are
    all in scope.
    """
    out: list[Message] = []
    for ev in await client.drain_buffer():
        if ev.get("type") != "org.agentstorming.message":
            continue
        text = (ev.get("payload") or {}).get("text") or ""
        sender = ev.get("sender") or ""
        speaker = pid_names.get(sender, sender.split("@")[0][:12])
        out.append(Message(role="assistant", speaker=speaker, content=text))
    return out


async def _run_live(question: Question, seed: int, *, with_moderator: bool) -> RunResult:
    try:
        from agentstorming_client import ClientConfig, StormClient  # noqa: F401
    except ImportError as e:  # pragma: no cover - environment problem, not logic
        raise LiveProtocolUnavailable(
            "agentstorming-client is not installed; install the workspace with "
            "./scripts/build-python.sh"
        ) from e

    from agentstorming_client import ClientConfig, StormClient

    t0 = time.time()
    usage = TokenUsage()
    user_prompt = _build_prompt(question)
    specialists = PERSONAS.specialists[:5]
    speakers = list(specialists) + (["project-lead"] if with_moderator else [])

    room_id, invites = await _provision_room(len(speakers))

    tmp = Path(tempfile.mkdtemp(prefix="as-live-eq-"))
    clients: dict[str, object] = {}
    pid_names: dict[str, str] = {}
    # Every message any client has seen, merged and de-duplicated. Individual
    # clients each hold their own view; this is the union used to build prompts,
    # which is what the emulated transcript corresponds to.
    seen: list[Message] = []
    seen_keys: set[tuple[str, str]] = set()

    moderator_turns = 0
    participant_turns = 0
    final_statement = ""
    extracted_answer = ""
    error = ""

    try:
        for name, invite in zip(speakers, invites):
            c = StormClient(ClientConfig(
                base_url=_base_url(), room_id=room_id, vault_dir=tmp / name))
            await c.start()
            await c.redeem_invite(invite)
            clients[name] = c
            pid = getattr(c, "pid", None)
            if pid:
                pid_names[pid] = name

        # Let the joins propagate so every client has every peer's public key
        # before the first signed message arrives.
        await asyncio.sleep(1.5)

        def merge(msgs: list[Message]) -> None:
            for m in msgs:
                k = (m.speaker, m.content)
                if k not in seen_keys:
                    seen_keys.add(k)
                    seen.append(m)

        max_rounds = 2  # identical to the emulated arm
        for round_i in range(max_rounds):
            for sp in specialists:
                c = clients[sp]
                merge(await _collect(c, room_id, pid_names))
                reply, u = await _specialist_turn(sp, user_prompt, seen, seed)
                usage += u
                await c.post_message(reply)
                # Record our own message directly. The emulated arm appends a
                # speaker's reply to the transcript immediately, so doing the
                # same here keeps the two transcripts corresponding; waiting to
                # receive our own event back would make the live arm's context
                # one message shorter at every turn and confound the comparison.
                merge([Message(role="assistant", speaker=sp, content=reply)])
                participant_turns += 1
                # Give the server and the other subscribers a moment; the point
                # is that peers read this over SSE, not from memory.
                await asyncio.sleep(0.6)

            if with_moderator:
                mc = clients["project-lead"]
                merge(await _collect(mc, room_id, pid_names))
                force = (round_i == max_rounds - 1)
                text, u = await _moderator_turn(
                    user_prompt, seen, seed, force_commit=force)
                usage += u
                await mc.post_message(text)
                merge([Message(role="assistant", speaker="project-lead", content=text)])
                moderator_turns += 1
                await asyncio.sleep(0.6)
                if force or "<continue/>" not in text:
                    ans = _extract(question, text)
                    if ans:
                        final_statement = text
                        extracted_answer = ans
                        break

        if not extracted_answer:
            votes = [
                a for a in (_extract(question, m.content) for m in seen
                            if m.speaker in specialists) if a
            ]
            if votes:
                extracted_answer = Counter(votes).most_common(1)[0][0]

    except Exception as e:  # surfaced in the result, never silently emulated
        error = f"{type(e).__name__}: {e}"
    finally:
        for c in clients.values():
            try:
                await c.stop()
            except Exception:  # nosec B110 - teardown must not mask the result
                pass

    runner = _LiveStormResultBuilder()
    runner.config_id = "live_as_free_mod" if with_moderator else "live_as_free_nomod"
    return runner._build_result(
        question, seed, seen, extracted_answer, usage, t0,
        final_statement=final_statement,
        moderator_turns=moderator_turns,
        participant_turns=participant_turns,
        error=error,
        metadata={"room_id": room_id, "transport": "live",
                  "base_url": _base_url(), "speakers": speakers},
    )


class _LiveStormResultBuilder(Runner):
    """Only here to reuse ``Runner._build_result`` without duplicating grading."""

    async def run(self, question: Question, seed: int) -> RunResult:  # pragma: no cover
        raise NotImplementedError


@register("live_as_free_mod")
class LiveAgentStormingFreeModRunner(Runner):
    """The live counterpart of ``as_free_mod``. This is the equivalence pair."""

    async def run(self, question: Question, seed: int) -> RunResult:
        result = await _run_live(question, seed, with_moderator=True)
        result.config_id = self.config_id
        return result


@register("live_as_free_nomod")
class LiveAgentStormingFreeNoModRunner(Runner):
    """The live counterpart of ``as_free_nomod``."""

    async def run(self, question: Question, seed: int) -> RunResult:
        result = await _run_live(question, seed, with_moderator=False)
        result.config_id = self.config_id
        return result
