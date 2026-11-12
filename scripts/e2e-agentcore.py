# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""End-to-end proof that a persona hosted on the AgentCore adapter joins a room.

## What this actually proves, and what it does not

Proves, with no AWS spend:

1. `agentstorming_agent.agentcore_app` serves the **AgentCore container
   contract** — `POST /invocations` and `GET /ping` — which is the interface
   AgentCore Runtime proxies to. If this works locally it works in the
   container, because AgentCore's side is a proxy, not a translation.
2. The control-plane inversion works: the persona daemon runs continuously on
   its own thread while invocations answer immediately. That is the whole point
   of the adapter and the thing most likely to be subtly broken, because a
   daemon and a request/response handler want different loops.
3. `HEALTHY_BUSY` is reported while the persona is live, which is what stops
   AgentCore reclaiming the session as idle mid-deliberation.
4. A message posted through an invocation arrives in the room, Ed25519-signed,
   and is verified by an independent client that shares no state with the agent.

Does **not** prove that AWS's managed infrastructure runs the image. That needs
a real deployment (`deploy/cloud/aws/agentcore-runtime/deploy.py apply`) and
costs money. What is left unproven there is AWS's side of the contract, not ours.

## Usage

    # a server must already be running and reachable
    AGENTSTORMING_BASE_URL=http://127.0.0.1:8440 \
    AGENTSTORMING_TEST_DSN=postgresql://agentstorming@127.0.0.1:5432/agentstorming_test \
    python scripts/e2e-agentcore.py

Exit code 0 on success. Output is TAP so it can be consumed like the scenario
runner.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import runpy
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = os.environ.get("AGENTSTORMING_BASE_URL", "http://127.0.0.1:8440")
DSN = os.environ.get(
    "AGENTSTORMING_TEST_DSN",
    "postgresql://agentstorming@127.0.0.1:5432/agentstorming_test",
)
AGENT_PORT = int(os.environ.get("AGENTSTORMING_E2E_AGENT_PORT", "8081"))
SAY_TEXT = f"agentcore-adapter-e2e-{int(time.time())}"

#: The agent's AgentCore entry point, run in a child process.
_AGENT_MODULE = "agentstorming_agent.agentcore_app"


def _run_agent(env: dict[str, str], log_path: str) -> None:
    """Child-process body: run the agent module as ``python -m`` would.

    The agent is launched with ``multiprocessing`` and ``runpy`` rather than
    ``subprocess`` with an argv. ``spawn`` starts the child on this same
    interpreter, so the harness keeps using the project venv it was started
    from, and no command line is ever built, so there is nothing for input to
    reach. The earlier form, ``Popen([sys.executable, "-m", ...])``, was
    already a fixed argv with no shell, but it was reported HIGH on every scan
    because the audit rule cannot tell ``sys.executable`` from a variable.
    """
    os.environ.update(env)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    runpy.run_module(_AGENT_MODULE, run_name="__main__", alter_sys=True)

_checks: list[tuple[bool, str]] = []


def check(ok: bool, name: str) -> bool:
    _checks.append((ok, name))
    print(f"{'ok' if ok else 'not ok'} {len(_checks)} - {name}", flush=True)
    return ok


def note(msg: str) -> None:
    print(f"  # {msg}", flush=True)


