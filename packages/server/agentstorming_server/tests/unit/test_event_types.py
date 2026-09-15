# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from agentstorming_server.domain.event import (
    PARTICIPANT_POSTED_TYPES,
    SYSTEM_POSTED_TYPES,
    TYPE_GO_SPEAK_GRANTED,
    TYPE_HAND_RAISED,
    TYPE_MESSAGE,
)


def test_disjoint_sets():
    assert PARTICIPANT_POSTED_TYPES & SYSTEM_POSTED_TYPES == frozenset()


def test_participant_contains_message_and_hand():
    assert TYPE_MESSAGE in PARTICIPANT_POSTED_TYPES
    assert TYPE_HAND_RAISED in PARTICIPANT_POSTED_TYPES


def test_system_contains_grant():
    assert TYPE_GO_SPEAK_GRANTED in SYSTEM_POSTED_TYPES
