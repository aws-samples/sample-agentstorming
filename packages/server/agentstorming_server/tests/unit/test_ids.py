# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from agentstorming_server.domain.ids import fingerprint_pubkey, kid_from_pubkey, make_pid, ParticipantId


def test_fingerprint_len():
    fp = fingerprint_pubkey(b"\x00" * 32)
    assert len(fp) == 43


def test_kid_16():
    assert len(kid_from_pubkey(b"\x00" * 32)) == 16


def test_make_pid_and_parse():
    pub = b"\x01" * 32
    pid = make_pid(pub, "room-x")
    parsed = ParticipantId.parse(pid)
    assert parsed.room_id == "room-x"
    assert len(parsed.fingerprint) == 43


def test_pubkey_wrong_size_rejected():
    import pytest
    with pytest.raises(ValueError):
        fingerprint_pubkey(b"\x00" * 31)