def _post(path: str, payload: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(  # nosec B310 - fixed http scheme, loopback
        f"http://127.0.0.1:{AGENT_PORT}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
        return json.loads(r.read().decode())


def _get(path: str, timeout: float = 10.0) -> tuple[int, str]:
    req = urllib.request.Request(  # nosec B310
        f"http://127.0.0.1:{AGENT_PORT}{path}", method="GET"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
        return r.status, r.read().decode()


async def _make_room() -> tuple[str, str, str]:
    """Create a room directly in the database; return (room_id, agent_invite, watcher_invite)."""
    from agentstorming_server.app import load_sql
    from agentstorming_server.domain.room import Room, RoomConfig
    from agentstorming_server.repo.base import Database
    from agentstorming_server.repo.invites import InviteRepo
    from agentstorming_server.repo.rooms import RoomRepo
    from agentstorming_server.services.sig import generate_keypair

    db = Database(DSN)
    await db.connect()
    schema_dir = (
        Path(__file__).resolve().parents[1]
        / "packages" / "server" / "agentstorming_server" / "schema"
    )
    sql = load_sql(schema_dir)
    if sql.strip():
        async with db.acquire() as conn:
            await conn.execute(sql)

    room_id = f"agentcore-e2e-{int(time.time())}"
    priv, pub = generate_keypair()
    now = datetime.now(timezone.utc)
    await RoomRepo(db).create(
        Room(id=room_id, state="ACTIVE",
             # raise_hand_required off: this proves the adapter and the
             # transport, not turn-taking, which has its own tests.
             config=RoomConfig(raise_hand_required=False),
             server_pubkey=pub, server_privkey=priv, created_at=now)
    )
    invites = InviteRepo(db)
    expires = now + timedelta(hours=1)
    _, agent_inv = await invites.create(room_id, "participant", expires)
    _, watch_inv = await invites.create(room_id, "participant", expires)
    await db.close()
    return room_id, agent_inv, watch_inv


def _write_persona(root: Path, room_id: str) -> Path:
    """A scripted persona: deterministic, and never calls an LLM."""
    d = root / "persona"
    d.mkdir(parents=True)
    (d / "persona.yaml").write_text(
        "name: e2e-agentcore\n"
        "display_name: AgentCore E2E\n"
        "backend: scripted\n"
        "model: scripted/stay-silent\n"
        "max_tokens_per_turn: 512\n"
        "compact_at_tokens: 120000\n"
        "tools: []\n"
        "skills: []\n"
        "triage:\n  enabled: false\n"
        "allow_interruption: false\n"
        f"room_url: {BASE}\n"
        f"room_id: {room_id}\n"
        "invite_token_env: AGENTSTORMING_INVITE_TOKEN\n"
        "invite_kind: participant\n"
        f"key_dir: {d / 'keys'}\n"
    )
    (d / "persona.md").write_text(
        "# AgentCore E2E persona\n\n"
        "Stays silent unless told to speak through an invocation. Exists to "
        "prove the adapter, so it must not depend on a model being reachable.\n"
    )
    return d


async def main() -> int:
    print("TAP version 14")

    room_id, agent_inv, watch_inv = await _make_room()
    note(f"room: {room_id}")

    tmp = Path(tempfile.mkdtemp(prefix="as-e2e-agentcore-"))
    proc = None
    watcher = None
    try:
        persona_dir = _write_persona(tmp, room_id)

        env = {
            "AGENTSTORMING_PERSONA_DIR": str(persona_dir),
            "AGENTSTORMING_INVITE_TOKEN": agent_inv,
            "AGENTSTORMING_AUTOSTART": "1",
            "PORT": str(AGENT_PORT),
        }
        agent_log = tmp / "agent.log"
        proc = multiprocessing.get_context("spawn").Process(
            target=_run_agent, args=(env, str(agent_log)), name="agentcore-agent",
        )
        proc.start()

        # --- 1. the container contract answers /ping -----------------------
        ready = False
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if not proc.is_alive():
                break
            try:
                status, body = _get("/ping", timeout=3)
                if status == 200:
                    ready = True
                    note(f"/ping -> {body.strip()[:80]}")
                    break
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(1.0)
        if not check(ready, "AgentCore container contract serves GET /ping"):
            if not proc.is_alive():
                out = agent_log.read_text(errors="replace") if agent_log.exists() else ""
                note(f"agent exited early (exit code {proc.exitcode}):")
                for line in out.splitlines()[-25:]:
                    note(line)
            return 1

        # --- 2. autostart actually joined ----------------------------------
        st = _post("/invocations", {"action": "status"})
        note(f"status: {json.dumps(st)[:300]}")
        running = bool(st.get("ok")) and bool((st.get("result") or {}).get("running"))
        check(running, "autostart joined the room without an invocation")

        # --- 3. HEALTHY_BUSY while live -------------------------------------
        _, ping_body = _get("/ping")
        check("Busy" in ping_body or "BUSY" in ping_body.upper(),
              "reports HealthyBusy while the persona is live")

        # --- 4. an independent client sees the agent, then its message ------
        from agentstorming_client import ClientConfig, StormClient
        watcher = StormClient(ClientConfig(
            base_url=BASE, room_id=room_id, vault_dir=tmp / "watcher"))
        await watcher.start()
        await watcher.redeem_invite(watch_inv)
        await asyncio.sleep(1.5)

        said = _post("/invocations", {"action": "say", "text": SAY_TEXT})
        note(f"say: {json.dumps(said)[:200]}")
        check(bool(said.get("ok")), "invocation 'say' accepted")

        seen = False
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not seen:
            for e in await watcher.drain_buffer():
                if (e.get("type") == "org.agentstorming.message"
                        and (e.get("payload") or {}).get("text") == SAY_TEXT):
                    seen = True
                    note(f"delivered from sender={e.get('sender')}")
                    break
            if not seen:
                await asyncio.sleep(0.25)
        check(seen, "message posted via invocation arrived, signature-verified")

        # --- 5. clean stop --------------------------------------------------
        t0 = time.monotonic()
        stopped = _post("/invocations", {"action": "stop"})
        elapsed = time.monotonic() - t0
        note(f"stop took {elapsed:.2f}s: {json.dumps(stopped)[:200]}")
        check(bool(stopped.get("ok")), "invocation 'stop' accepted")
        # The cross-thread stop bug this guards against showed up as a ~30s
        # stall, because asyncio Events were set from the wrong loop.
        check(elapsed < 10.0, f"stop returned promptly ({elapsed:.2f}s < 10s)")

    finally:
        if watcher is not None:
            try:
                await watcher.stop()
            except Exception:  # nosec B110 - teardown must not mask a result
                pass
        if proc is not None and proc.is_alive():
            proc.terminate()
            proc.join(timeout=15)
            if proc.is_alive():
                proc.kill()
                proc.join()
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for ok, n in _checks if not ok]
    print(f"1..{len(_checks)}")
    if failed:
        print(f"# FAILED {len(failed)}/{len(_checks)}: {'; '.join(failed)}")
        return 1
    print(f"# all {len(_checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
