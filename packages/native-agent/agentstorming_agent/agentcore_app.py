# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Host the native persona agent on Amazon Bedrock AgentCore Runtime.

## Why this module exists

The native agent is a *daemon*: it holds an SSE stream to the storm server
and decides on its own when to speak. AgentCore Runtime, by contrast, is
invocation-driven — you call `InvokeAgentRuntime` and an `@app.entrypoint`
function answers.

The two reconcile because the AgentCore SDK's app is a long-lived HTTP
server that AgentCore proxies requests to, and on the **Instances** compute
type the session (an AWS-managed EC2 instance in your account) persists for
up to 14 days. So the daemon runs continuously in the server process, and
invocations are a *control plane* over it: start, status, stop, say
something on the operator's behalf.

That inversion is the whole adapter. Nothing about the room protocol
changes; `NativeAgent` is used exactly as the systemd and docker-compose
deployments use it.

## Why Instances rather than microVMs

- microVM sessions cap at 8 hours. A deliberation that runs overnight, or a
  panel a human drops into the next morning, needs longer.
- One Instances session can host **multiple agents** (1:N) that share a
  filesystem. An Agent Storming room is exactly that shape: invoke one
  runtime per persona with the same `runtimeSessionId` and the whole panel
  lands on one host, each persona with its own IAM execution role.
- The credential broker must run as a separate process under a different
  uid (Stage-13 §broker invariant). That needs OS-level control, which the
  Instances compute type gives and the serverless model does not.

## Control surface

Every invocation is a JSON object with an ``action``:

    {"action": "status"}                    → loop health
    {"action": "start"}                     → idempotent; starts the daemon
    {"action": "stop"}                      → clean leave, then exit the loop
    {"action": "say", "text": "..."}        → post as this persona
    {"action": "room"}                      → current room state snapshot

``AGENTSTORMING_AUTOSTART=1`` (the default in the shipped image) starts the
daemon at container boot, so the persona joins the room without anyone
invoking it first — which is what "always running" has to mean for a
participant that is supposed to be listening.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("agentstorming.agentcore")

_PERSONA_DIR_ENV = "AGENTSTORMING_PERSONA_DIR"
_AUTOSTART_ENV = "AGENTSTORMING_AUTOSTART"


class AgentHost:
    """Owns the daemon's event loop on a private background thread.

    The AgentCore SDK serves invocations on its own asyncio loop. Running
    the persona loop on that same loop would mean a long deliberation
    blocks health checks and control calls, so the daemon gets a dedicated
    thread with its own loop and everything crossing the boundary goes
    through ``run_coroutine_threadsafe``.
    """

    def __init__(self, persona_dir: Path) -> None:
        self.persona_dir = persona_dir
        self._agent: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task_future: Any = None
        self._lock = threading.Lock()
        self._exit_code: int | None = None
        self._start_error: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Start the daemon if it isn't already running. Idempotent."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"started": False, "reason": "already_running",
                        **self._status_locked()}
            self._exit_code = None
            self._start_error = None
            ready = threading.Event()
            self._thread = threading.Thread(
                target=self._thread_main, args=(ready,),
                name="agentstorming-persona", daemon=True,
            )
            self._thread.start()
        # Wait briefly for construction so an immediate config error is
        # reported to the caller rather than only appearing in logs.
        ready.wait(timeout=30)
        if self._start_error:
            return {"started": False, "error": self._start_error}
        return {"started": True, **self.status()}

    def _thread_main(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            from .loop import NativeAgent
            self._agent = NativeAgent(self.persona_dir)
        except Exception as e:
            self._start_error = f"{type(e).__name__}: {e}"
            log.exception("persona construction failed")
            ready.set()
            return
        ready.set()
        try:
            self._exit_code = loop.run_until_complete(self._agent.run())
        except Exception as e:
            self._start_error = f"{type(e).__name__}: {e}"
            log.exception("persona loop crashed")
        finally:
            try:
                loop.close()
            finally:
                self._loop = None

    def stop(self, timeout: float = 30.0) -> dict[str, Any]:
        agent = self._agent
        if agent is None:
            return {"stopped": False, "reason": "not_running"}
        loop = self._loop
        if loop is not None and not loop.is_closed():
            # request_stop() sets asyncio.Events that belong to the daemon's
            # loop. Setting them from this thread is not thread-safe and,
            # worse, does not wake the waiter — the loop would only notice on
            # its next timeout, so a stop appeared to hang for ~30 seconds.
            # Marshal the call onto the owning loop instead.
            loop.call_soon_threadsafe(agent.request_stop)
        else:
            agent.request_stop()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        return {
            "stopped": thread is None or not thread.is_alive(),
            "exit_code": self._exit_code,
        }

    # -- introspection -----------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict[str, Any]:
        alive = self._thread is not None and self._thread.is_alive()
        out: dict[str, Any] = {
            "thread_alive": alive,
            "exit_code": self._exit_code,
            "error": self._start_error,
            "persona_dir": str(self.persona_dir),
        }
        if self._agent is not None:
            try:
                out.update(self._agent.status())
            except Exception as e:  # never let status raise
                out["status_error"] = f"{type(e).__name__}: {e}"
        return out

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- actions that must run on the daemon's loop ------------------------

    def _submit(self, coro_factory, timeout: float = 60.0):
        loop = self._loop
        if loop is None or self._agent is None:
            raise RuntimeError("persona is not running")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(self._agent), loop)
        return fut.result(timeout=timeout)

    def say(self, text: str) -> dict[str, Any]:
        if not text:
            raise ValueError("text is required")

        async def _post(agent):
            return await agent.client.post_message(text)

        env = self._submit(_post)
        return {"posted": True, "seq": (env or {}).get("seq")}

    def room(self) -> dict[str, Any]:
        async def _state(agent):
            return await agent.client.get_room_state()

        return self._submit(_state)


