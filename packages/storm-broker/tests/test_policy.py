# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Capability policy tests."""

from storm_broker.policy import (
    Capability, PolicyEngine, PolicyDecision, parse_capabilities, _parse_rate,
)


def _cap(**kw):
    base = dict(tool="aws.bedrock.invoke", args={}, rate_per_second=10.0,
                requires_trust="user", effects="read")
    base.update(kw)
    return Capability(**base)


def test_matches_basic():
    c = _cap()
    assert c.matches("aws.bedrock.invoke", {})
    assert not c.matches("aws.s3.get", {})


def test_matches_args_glob():
    c = _cap(args={"model": ["claude-sonnet-4-5*"]})
    assert c.matches("aws.bedrock.invoke", {"model": "claude-sonnet-4-5-20250929"})
    assert not c.matches("aws.bedrock.invoke", {"model": "gpt-4"})


def test_engine_no_match_denies():
    e = PolicyEngine([])
    d = e.evaluate("anything", {})
    assert not d.allow and d.reason == "no_matching_capability"


def test_engine_match_allows():
    e = PolicyEngine([_cap(tool="github.create_pr")])
    d = e.evaluate("github.create_pr", {})
    assert d.allow


def test_engine_trust_mismatch():
    e = PolicyEngine([_cap(tool="github.create_pr", requires_trust="user")])
    d = e.evaluate("github.create_pr", {}, trust="peer")
    assert not d.allow and d.reason == "trust_mismatch"


def test_engine_rate_limit():
    # A non-positive rate disables the capability entirely — not even one
    # burst call gets through.
    e = PolicyEngine([_cap(tool="x", rate_per_second=0.0)])
    d = e.evaluate("x", {})
    assert not d.allow and d.reason == "rate_limited"


def test_engine_rate_limit_allows_burst_then_throttles():
    """A positive rate permits an initial call and then throttles.

    Guards the bucket-seeding fix: the bucket starts full (capacity), not at
    `rate_per_second`, so a slow capability like `5/hour` is usable at once
    instead of being denied for its first ~12 minutes.
    """
    e = PolicyEngine([_cap(tool="slow", rate_per_second=5 / 3600.0)])
    assert e.evaluate("slow", {}).allow
    d = e.evaluate("slow", {})
    assert not d.allow and d.reason == "rate_limited"


def test_engine_budget_exceeded():
    e = PolicyEngine([_cap(tool="bedrock", budget_usd_per_day=1.0)])
    e.evaluate("bedrock", {}, estimated_cost_usd=0.5)
    d = e.evaluate("bedrock", {}, estimated_cost_usd=0.6)
    assert not d.allow and d.reason == "budget_exceeded"


def test_engine_approval_required():
    e = PolicyEngine([_cap(tool="github.create_pr", ask_owner_at_usd=0.0)])
    d = e.evaluate("github.create_pr", {}, estimated_cost_usd=10.0)
    assert not d.allow and d.reason == "approval_required" and d.approval_required


def test_monotonic_narrow():
    e = PolicyEngine([_cap(tool="a"), _cap(tool="b")])
    e.narrow(["a"])
    assert not e.evaluate("a", {}).allow
    assert e.evaluate("b", {}).allow


def test_parse_rate():
    assert _parse_rate("60/minute") == 1.0
    assert _parse_rate("60/second") == 60.0
    assert _parse_rate("3600/hour") == 1.0


def test_parse_capabilities_yaml_shape():
    items = [
        {"tool": "aws.bedrock.invoke", "rate": "60/minute",
         "args": {"model": ["sonnet*"]}, "budget_usd": "10/day",
         "requires_trust": "user"},
        {"tool": "github.create_pr", "rate": "5/hour",
         "ask_owner_at": 50, "requires_trust": "user", "effects": "write"},
    ]
    caps = parse_capabilities(items)
    assert len(caps) == 2
    assert caps[0].tool == "aws.bedrock.invoke"
    assert caps[0].args == {"model": ["sonnet*"]}
    assert caps[0].budget_usd_per_day == 10.0
    assert caps[1].ask_owner_at_usd == 50.0
