# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Audit chain integrity tests."""

import secrets
from pathlib import Path

from storm_broker.audit import AuditChain, AuditEntry, args_hash


def _entry(i: int) -> AuditEntry:
    return AuditEntry(
        event_id=f"event-{i}",
        persona_pid="pid",
        room_id="r",
        task_id=f"t-{i}",
        tool="aws.bedrock.invoke",
        args_hash=args_hash({"i": i}),
        decision="allow",
        reason="ok",
    )


def test_append_chains_correctly(tmp_path: Path):
    chain = AuditChain(tmp_path / "a.ndjson", secrets.token_bytes(32))
    e1 = chain.append(_entry(1))
    e2 = chain.append(_entry(2))
    assert e2.prev_hash == e1.entry_hash
    ok, n = chain.verify()
    assert ok and n == 2


def test_tampered_entry_fails_verify(tmp_path: Path):
    p = tmp_path / "a.ndjson"
    chain = AuditChain(p, secrets.token_bytes(32))
    chain.append(_entry(1))
    chain.append(_entry(2))
    # Tamper line 1 — change the reason field
    lines = p.read_text().splitlines()
    lines[0] = lines[0].replace('"reason":"ok"', '"reason":"OK"')
    p.write_text("\n".join(lines) + "\n")
    ok, n = chain.verify()
    assert not ok and n == 1


def test_chain_resumes_across_restart(tmp_path: Path):
    p = tmp_path / "a.ndjson"
    key = secrets.token_bytes(32)
    chain = AuditChain(p, key)
    chain.append(_entry(1))
    # Re-instantiate (simulates restart)
    chain2 = AuditChain(p, key)
    e2 = chain2.append(_entry(2))
    assert e2.prev_hash != ""
    ok, n = chain2.verify()
    assert ok and n == 2


def test_args_hash_stable():
    a = {"x": 1, "y": [1, 2]}
    b = {"y": [1, 2], "x": 1}
    assert args_hash(a) == args_hash(b)
