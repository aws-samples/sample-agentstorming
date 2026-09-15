# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import pytest

from agentstorming_client.metadata import MetadataStore


@pytest.mark.asyncio
async def test_apply_snapshot_then_incremental():
    m = MetadataStore()
    snap = {
        "type": "org.agentstorming.metadata_snapshot",
        "payload": {
            "room_id": "r", "room_state": "ACTIVE",
            "participants": [{"pid": "a", "pubkey": "x", "affiliation": "member"}],
            "moderator_pid": None, "raised_hands": [], "active_grant": None,
        },
    }
    await m.apply(snap)
    await m.apply({"type": "org.agentstorming.participant_joined", "payload": {"pid": "b", "pubkey": "y"}})
    state = await m.snapshot()
    assert {p["pid"] for p in state["participants"]} == {"a", "b"}


@pytest.mark.asyncio
async def test_hand_raised_then_granted():
    m = MetadataStore()
    await m.apply({"type": "org.agentstorming.metadata_snapshot", "payload": {"participants": [], "raised_hands": [], "active_grant": None}})
    await m.apply({"type": "org.agentstorming.hand_raised", "sender": "a", "ts_sender": "t", "payload": {"hand_id": "H1"}})
    state = await m.snapshot()
    assert any(h["hand_id"] == "H1" for h in state["raised_hands"])
    await m.apply({"type": "org.agentstorming.go_speak_granted", "payload": {"hand_id": "H1", "grant_id": "G1", "pid": "a", "ttl_expires_at": "t+300"}})
    state = await m.snapshot()
    assert state["active_grant"]["grant_id"] == "G1"
    assert all(h["hand_id"] != "H1" for h in state["raised_hands"])
