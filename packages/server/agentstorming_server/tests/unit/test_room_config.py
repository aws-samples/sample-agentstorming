# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from agentstorming_server.domain.room import RoomConfig


def test_roundtrip_defaults():
    cfg = RoomConfig()
    j = cfg.to_json()
    cfg2 = RoomConfig.from_json(j)
    assert cfg2.to_json() == j


def test_raise_hand_toggle():
    cfg = RoomConfig(raise_hand_required=True, go_speak_ttl_seconds=120)
    j = cfg.to_json()
    assert j["raise_hand_required"] is True
    assert j["go_speak_ttl_seconds"] == 120
