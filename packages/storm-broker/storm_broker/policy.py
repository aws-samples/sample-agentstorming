# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Capability policy — TOML rules + monotonic narrowing.

The broker's per-tool authorization layer. A persona's policy is a
list of allowed tool invocations, each with constraints on args,
rate, budget, trust level. Capability sets are MONOTONIC at runtime
— they may only narrow (Progent-style verified narrowing,
arXiv:2504.11703).

A jailbroken persona cannot expand its own policy mid-session.
Expansion requires an owner-signed event.
"""

from __future__ import annotations

import fnmatch
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Capability:
    tool: str                     # e.g. "aws.bedrock.invoke"
    args: dict[str, list[str]] = field(default_factory=dict)
    # rate is "<n>/<window>" — n calls per window in seconds-equivalent.
    rate_per_second: float = 1.0
    budget_usd_per_day: float | None = None
    requires_trust: str = "user"  # "user" | "peer" | "retrieval" | "system" | "any"
    effects: str = "read"         # "read" | "write" | "admin"
    ask_owner_at_usd: float | None = None  # HITL trigger

    def matches(self, tool: str, args: dict[str, Any]) -> bool:
        if tool != self.tool:
            return False
        for key, allowed in self.args.items():
            if key not in args:
                return False
            val = args[key]
            if not isinstance(val, str):
                val = str(val)
            if not any(fnmatch.fnmatchcase(val, pat) for pat in allowed):
                return False
        return True


@dataclass
class _RateBucket:
    capacity: float
    rate_per_second: float
    tokens: float
    last_ts: float

    def try_consume(self, n: float = 1.0, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        elapsed = now - self.last_ts
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_second)
        self.last_ts = now
        if self.tokens < n:
            return False
        self.tokens -= n
        return True


class PolicyEngine:
    """Holds a list of capabilities for a persona and enforces them.

    Methods are SAFE to call from a single-threaded asyncio event
    loop. Multi-threaded use needs an external lock.
    """

    def __init__(
        self, capabilities: list[Capability], room_mode: str = "active",
    ) -> None:
        self._caps = list(capabilities)
        self._buckets: dict[str, _RateBucket] = {}
        self._spent_usd_today: dict[str, float] = {}
        self._spent_day_start: float = self._today()
        # Stage-13 §Planning mode. While "planning", every capability
        # declaring effects write/admin is denied here regardless of rate,
        # budget, or trust. Deliberately defaults to whatever the operator
        # configured rather than to "active", so a broker that never
        # receives a verifiable promotion stays closed.
        self._room_mode = room_mode if room_mode in ("planning", "active") else "active"

    @property
    def room_mode(self) -> str:
        return self._room_mode

    def set_room_mode(self, mode: str) -> None:
        """Set the deliberation mode.

        MUST only be called by the broker server after it has verified a
        server-signed ``org.agentstorming.mode_promoted`` envelope. A
        jailbroken agent asking for "active" is not sufficient authority —
        see BrokerServer.set_room_mode.
        """
        if mode not in ("planning", "active"):
            raise ValueError(f"invalid room mode: {mode!r}")
        self._room_mode = mode

    def narrow(self, removed_tools: list[str]) -> None:
        """Remove capabilities by tool name. Monotonic — only narrows.

        Used when the moderator pens a persona or when a peer-attack
        triggers automatic capability stripping.
        """
        self._caps = [c for c in self._caps if c.tool not in removed_tools]

    def evaluate(
        self,
        tool: str,
        args: dict[str, Any],
        trust: str = "user",
        estimated_cost_usd: float = 0.0,
    ) -> "PolicyDecision":
        # Find first matching capability (rules are ordered by priority).
        for cap in self._caps:
            if cap.matches(tool, args):
                return self._evaluate_against_cap(cap, tool, args, trust, estimated_cost_usd)
        return PolicyDecision(allow=False, reason="no_matching_capability", cap=None)

    def _evaluate_against_cap(
        self, cap: Capability, tool: str, args: dict[str, Any], trust: str, cost: float
    ) -> "PolicyDecision":
        # 0. Planning mode (Stage-13). A room still in planning may read
        # but not act; this is the control that would have stopped the
        # Replit SaaStr class of incident, so it is checked before every
        # other gate and is not overridable by trust level or budget.
        if self._room_mode == "planning" and cap.effects in ("write", "admin"):
            return PolicyDecision(allow=False, reason="planning_mode", cap=cap)
        # 1. Trust-level check.
        if cap.requires_trust != "any" and trust != cap.requires_trust:
            return PolicyDecision(allow=False, reason="trust_mismatch", cap=cap)
        # 2. Rate limit. A non-positive rate means the capability is
        # disabled outright ("0/hour" = never), so short-circuit before the
        # bucket: a full initial bucket would otherwise let exactly one call
        # through something the operator wrote in order to forbid.
        if cap.rate_per_second <= 0:
            return PolicyDecision(allow=False, reason="rate_limited", cap=cap)
        bucket = self._buckets.get(tool)
        if bucket is None:
            capacity = max(1.0, cap.rate_per_second)
            bucket = _RateBucket(
                capacity=capacity,
                rate_per_second=cap.rate_per_second,
                # Start FULL, not at `rate_per_second`. Seeding with the
                # rate meant any capability slower than 1/second began with
                # a fraction of a token and had its very first call denied:
                # `rate: 5/hour` is 0.00139/s, so the first github.create_pr
                # was rate_limited for ~12 minutes after startup. A token
                # bucket is supposed to permit an initial burst up to
                # capacity and then throttle to the refill rate.
                tokens=capacity,
                last_ts=time.monotonic(),
            )
            self._buckets[tool] = bucket
        if not bucket.try_consume(1.0):
            return PolicyDecision(allow=False, reason="rate_limited", cap=cap)
        # 3. Budget cap (per day).
        self._maybe_reset_day()
        if cap.budget_usd_per_day is not None:
            spent = self._spent_usd_today.get(tool, 0.0)
            if spent + cost > cap.budget_usd_per_day:
                return PolicyDecision(allow=False, reason="budget_exceeded", cap=cap)
        # 4. HITL approval threshold.
        if cap.ask_owner_at_usd is not None and cost >= cap.ask_owner_at_usd:
            return PolicyDecision(
                allow=False, reason="approval_required", cap=cap,
                approval_required=True, estimated_cost=cost,
            )
        # All clear.
        if cap.budget_usd_per_day is not None:
            self._spent_usd_today[tool] = self._spent_usd_today.get(tool, 0.0) + cost
        return PolicyDecision(allow=True, reason="ok", cap=cap)

    def _maybe_reset_day(self) -> None:
        if self._today() > self._spent_day_start:
            self._spent_usd_today.clear()
            self._spent_day_start = self._today()

    @staticmethod
    def _today() -> float:
        return time.time() // 86400


@dataclass
class PolicyDecision:
    allow: bool
    reason: str
    cap: Capability | None
    approval_required: bool = False
    estimated_cost: float = 0.0


def parse_capabilities(items: list[dict[str, Any]]) -> list[Capability]:
    """Convert a YAML/TOML capability block into Capability objects."""
    out: list[Capability] = []
    for item in items:
        rate_str = item.get("rate", "60/minute")
        rate_per_second = _parse_rate(rate_str)
        out.append(
            Capability(
                tool=item["tool"],
                args=item.get("args", {}),
                rate_per_second=rate_per_second,
                budget_usd_per_day=_parse_budget(item.get("budget_usd")),
                requires_trust=item.get("requires_trust", "user"),
                effects=item.get("effects", "read"),
                ask_owner_at_usd=_parse_money(item.get("ask_owner_at")),
            )
        )
    return out


def _parse_rate(s: str) -> float:
    n_str, _, window = s.partition("/")
    n = float(n_str)
    if window == "second":
        return n
    if window == "minute":
        return n / 60.0
    if window == "hour":
        return n / 3600.0
    if window == "day":
        return n / 86400.0
    raise ValueError(f"invalid rate: {s!r}")


def _parse_budget(s: str | int | float | None) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    # "10/day" or "10"
    if "/" in s:
        amount, _, _ = s.partition("/")
        return float(amount)
    return float(s)


def _parse_money(s: str | int | float | None) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = s.strip().lstrip("$")
    if s.endswith("_usd"):
        s = s[:-4]
    return float(s)