def _persona_dir() -> Path:
    raw = os.environ.get(_PERSONA_DIR_ENV)
    if not raw:
        raise RuntimeError(
            f"{_PERSONA_DIR_ENV} is not set — point it at the persona "
            "directory containing persona.yaml"
        )
    p = Path(raw).expanduser()
    if not (p / "persona.yaml").is_file():
        raise RuntimeError(f"{p}/persona.yaml not found")
    return p


def build_app():
    """Construct the AgentCore app. Imported lazily so the SDK is optional."""
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    from bedrock_agentcore.runtime.models import PingStatus

    app = BedrockAgentCoreApp()
    host = AgentHost(_persona_dir())

    @app.ping
    def _ping() -> PingStatus:
        # HealthyBusy while the persona is live tells AgentCore the session
        # is doing work, which keeps it from being reclaimed as idle mid
        # deliberation.
        return PingStatus.HEALTHY_BUSY if host.busy else PingStatus.HEALTHY

    @app.entrypoint
    def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
        action = (event or {}).get("action", "status")
        session_id = getattr(context, "session_id", None)
        try:
            if action == "status":
                result = host.status()
            elif action == "start":
                result = host.start()
            elif action == "stop":
                result = host.stop()
            elif action == "say":
                result = host.say((event or {}).get("text", ""))
            elif action == "room":
                result = host.room()
            else:
                return {"ok": False, "error": f"unknown action: {action!r}",
                        "actions": ["status", "start", "stop", "say", "room"]}
        except Exception as e:
            log.exception("action %s failed", action)
            return {"ok": False, "action": action,
                    "error": f"{type(e).__name__}: {e}",
                    "session_id": session_id}
        return {"ok": True, "action": action, "session_id": session_id,
                "result": result}

    if os.environ.get(_AUTOSTART_ENV, "1") not in ("0", "false", "False"):
        log.info("autostart enabled — joining the room at boot")
        host.start()

    return app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("AGENTSTORMING_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    port = int(os.environ.get("PORT", "8080"))
    # AgentCore proxies invocations to the container over its own network
    # namespace; binding loopback would make the agent unreachable and the
    # health check fail. The container is not on a public network.
    build_app().run(port=port, host="0.0.0.0")  # nosec B104 - AgentCore proxies to this port


if __name__ == "__main__":
    main()
