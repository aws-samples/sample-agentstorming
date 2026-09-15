# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from agentstorming_server.domain.participant import Participant, affiliation_power_level
from datetime import datetime, timezone


def _p(affiliation, rank=None):
    return Participant(
        room_id="r", pid="p", pubkey=b"", affiliation=affiliation, deputy_rank=rank,
        joined_at=datetime.now(timezone.utc),
    )


def test_hierarchy():
    assert _p("room-owner").power_level == 100
    assert _p("original-moderator").power_level == 90
    assert _p("member", rank=1).power_level == 79
    assert _p("member", rank=5).power_level == 75
    assert _p("member").power_level == 50
    assert _p("ejected").power_level == -1
    assert _p("penned").power_level == -10


def test_affiliation_power_function():
    assert affiliation_power_level("room-owner", None) > affiliation_power_level("original-moderator", None)
    assert affiliation_power_level("original-moderator", None) > affiliation_power_level("member", 1)
    assert affiliation_power_level("member", 1) > affiliation_power_level("member", 10)
