# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Main observe/deliberate/speak loop."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from agentstorming_client import ClientConfig, StormClient
from agentstorming_client.broker_client import BrokerClient, BrokerError
from agentstorming_client.errors import StormError

from .backends import make_backend
from .config import PersonaConfig, load_persona
from .memory import RestartRequested
from .scratchpad import ScratchpadStore
from .triage import maybe_triage

log = logging.getLogger("agentstorming.native")


class NativeAgent:
    def __init__(self, persona_dir: Path) -> None:
        self.persona_dir = Path(persona_dir)
        self.cfg, self.persona_body = load_persona(self.persona_dir)
        self.client = StormClient(ClientConfig(
            base_url=self.cfg.room_url,
            room_id=self.cfg.room_id,
            vault_dir=self.cfg.expanded_key_dir() / self.cfg.name,
        ))
        # Build the broker FIRST so the backend can route credential
        # fetches through it instead of reading process env vars.
        self.broker = self._build_broker()
        self.backend = make_backend(self.persona_dir, self.cfg, broker=self.broker)
        self.scratchpad = ScratchpadStore(self.persona_dir)
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        # Observability for supervisors that host this loop rather than
        # exec'ing it (see agentcore_app.py).
        self.started_at: float | None = None
        self.turns_taken = 0
        self.messages_posted = 0
        self.last_error: str | None = None

    def request_stop(self) -> None:
        """Ask the loop to finish its current turn and exit cleanly."""
        self._stopping.set()
        self._wake.set()

    def status(self) -> dict:
        """A snapshot of loop health, safe to serialise into a response."""
        import time
        return {
            "persona": self.cfg.name,
            "room_id": self.cfg.room_id,
            "room_url": self.cfg.room_url,
            "pid": self.client.pid,
            "running": not self._stopping.is_set(),
            "uptime_s": None if self.started_at is None
            else round(time.monotonic() - self.started_at, 1),
            "turns_taken": self.turns_taken,
            "messages_posted": self.messages_posted,
            "broker_connected": self.broker is not None,
            "broker_room_mode": self._broker_room_mode(),
            "last_error": self.last_error,
        }

    def _broker_room_mode(self) -> str | None:
        if self.broker is None:
            return None
        try:
            return self.broker.get_room_mode().get("mode")
        except (OSError, BrokerError):
            return None

    async def request_with_approval(
        self, *, provider: str, target: str, method: str, url: str,
        estimated_cost_usd: float, reason: str = "",
    ) -> dict | None:
        """Call broker.prepare_headers; if it raises approval_required,
        emit the whisper and return None. The caller should retry once
        an approval_granted whisper arrives (out of scope for v0 — the
        owner CLI flips it via record_approval and the agent retries
        on the next loop tick).
        """
        if self.broker is None:
            return None
        try:
            return self.broker.prepare_headers(
                provider=provider, target=target,
                method=method, url=url,
                trust="user", room_id=self.cfg.room_id,
                persona_pid=self.client.pid or "",
            )
        except BrokerError as e:
            if e.message == "approval_required":
                approval_id = e.data.get("approval_id") or ""
                cost = float(e.data.get("estimated_cost_usd",
                                        estimated_cost_usd))
                # Find the owner pid from the room state if we can.
                state = await self.client.get_room_state()
                owner_pid = state.get("owner_pid") or state.get("moderator_pid") or ""
                try:
                    await self.client.post_approval_request(
                        approval_id=approval_id,
                        tool=f"{provider}.{target}",
                        target_pid=owner_pid,
                        estimated_cost_usd=cost,
                        reason=reason,
                    )
                except StormError:
                    log.exception("post_approval_request failed")
                return None
            raise

    def _build_broker(self) -> BrokerClient | None:
        """Connect to the storm-broker if configured.

        If broker.required is True and the broker is unreachable, raise so
        the agent fails fast — credentials must NOT silently fall back to
        env-var/IMDS reads in that case (Stage 13 §13.4 broker invariant).
        """
        bcfg = self.cfg.broker
        if not bcfg.socket_path:
            return None
        c = BrokerClient(socket_path=bcfg.socket_path, timeout=bcfg.timeout_s)
        try:
            c.ping()
        except (OSError, BrokerError) as e:
            if bcfg.required:
                raise RuntimeError(
                    f"broker required but unreachable at {bcfg.socket_path}: {e}"
                ) from e
            log.warning("broker configured at %s but ping failed: %s",
                        bcfg.socket_path, e)
            return None
        log.info("connected to storm-broker at %s", bcfg.socket_path)
        return c

    async def run(self) -> int:
        invite = os.environ.get(self.cfg.invite_token_env)
        await self.client.start()
        if invite and not self.client.pid:
            try:
                await self.client.redeem_invite(invite, kind=self.cfg.invite_kind)
            except StormError as e:
                log.error("invite redemption failed: %s", e)
                return 1

        import time
        self.started_at = time.monotonic()

        # Wake when events arrive
        @self.client.on("org.agentstorming.message")
        async def _on_message(ev):
            self._wake.set()

        @self.client.on("org.agentstorming.go_speak_granted")
        async def _on_grant(ev):
            self._wake.set()

        # Stage-13 §Planning mode. We are the courier for the room's mode:
        # the broker verifies the server's signature itself and ignores our
        # opinion (ADR-008), so relaying every mode-bearing event we see is
        # both safe and the only way an out-of-process broker can learn that
        # the moderator promoted the room. Snapshots cover the case where we
        # joined after the promotion had already happened.
        if self.broker is not None:
            @self.client.on("org.agentstorming.mode_promoted")
            async def _on_mode_promoted(ev):
                self._relay_mode(ev)

            @self.client.on("org.agentstorming.metadata_snapshot")
            async def _on_snapshot(ev):
                self._relay_mode(ev)

        # Whether we've already done the one-shot moderator opening turn.
        opened_room = False
        try:
            while not self._stopping.is_set():
                events = await self.client.drain_buffer()
                state = await self.client.get_room_state()
                my_pid = self.client.pid

                # Moderator kick-off: if I'm the moderator and the room has
                # documents but no messages from anyone yet, deliberate once
                # so the brief lands in the channel without the human
                # having to nudge.
                if not opened_room and my_pid:
                    is_mod = state.get("moderator_pid") == my_pid
                    docs = state.get("documents") or []
                    has_any_message = any(
                        e.get("type") == "org.agentstorming.message" for e in events
                    )
                    if is_mod and docs and not has_any_message:
                        events_for_llm: list[dict] = []
                        decision = await self.backend.deliberate(events_for_llm, state)
                        opened_room = True
                        if decision.speak and decision.text:
                            try:
                                await self.client.post_message(decision.text)
                            except StormError:
                                log.exception("opening post failed")
                        continue
                    if is_mod and not docs:
                        # No brief to anchor on — skip kickoff, behave normally.
                        opened_room = True
                    elif not is_mod:
                        # Specialists never open the room.
                        opened_room = True

                if not events:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        continue
                    continue
                # Filter our own messages out to avoid self-reactions.
                events_for_llm = [e for e in events if e.get("sender") != my_pid]
                if not events_for_llm:
                    continue
                # Triage: cheap small-LLM pre-filter. Honours persona.triage.enabled.
                if not await maybe_triage(self.cfg, events_for_llm, state):
                    log.debug("triage: skipping deliberation")
                    continue
                decision = await self.backend.deliberate(events_for_llm, state)
                self.turns_taken += 1
                if not decision.speak or not decision.text:
                    continue
                # Raise-hand mode?
                cfg_snap = state.get("config", {}) or {}
                if cfg_snap.get("raise_hand_required"):
                    try:
                        await self.client.raise_hand(decision.hint or "")
                        # Await grant
                        await self._await_grant()
                    except StormError:
                        log.exception("raise hand failed")
                        continue
                try:
                    await self.client.post_message(decision.text)
                    self.messages_posted += 1
                except StormError as e:
                    self.last_error = str(e)
                    log.exception("post failed")
        except RestartRequested as e:
            log.info("restart requested: %s", e.reason)
            return 42
        except KeyboardInterrupt:
            pass
        except Exception as e:  # surfaced through status() for hosted runs
            self.last_error = f"{type(e).__name__}: {e}"
            raise
        finally:
            await self.client.stop()
        return 0

    def _relay_mode(self, envelope: dict) -> None:
        """Forward a mode-bearing signed event to the broker, best-effort."""
        if self.broker is None:
            return
        try:
            result = self.broker.maybe_relay_room_mode(envelope)
        except (OSError, BrokerError) as e:
            log.warning("relaying room mode to broker failed: %s", e)
            return
        if result:
            log.info("broker room mode is now %s", result.get("mode"))

    async def _await_grant(self, timeout: float = 300) -> None:
        """Poll the local metadata store for an active grant addressed to us."""
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            g = await self.client.metadata.active_grant_for(self.client.pid)
            if g:
                return
            await asyncio.sleep(0.5)
