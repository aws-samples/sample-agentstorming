# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Planning mode denies write-effect capabilities at the policy engine.

Stage-13 §Planning mode: "In `planning` mode: all tool calls with
`effects: write` SHALL be denied at the broker. Read-only tool calls
(`effects: read`) are permitted."
"""

from __future__ import annotations

import pytest

from storm_broker.policy import PolicyEngine, parse_capabilities

CAPS = [
    {"tool": "github.create_pr", "effects": "write", "rate": "5/hour"},
    {"tool": "github.read_issue", "effects": "read", "rate": "60/minute"},
    {"tool": "aws.iam.put_policy", "effects": "admin", "rate": "5/hour"},
]


def _engine(mode: str) -> PolicyEngine:
    return PolicyEngine(parse_capabilities(CAPS), room_mode=mode)


def test_planning_denies_write():
    d = _engine("planning").evaluate("github.create_pr", {})
    assert not d.allow
    assert d.reason == "planning_mode"


def test_planning_denies_admin():
    d = _engine("planning").evaluate("aws.iam.put_policy", {})
    assert not d.allow
    assert d.reason == "planning_mode"


def test_planning_permits_read():
    d = _engine("planning").evaluate("github.read_issue", {})
    assert d.allow, d.reason


def test_active_permits_write():
    d = _engine("active").evaluate("github.create_pr", {})
    assert d.allow, d.reason


def test_promotion_unblocks_writes():
    e = _engine("planning")
    assert e.evaluate("github.create_pr", {}).reason == "planning_mode"
    e.set_room_mode("active")
    assert e.evaluate("github.create_pr", {}).allow


def test_default_is_active_for_back_compat():
    assert PolicyEngine(parse_capabilities(CAPS)).room_mode == "active"


def test_unknown_mode_falls_back_to_active_at_construction():
    """A typo in config must not silently disable every write tool."""
    assert PolicyEngine(parse_capabilities(CAPS), room_mode="plannning").room_mode == "active"


def test_set_room_mode_rejects_garbage():
    e = _engine("planning")
    with pytest.raises(ValueError):
        e.set_room_mode("whatever")
    assert e.room_mode == "planning"


def test_planning_mode_outranks_trust_and_budget():
    """The mode gate is checked before trust, rate, and budget."""
    caps = parse_capabilities([
        {"tool": "slack.post", "effects": "write", "rate": "100/minute",
         "requires_trust": "any", "budget_usd": "1000/day"},
    ])
    e = PolicyEngine(caps, room_mode="planning")
    d = e.evaluate("slack.post", {}, trust="user", estimated_cost_usd=0.0)
    assert d.reason == "planning_mode"


def test_low_rate_capability_allows_its_first_call():
    """A `5/hour` tool must not be rate-limited on call one.

    The bucket used to be seeded with `rate_per_second` (0.00139 for
    5/hour), so the first call found a near-empty bucket and was refused
    for ~12 minutes after startup.
    """
    e = _engine("active")
    assert e.evaluate("github.create_pr", {}).allow
